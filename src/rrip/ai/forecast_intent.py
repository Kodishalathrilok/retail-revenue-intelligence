"""Turning a predictive question into structured forecast parameters.

    "What is next week's expected Grocery revenue?"
        -> {"intent": "forecast", "department": "GROCERY", "horizon": 1}
        -> rrip.forecast.service.forecast(...)
        -> a number computed by a scored predictor

THE DIVISION OF LABOUR, AND WHY IT IS DRAWN HERE

This project's rule is that the model interprets and deterministic systems
calculate. A forecast is the sharpest case for it: an LLM asked for next week's
Grocery revenue will produce a confident, well-formatted, entirely invented
figure, and nothing downstream can tell it from a real one.

So the model's maximum permitted contribution on this path is choosing a
department name from a closed list. Everything else -- the horizon, the target,
the interval, the confidence -- comes from rrip.forecast.

EXTRACTION IS DETERMINISTIC FIRST, AND USUALLY LAST

resolve() matches department names and horizons with string rules and no
network call. It handles the overwhelming majority of real phrasings, and it is
testable without a provider. The LLM fallback is only reached when the
deterministic pass finds no department, and even then its output is validated
against the department list before it is used -- a name outside that list is
discarded, exactly as rrip.ai.causal discards proposed confounders that are not
in the schema.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from rrip.forecast.contract import HORIZON_WEEKS, SUPPORTED_HORIZONS

# Words a user might use for a department that is not spelled the way the
# source spells it. dunnhumby's department names are terse and abbreviated --
# 'DRUG GM', 'MEAT-PCKGD', 'KIOSK-GAS' -- so exact matching alone would fail on
# entirely reasonable phrasings.
ALIASES: dict[str, str] = {
    "groceries": "GROCERY",
    "grocery": "GROCERY",
    "drug": "DRUG GM",
    "drugs": "DRUG GM",
    "pharmacy": "DRUG GM",
    "general merchandise": "DRUG GM",
    "produce": "PRODUCE",
    "fruit and veg": "PRODUCE",
    "fruit and vegetables": "PRODUCE",
    "vegetables": "PRODUCE",
    "meat": "MEAT",
    "packaged meat": "MEAT-PCKGD",
    "fuel": "KIOSK-GAS",
    "petrol": "KIOSK-GAS",
    "gas": "KIOSK-GAS",
    "kiosk": "KIOSK-GAS",
    "deli": "DELI",
    "delicatessen": "DELI",
    "bakery": "PASTRY",
    "pastry": "PASTRY",
    "seafood": "SEAFOOD",
    "fish": "SEAFOOD",
    "packaged seafood": "SEAFOOD-PCKGD",
    "flowers": "FLORAL",
    "floral": "FLORAL",
    "cosmetics": "COSMETICS",
    "makeup": "COSMETICS",
    "beauty": "COSMETICS",
    "nutrition": "NUTRITION",
    "supplements": "NUTRITION",
    "vitamins": "NUTRITION",
    "spirits": "SPIRITS",
    "alcohol": "SPIRITS",
    "liquor": "SPIRITS",
    "salad bar": "SALAD BAR",
    "garden": "GARDEN CENTER",
    "garden centre": "GARDEN CENTER",
    "restaurant": "RESTAURANT",
}

# "in 3 weeks", "3 weeks ahead", "3 weeks out"
_HORIZON_RE = re.compile(
    r"\b(?:in|over the next|next)?\s*(\d+)\s*weeks?\s*(?:ahead|out|from now|time)?\b")

_NEXT_WEEK_RE = re.compile(r"\bnext week\b|\bcoming week\b|\bupcoming week\b|"
                           r"\bweek ahead\b")


@dataclass
class ForecastIntent:
    """Structured parameters. Carries no figures -- only what to ask for."""

    intent: str = "forecast"
    department: str | None = None
    horizon: int = HORIZON_WEEKS
    resolved_by: str = "deterministic"
    matched_text: str | None = None
    candidates: list[str] = field(default_factory=list)
    # True when the question named a department the forecaster does not model,
    # as distinct from naming none at all. The two need different replies.
    unknown_department: str | None = None

    @property
    def is_complete(self) -> bool:
        return self.department is not None

    def to_dict(self) -> dict:
        return {"intent": self.intent, "department": self.department,
                "horizon": self.horizon, "resolved_by": self.resolved_by,
                "matched_text": self.matched_text,
                "unknown_department": self.unknown_department,
                "supported_horizons": list(SUPPORTED_HORIZONS)}


def _normalise(text: str) -> str:
    return " " + re.sub(r"[^a-z0-9 &/.-]+", " ", text.lower()).strip() + " "


def _mentions(haystack: str, needle: str) -> bool:
    """Whole-token containment that tolerates adjacent punctuation.

    Plain `" name " in text` is not usable here, because several department
    names legitimately contain the characters that also end a sentence:
    `MISC. TRANS.` and `MEAT-PCKGD` and `COUP/STR & MFG`. Stripping punctuation
    would destroy the names; requiring surrounding spaces means
    "Forecast revenue for MEAT-PCKGD." fails to match, which it did.

    This is the same failure the router carries a regression test for -- a
    trailing full stop silently defeating a trigger, and the question getting a
    confident answer to something it did not ask.

    The boundary is "not a letter or digit", so `meat` still matches inside
    `meat-pckgd`. Callers rely on longest-match-first ordering to resolve that.
    """
    return re.search(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])",
                     haystack) is not None


def extract_horizon(question: str) -> int:
    """Weeks ahead, defaulting to 1.

    An explicit "in 3 weeks" is returned as 3 even though the service will
    refuse it. Silently clamping to the supported horizon would answer a
    different question than the one asked and label it as the answer to this
    one -- the refusal is the correct behaviour and it belongs to the service.
    """
    q = _normalise(question)
    if _NEXT_WEEK_RE.search(q):
        return 1
    m = _HORIZON_RE.search(q)
    if m:
        try:
            n = int(m.group(1))
        except ValueError:
            return HORIZON_WEEKS
        return n if n > 0 else HORIZON_WEEKS
    return HORIZON_WEEKS


def resolve(question: str, departments: list[str]) -> ForecastIntent:
    """Deterministic extraction. No model call.

    LONGEST MATCH WINS, ACROSS BOTH TABLES AT ONCE. Source names and aliases
    are ranked together rather than source names first, and the reason is a bug
    this had: "how much will packaged meat make next week" resolved to MEAT,
    because the exact-name pass matched the substring 'meat' and returned
    before the alias 'packaged meat' was ever considered. The two departments
    are different series with different forecasts, and nothing in the answer
    would have looked wrong.
    """
    q = _normalise(question)
    intent = ForecastIntent(horizon=extract_horizon(question),
                            candidates=sorted(departments))

    known = {d.upper() for d in departments}

    candidates: list[tuple[str, str]] = [(d.lower(), d) for d in departments]
    candidates += [(alias, target) for alias, target in ALIASES.items()]
    candidates.sort(key=lambda t: len(t[0]), reverse=True)

    for text, target in candidates:
        if not _mentions(q, text):
            continue
        if target in known:
            intent.department = next(d for d in departments if d == target)
            intent.matched_text = text
        else:
            # A real dunnhumby department that this model does not cover. That
            # is a different answer from "no department named", and the caller
            # can say which.
            intent.unknown_department = target
            intent.matched_text = text
        return intent

    return intent


PROMPT = """\
You map a retail question to ONE department name from a fixed list.

