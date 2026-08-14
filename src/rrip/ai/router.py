"""Answerability routing: decide what kind of question this is before spending a
model call on it.

WHAT PROBLEM THIS SOLVES, MEASURED

The first full benchmark run scored `unanswerable` at 25% (1 of 4). For three of
four questions the schema holds no data for -- sentiment, email addresses,
advertising spend -- the system generated SQL, executed it, and returned rows.
That is the worst available failure: not an error, an answer. A user cannot tell
it apart from a real one.

The model was not being careless. Nothing had told it those subjects were
absent, and a language model asked for advertising spend against a schema with a
`coupon_disc` column will find something plausible to sum.

WHY DETERMINISTIC AND NOT ANOTHER MODEL CALL

Asking an LLM "can you answer this?" makes the untrusted component the judge of
its own competence, and costs a second call on the latency path. What the
question is *about* is decidable from vocabulary, and the vocabulary is already
written down in rrip.semantic. So this is a table lookup, and its rules are
inspectable and testable without a network.

WHAT THIS IS NOT

It is NOT a security control. UNSAFE here saves a model call and gives the user
a straight answer; it is not what stops harm. Harm is stopped by the gates in
rrip.ai.nl2sql and, behind them, by the read-only role. A question this router
waves through is no less guarded than before.

It is also NOT a cost oracle. TOO_EXPENSIVE fires only when the question asks
for unboundedness in words. The authority on query cost is gate 4 (EXPLAIN) and
gate 5 (statement timeout), which see actual SQL.

THE FAILURE MODE TO WATCH

Every rule here can fire on a question it should not, and a false UNSUPPORTED is
a refusal to answer something answerable -- silently narrowing the system. That
rate is measured, not assumed: `rrip eval` reports router false positives
against the benchmark's ANSWERABLE cases, and tests/test_router.py asserts that
every case carrying a reference query routes ANSWERABLE.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from rrip.semantic import (
    ABSENT_DOMAINS,
    AMBIGUITY_RULES,
    FORECAST_SCOPE_REASON,
    FORECAST_TRIGGERS,
    forecastable_metric_terms,
    unforecastable_metric_terms,
)

ANSWERABLE = "ANSWERABLE"
AMBIGUOUS = "AMBIGUOUS"
UNSUPPORTED = "UNSUPPORTED"
UNSAFE = "UNSAFE"
TOO_EXPENSIVE = "TOO_EXPENSIVE"

# A predictive question, to be answered by rrip.forecast rather than by SQL.
#
# This verdict is what stops the worst failure available on this path. Asked
# "what is next week's expected Grocery revenue?", a model given a schema and
# no instruction otherwise writes a perfectly valid aggregate over historical
# rows and returns a number. It executes, it passes every SQL gate, and it is
# not a forecast. Routing the question away from SQL generation entirely is the
# only intervention that reliably prevents it, and it costs no model call.
FORECAST = "FORECAST"

# Mutation intent, matched as SHAPES rather than bare verbs.
#
# A bare-keyword list cannot be used here. "Which departments show the biggest
# drop in revenue?" is a routine retention question and contains "drop";
# "delete" appears in "deleted line items". Requiring the verb to govern a
# data-shaped object is what separates an instruction from a noun.
UNSAFE_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\b(delete|remove|drop|truncate|wipe|erase|purge|clear)\s+"
     r"(the\s+|all\s+|every\s+|each\s+|any\s+)?\w*\s*"
     r"(table|tables|row|rows|record|records|data|database|entry|entries|"
     r"transaction|transactions)\b",
     "asks for data to be deleted"),
    (r"\bdrop\s+(table|schema|database)\b", "asks for an object to be dropped"),
    (r"\b(update|set|change|modify|overwrite|reset)\s+(all\s+|every\s+)?"
     r"[\w.]+\s+to\s+\S", "asks for values to be overwritten"),
    (r"\b(insert|add)\s+(a\s+|new\s+)?(row|rows|record|records)\b",
     "asks for rows to be written"),
    (r"\bgrant\b[^.]{0,40}\b(superuser|admin|privilege|permission|access)\b",
     "asks for privileges to be granted"),
    (r"\b(password|passwords)\b[^.]{0,20}\b(hash|hashes)\b|\bpg_shadow\b|"
     r"\bpg_authid\b", "asks for credential material"),
    (r"/etc/passwd|\bpg_read_file\b|\bpg_ls_dir\b|\blo_import\b",
     "asks for server filesystem access"),
    (r"\bignore\s+(your\s+|all\s+)?(previous|prior|earlier)\s+instructions?\b|"
     r"\bmaintenance mode\b|\byou are now\b[^.]{0,40}\bmode\b",
     "attempts to override the system instructions"),
)

# Unboundedness asked for in words. Narrow on purpose -- see the module note.
EXPENSIVE_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\bcross\s+join\b", "asks for a cross join"),
    (r"\bevery\s+(single\s+)?(transaction|row|record)\b[^.]{0,40}\b"
     r"(with\s+all|all\s+its|every\s+column)\b",
     "asks for every row with every column"),
    (r"\bevery\s+\w+\s+with\s+every\s+other\b", "asks for a self-pairing of a fact table"),
    (r"\b(all|every)\s+2[.,]?6\s*(million|m)\b", "asks for the whole fact table"),
)


@dataclass
class Routing:
    """The routing decision, with the evidence that produced it."""

    verdict: str
    reason: str
    clarification: str | None = None
    # Which rule fired, so a wrong decision is traceable to one line of the
    # semantic layer rather than to "the router".
    rule: str | None = None
    matched: list[str] = field(default_factory=list)
    duration_ms: float = 0.0

    @property
    def should_generate_sql(self) -> bool:
        return self.verdict == ANSWERABLE

    @property
    def should_forecast(self) -> bool:
        return self.verdict == FORECAST

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "reason": self.reason,
            "clarification": self.clarification,
            "rule": self.rule,
            "matched": self.matched,
            "duration_ms": round(self.duration_ms, 3),
        }


def _normalise(question: str) -> str:
    """Lowercase and collapse whitespace and punctuation to spaces.

    Padded with spaces so a phrase trigger can be matched with plain substring
    containment at word boundaries without every trigger needing its own regex.

    A decimal point is protected before punctuation is stripped, but sentence
    punctuation is not. Keeping '.' wholesale broke exactly one case and did it
    silently: "Show me sales last month." normalised with the full stop still
    attached, so " last month " did not match and an ambiguous question routed
    ANSWERABLE. Slashes and underscores survive because /etc/passwd and
    pg_read_file are single tokens that unsafe rules match on.
    """
    s = question.lower()
    s = re.sub(r"(?<=\d)[.,](?=\d)", "\x01", s)      # protect 2.6 and 1,000
    s = re.sub(r"[^a-z0-9/_\x01]+", " ", s)
    s = s.replace("\x01", ".")
    return " " + s.strip() + " "


def _stem(word: str) -> str:
    """Strip a common inflectional suffix. Deliberately crude.

    A held-out question set caught the reason this is needed: "What motivated
    households to buy organic produce?" routed ANSWERABLE because the trigger
    was "motivation", and "Do sales peak on Saturdays?" because the trigger was
    "saturday". Listing every inflection of every trigger is the alternative,
    and it fails again on the next word nobody thought of.

    Both the question and the trigger go through this, so the only requirement
    is that it is consistent -- not that it produces real word stems. "sales"
    becoming "sal" is fine because the trigger becomes "sal" too. The guard
    against over-stemming is tests/test_router.py, which fails if any benchmark
    case with a reference answer gets blocked.
    """
    if len(word) <= 3:
        return word
    for suffix in ("ing", "ion", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)]
    return word


def _stem_phrase(text: str) -> str:
    return " " + " ".join(_stem(w) for w in text.split()) + " "


def _contains_phrase(haystack: str, phrase: str) -> bool:
    """Match a trigger phrase, tolerating inflection on either side."""
    if f" {phrase.lower().strip()} " in haystack:
        return True
    return _stem_phrase(phrase.lower()) in _stem_phrase(haystack)


def classify(question: str) -> Routing:
    """Route a question.

    Precedence: unsafe, unsupported, FORECAST, expensive, ambiguous.

    Unsafe first because a destructive request should never be treated as merely
    ambiguous. Unsupported before ambiguous because "which customers are the
    happiest" is not an under-specified question -- clarifying it cannot help,
    the data does not exist.

    FORECAST sits after unsupported and before ambiguous, and both placements
    are deliberate:

      * AFTER unsupported, so "will customer satisfaction improve next week?"
        is refused for the reason that actually applies -- there is no
        satisfaction data -- rather than being dispatched to a forecaster that
        would then have to refuse it a second time.
      * BEFORE ambiguous, because a predictive question is not under-specified.
        The forecaster has exactly one target, so there is no choice for the
        user to make and a clarification prompt would be asking about a
        decision that does not exist.
    """
    t0 = time.perf_counter()
    q = _normalise(question)

    def done(r: Routing) -> Routing:
        r.duration_ms = (time.perf_counter() - t0) * 1000
        return r

    if not question.strip():
        return done(Routing(UNSUPPORTED, "empty question", rule="empty"))

    for pattern, why in UNSAFE_PATTERNS:
        m = re.search(pattern, q, re.IGNORECASE)
        if m:
            return done(Routing(
                UNSAFE,
                f"This question {why}. This system is read-only: it answers "
                "questions about the data and cannot modify it.",
                clarification=("If you want to measure something instead, name "
                               "the metric -- for example revenue, baskets or "
                               "active households."),
                rule=why, matched=[m.group(0).strip()]))

    for domain in ABSENT_DOMAINS:
        hits = [t for t in domain.triggers if _contains_phrase(q, t)]
        if hits:
            return done(Routing(
                UNSUPPORTED, domain.reason,
                clarification=("This dataset covers transactions, promotions, "
                               "coupons and coarse household demographics. Ask "
                               "about one of those and it can be answered."),
                rule=domain.name, matched=hits))

    forecast_hits = [t for t in FORECAST_TRIGGERS if _contains_phrase(q, t)]
    if forecast_hits:
        wanted = [t for t in forecastable_metric_terms() if _contains_phrase(q, t)]
        other = [t for t in unforecastable_metric_terms() if _contains_phrase(q, t)]

        # A predictive question about a metric that was never modelled is
        # refused, not approximated. There is no measured error for units or
        # baskets, so any figure would carry a confidence it has not earned.
        if other and not wanted:
            return done(Routing(
                UNSUPPORTED, FORECAST_SCOPE_REASON,
                clarification=("Ask about forecast revenue for a department -- "
                               "for example, \"what is next week's expected "
                               "Grocery revenue?\""),
                rule="forecast_scope", matched=sorted(set(forecast_hits + other))))

        return done(Routing(
            FORECAST,
            ("This is a predictive question. It is answered by the forecasting "
             "model rather than by SQL, because the answer has not happened "
             "yet and cannot be queried from the transaction history."),
            rule="forecast_intent", matched=forecast_hits))

    for pattern, why in EXPENSIVE_PATTERNS:
        m = re.search(pattern, q, re.IGNORECASE)
        if m:
            return done(Routing(
                TOO_EXPENSIVE,
                f"This question {why}, which would scan far more data than an "
                "interactive answer allows.",
                clarification=("Narrow it -- aggregate it, filter to a week "
                               "range, or ask for a top-N instead."),
                rule=why, matched=[m.group(0).strip()]))

    for rule in AMBIGUITY_RULES:
        hits = [t for t in rule.triggers if _contains_phrase(q, t)]
        if not hits:
            continue
        # A superlative that names its measure is not ambiguous. "Top 5
        # departments by revenue" resolves itself, and treating it as ambiguous
        # would refuse the single most common analytical question there is.
        if any(_contains_phrase(q, r) for r in rule.resolved_by):
            continue
        return done(Routing(
            AMBIGUOUS, rule.reason, clarification=rule.clarification,
            rule=rule.name, matched=hits))

    return done(Routing(ANSWERABLE, "no missing data or unresolved choice detected"))