Rules:
- Reply with EXACTLY one name from the list, copied character for character.
- If no department in the list is clearly the subject, reply with: NONE
- Do not explain. Do not add punctuation. Do not invent a name.
- You are NOT being asked to forecast anything. Do not produce any number.

List:
{departments}

Question: {question}

Answer:"""


async def resolve_with_llm(question: str, departments: list[str],
                           provider) -> ForecastIntent:
    """Fallback for phrasings the rules miss. Output is validated, not trusted.

    The model picks a label from a closed list; if it returns anything else --
    a department that does not exist, an explanation, a number -- the result is
    discarded and the intent stays incomplete. That is the same treatment
    rrip.ai.causal gives proposed confounders, and for the same reason: a
    constrained generation is still a generation.
    """
    intent = resolve(question, departments)
    if intent.is_complete or intent.unknown_department:
        return intent

    response = await provider.complete(
        PROMPT.format(departments="\n".join(sorted(departments)),
                      question=question),
        system="You select one label from a list. You never compute values.")

    candidate = (response.text or "").strip().strip(".\"'").upper()
    if candidate and candidate != "NONE" and candidate in {d.upper()
                                                           for d in departments}:
        intent.department = next(d for d in departments
                                 if d.upper() == candidate)
        intent.resolved_by = "llm"
        intent.matched_text = candidate
    return intent
