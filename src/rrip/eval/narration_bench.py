"""Narration faithfulness benchmark: raw rows vs derived metrics, same cases.

WHAT THIS ANSWERS

Does handing the model deterministically derived fields, instead of raw rows,
actually reduce unsupported numerical claims -- or does it just move where the
rejection happens? Both paths run every case against the same live model. The
only difference is the payload.

NO LLM JUDGE

Grading is mechanical. Every assertion comes from the case, written before any
model call (see rrip.eval.narration_cases). A second model deciding whether the
first model was honest would be the same untrusted component marking its own
work.

THE TRUTH SET, AND WHY IT IS THE STRUCTURED PAYLOAD FOR BOTH PATHS

To judge whether a number is invented or merely absent, something has to define
what is TRUE about the case. That is `derive()`'s output: totals, changes,
percentages and shares computed deterministically from the rows. A number in
that set is true whether or not the payload the model saw contained it.

This distinction is the entire measurement:

  GROUNDING_REJECTED_STRICT   the guard rejected a narrative whose numbers were
                              all TRUE -- correct arithmetic the raw payload did
                              not literally contain. The guard was right by its
                              own rule and the user still lost a good answer.
  GROUNDING_REJECTED_CORRECT  the guard rejected a narrative containing a number
                              that is not true under any reading. The guard
                              earned its keep.

Collapsing those two into "rejection rate" would make the baseline look
identical to the structured path while the user experience differs completely.

A TRAP THAT IS DERIVABLE IS NOT A TRAP

Cases carry `trap_values` -- plausible mis-calculations. Any trap that turns out
to be in the truth set is demoted automatically before grading. Without that
rule the key could punish a model for stating something correct, which is how a
benchmark quietly starts measuring its author's arithmetic.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

from rich.console import Console

from rrip.ai.derive import derive
from rrip.ai.narration import (
    ALLOWED_BARE,
    NarrationResult,
    collect_allowed,
    extract_numbers,
    narrate,
)
from rrip.ai.provider import LLMProvider
from rrip.config import PROJECT_ROOT
from rrip.eval.narration_cases import CASES, DATASET_VERSION, NarrationCase

console = Console()

FAITHFUL = "FAITHFUL"
UNSUPPORTED_NUMBER = "UNSUPPORTED_NUMBER"
NUMERICALLY_WRONG = "NUMERICALLY_WRONG"
SEMANTICALLY_WRONG = "SEMANTICALLY_WRONG"
VALID_REFUSAL = "VALID_REFUSAL"
GROUNDING_REJECTED_CORRECT = "GROUNDING_REJECTED_CORRECT"
GROUNDING_REJECTED_STRICT = "GROUNDING_REJECTED_STRICT"
HARNESS_ERROR = "HARNESS_ERROR"

FAILURE_VERDICTS = {UNSUPPORTED_NUMBER, NUMERICALLY_WRONG, SEMANTICALLY_WRONG}

INCREASE_WORDS = ("increase", "increased", "rose", "grew", "growth", "up ",
                  "higher", "gain", "climbed", "improved", "rising")
DECREASE_WORDS = ("decrease", "decreased", "fell", "declined", "decline", "drop",
                  "dropped", "down ", "lower", "shrank", "reduced", "falling",
                  "loss")
NO_CHANGE_WORDS = ("unchanged", "no change", "flat", "identical", "the same",
                   "stable", "steady")
SUPERLATIVES = ("highest", "largest", "biggest", "top ", "leading", "most ",
                "strongest", "peak", "greatest", "best")


@dataclass
class CaseOutcome:
    case_id: str
    category: str
    path: str                      # "baseline" | "structured"
    verdict: str
    escaped_to_user: bool
    ok: bool
    narrative_text: str = ""
    guard_violations: list[float] = field(default_factory=list)
    offending_values: list[float] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    duration_ms: float = 0.0
    cached: bool = False


def _prose(narrative: dict | None) -> str:
    """Flatten the narrative, keeping FIELD BOUNDARIES as sentence boundaries.

    Joining with a bare space produced a false failure: a `so_what` ending
    without punctuation ran into the next `statement`, so "identifies the peak
    performing week" merged with "Week 9 dropped to 64000.00" into one apparent
    sentence containing a superlative and the wrong week. The narrative was
    correct; the flattening invented the error. Fields are separate claims and
    are punctuated as such.
    """
    if not narrative:
        return ""
    parts = [str(narrative.get("headline", ""))]
    for i in narrative.get("insights", []) or []:
        if isinstance(i, dict):
            parts.append(str(i.get("statement", "")))
            parts.append(str(i.get("so_what", "")))
    parts += [str(c) for c in narrative.get("caveats", []) or []]

    out = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        out.append(p if p[-1] in ".!?" else p + ".")
    return " ".join(out)


# Words that turn a mention into a disclaimer. A narrative saying "it is not
# possible to determine profit margins" is doing exactly what it should, and
# flagging it as an unsupported claim rewards silence over honesty.
NEGATION_MARKERS = (
    "not ", "n't", "cannot", "can not", "without", "lacks", "lack of",
    "unable", "no cost", "does not", "do not", "is not", "are not", "never",
    "insufficient", "cannot be", "impossible", "unavailable", "absent",
)


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


def _is_negated(sentence: str) -> bool:
    return any(m in sentence.lower() for m in NEGATION_MARKERS)


def _asserted_terms(text: str, terms: tuple[str, ...]) -> list[str]:
    """Terms genuinely claimed, ignoring those inside a disclaimer."""
    hits = []
    for term in terms:
        for sentence in _sentences(text):
            if term.lower() in sentence.lower() and not _is_negated(sentence):
                hits.append(term)
                break
    return hits


def truth_set(case: NarrationCase) -> set[float]:
    """Everything true about this case, regardless of which payload was sent."""
    rows, columns = case.materialise()
    structured = derive(rows, columns, case.question).data
    return collect_allowed(structured) | {float(x) for x in ALLOWED_BARE}


def effective_traps(case: NarrationCase, truth: set[float]) -> list[float]:
    """Traps that are genuinely false. A derivable 'trap' is dropped."""
    return [t for t in case.trap_values if t not in truth]


def _mentions(text: str, value: float) -> bool:
    """Is this exact value stated in the prose?"""
    for found, _ctx in extract_numbers(text):
        if found == value:
            return True
    return False


def _direction_conflict(text: str, expected: str) -> str | None:
    """Only fires when the text asserts the OPPOSITE and never the truth.

    Deliberately conservative. A narrative covering two measures moving in
    different directions legitimately contains both words, and reporting that as
    an error would manufacture failures.

    Direction words inside a disclaimer do not count either. "campaign growth
    cannot be fully evaluated" contains "growth" and asserts no growth; reading
    it as a claim of increase produced a false failure on NAR-062.
    """
    live = " ".join(s for s in _sentences(text) if not _is_negated(s)).lower()
    low = live
    has_inc = any(w in low for w in INCREASE_WORDS)
    has_dec = any(w in low for w in DECREASE_WORDS)
    has_flat = any(w in low for w in NO_CHANGE_WORDS)

    if expected == "increase" and has_dec and not has_inc:
        return "describes a decrease where the data increases"
    if expected == "decrease" and has_inc and not has_dec:
        return "describes an increase where the data decreases"
    if expected == "no_change" and (has_inc or has_dec) and not has_flat:
        return "describes a change where the values are identical"
    return None


def _top_conflict(text: str, case: NarrationCase) -> str | None:
    """Does a superlative sentence name the wrong row?"""
    if not case.expected_top:
        return None
    label_col, winner = case.expected_top
    if winner is None:
        return None

    others = []
    for r in case.rows:
        v = r.get(label_col)
        if v is not None and str(v) != str(winner):
            others.append(str(v))
    if not others:
        return None

    for sentence in re.split(r"(?<=[.!?])\s+", text):
        low = sentence.lower()
        if not any(s in low for s in SUPERLATIVES):
            continue
        if str(winner).lower() in low:
            continue
        for other in others:
            if re.search(rf"\b{re.escape(other.lower())}\b", low):
                return (f"names {other!r} with a superlative; the largest is "
                        f"{winner!r}")
    return None


def classify(case: NarrationCase, result: NarrationResult,
             truth: set[float]) -> tuple[str, list[str], list[float]]:
    """Return (verdict, reasons, offending values)."""
    traps = effective_traps(case, truth)

    if not result.ok:
        if result.violations:
            invented = [v.value for v in result.violations if v.value not in truth]
            if invented:
                return (GROUNDING_REJECTED_CORRECT,
                        [f"guard blocked untrue value(s): {invented}"], invented)
            blocked = [v.value for v in result.violations]
            return (GROUNDING_REJECTED_STRICT,
                    ["guard blocked a narrative whose numbers were all true but "
                     f"absent from the payload sent: {blocked}"], blocked)
        return (HARNESS_ERROR, [result.rejected_reason or "no narrative"], [])

    text = _prose(result.narrative)
    reasons: list[str] = []
    offending: list[float] = []

    trap_hits = [t for t in traps if _mentions(text, t)]
    if trap_hits:
        reasons.append(f"states known-wrong value(s): {trap_hits}")
        offending += trap_hits

    invented = [v for v, _ctx in extract_numbers(text)
                if v not in truth and v not in trap_hits]
    if invented:
        reasons.append(f"states value(s) absent from the data: {sorted(set(invented))}")
        offending += invented

    semantic: list[str] = []
    if case.expected_direction:
        conflict = _direction_conflict(text, case.expected_direction)
        if conflict:
            semantic.append(conflict)
    top = _top_conflict(text, case)
    if top:
        semantic.append(top)
    for term in _asserted_terms(text, case.forbidden_terms):
        semantic.append(f"asserts {term!r}, which the input cannot support")

    if trap_hits:
        return (NUMERICALLY_WRONG, reasons + semantic, offending)
    if invented:
        return (UNSUPPORTED_NUMBER, reasons + semantic, offending)
    if semantic:
        return (SEMANTICALLY_WRONG, semantic, [])

    if case.expect_no_claim_about:
        return (VALID_REFUSAL,
                [f"made no unsupported claim about {case.expect_no_claim_about}"],
                [])
    return (FAITHFUL, [], [])


async def run_case(case: NarrationCase, provider: LLMProvider, path: str,
                   truth: set[float]) -> CaseOutcome:
    rows, columns = case.materialise()
    payload = (derive(rows, columns, case.question).data if path == "structured"
               else {"columns": columns, "rows": rows})

    t0 = time.perf_counter()
    try:
        result = await narrate(payload, case.question, provider)
    except Exception as exc:
        return CaseOutcome(case.id, case.category, path, HARNESS_ERROR, False,
                           False, reasons=[f"{type(exc).__name__}: {exc}"],
                           duration_ms=(time.perf_counter() - t0) * 1000)

    verdict, reasons, offending = classify(case, result, truth)
    return CaseOutcome(
        case_id=case.id, category=case.category, path=path, verdict=verdict,
        escaped_to_user=result.ok and verdict in FAILURE_VERDICTS,
        ok=result.ok, narrative_text=_prose(result.narrative)[:600],
        guard_violations=[v.value for v in result.violations],
        offending_values=sorted(set(offending)), reasons=reasons,
        duration_ms=(time.perf_counter() - t0) * 1000)


def summarise(outcomes: list[CaseOutcome]) -> dict:
    def bucket(path: str) -> dict:
        subset = [o for o in outcomes if o.path == path]
        n = len(subset)
        if not n:
            return {}
        counts: dict[str, int] = {}
        for o in subset:
            counts[o.verdict] = counts.get(o.verdict, 0) + 1
        graded = [o for o in subset if o.verdict != HARNESS_ERROR]
        return {
            "n": n,
            "verdicts": dict(sorted(counts.items())),
            "faithful_pct": round(100 * counts.get(FAITHFUL, 0) / n, 1),
            "valid_refusal_pct": round(100 * counts.get(VALID_REFUSAL, 0) / n, 1),
            "unsupported_number_pct": round(
                100 * counts.get(UNSUPPORTED_NUMBER, 0) / n, 1),
            "numerically_wrong_pct": round(
                100 * counts.get(NUMERICALLY_WRONG, 0) / n, 1),
            "semantically_wrong_pct": round(
                100 * counts.get(SEMANTICALLY_WRONG, 0) / n, 1),
            "grounding_rejected_correct_pct": round(
                100 * counts.get(GROUNDING_REJECTED_CORRECT, 0) / n, 1),
            "grounding_rejected_strict_pct": round(
                100 * counts.get(GROUNDING_REJECTED_STRICT, 0) / n, 1),
            # The number that matters for a user: a wrong claim the guard let
            # through and a reader would have seen.
            "escaped_to_user": sum(o.escaped_to_user for o in subset),
            "escaped_to_user_pct": round(
                100 * sum(o.escaped_to_user for o in subset) / n, 1),
            # Usable = the reader got an answer at all.
            "delivered_pct": round(100 * sum(o.ok for o in subset) / n, 1),
            "harness_errors": n - len(graded),
        }

    base, struct = bucket("baseline"), bucket("structured")
    delta = {}
    paired = _paired_comparison(outcomes)
    if base and struct:
        for key in ("faithful_pct", "unsupported_number_pct",
                    "numerically_wrong_pct", "semantically_wrong_pct",
                    "grounding_rejected_strict_pct",
                    "grounding_rejected_correct_pct", "escaped_to_user_pct",
                    "delivered_pct"):
            delta[key] = round(struct[key] - base[key], 1)
    return {"baseline": base, "structured": struct, "delta_pp": delta,
            "paired": paired}


def _paired_comparison(outcomes: list[CaseOutcome]) -> dict:
    """Same case, both paths -- is the difference more than noise?

    A percentage-point delta over 65 cases can be two cases. Reporting "+3.1pp"
    without saying how many cases that is, and whether it could be chance,
    invites the reader to treat a coin flip as a result. This is an exact
    McNemar test on the discordant pairs: the only cases that carry information
    are the ones where the two paths disagree.
    """
    from math import comb

    acceptable = {FAITHFUL, VALID_REFUSAL}
    by_case: dict[str, dict[str, str]] = {}
    for o in outcomes:
        by_case.setdefault(o.case_id, {})[o.path] = o.verdict

    both = structured_only = baseline_only = neither = 0
    discordant: list[dict] = []
    for cid, paths in by_case.items():
        if "baseline" not in paths or "structured" not in paths:
            continue
        b_ok = paths["baseline"] in acceptable
        s_ok = paths["structured"] in acceptable
        if b_ok and s_ok:
            both += 1
        elif s_ok:
            structured_only += 1
            discordant.append({"case_id": cid, "favours": "structured",
                               "baseline_verdict": paths["baseline"]})
        elif b_ok:
            baseline_only += 1
            discordant.append({"case_id": cid, "favours": "baseline",
                               "structured_verdict": paths["structured"]})
        else:
            neither += 1

    n = structured_only + baseline_only
    p_value = None
    if n:
        k = min(structured_only, baseline_only)
        p_value = min(sum(comb(n, i) for i in range(k + 1)) / 2 ** n * 2, 1.0)

    return {
        "n_cases": len(by_case),
        "acceptable_on_both": both,
        "structured_only": structured_only,
        "baseline_only": baseline_only,
        "acceptable_on_neither": neither,
        "n_discordant": n,
        "mcnemar_exact_p": round(p_value, 4) if p_value is not None else None,
        "significant_at_005": bool(p_value is not None and p_value < 0.05),
        "discordant": discordant,
        "interpretation": (
            "Every discordant pair favours the structured path, but the count "
            "is too small to exclude chance at the 0.05 level. Direction is "
            "consistent; the effect is not established."
            if n and baseline_only == 0 and (p_value or 1) >= 0.05
            else "See mcnemar_exact_p."),
    }


def provenance(provider: LLMProvider) -> dict:
    import subprocess

    from rrip.ai import narration
    from rrip.config import settings
    from rrip.semantic import render_prompt

    def git(*args: str) -> str:
        try:
            return subprocess.run(["git", *args], capture_output=True, text=True,
                                  cwd=PROJECT_ROOT, timeout=10).stdout.strip() or "unknown"
        except (OSError, subprocess.SubprocessError):
            return "unknown"

    return {
        "dataset_version": DATASET_VERSION,
        "n_cases": len(CASES),
        # The narration SYSTEM prompt, hashed. No API key is read here and none
        # is ever written to a report.
        "narration_prompt_sha256": hashlib.sha256(
            narration.SYSTEM.encode()).hexdigest()[:16],
        "semantic_layer_sha256": hashlib.sha256(
            render_prompt(published=settings.is_published).encode()).hexdigest()[:16],
        "git_commit": git("rev-parse", "HEAD"),
        "git_dirty": bool(git("status", "--porcelain")),
        "python": sys.version.split()[0],
    }


async def run(provider: LLMProvider, limit: int | None = None,
              category: str | None = None) -> dict:
    if not provider.available:
        raise RuntimeError(
            f"provider {provider.name!r} has no API key configured. This "
            "benchmark measures a live model; it will not substitute another "
            "provider or report a number it did not obtain.")

    cases = CASES if not category else [c for c in CASES if c.category == category]
    if limit:
        cases = cases[:limit]

    outcomes: list[CaseOutcome] = []
    for i, case in enumerate(cases, 1):
        truth = truth_set(case)
        for path in ("baseline", "structured"):
            o = await run_case(case, provider, path, truth)
            outcomes.append(o)
            mark = ("[green]" if o.verdict in (FAITHFUL, VALID_REFUSAL)
                    else "[yellow]" if o.verdict.startswith("GROUNDING")
                    else "[red]")
            console.print(f"[dim]{i:>3}/{len(cases)}[/dim] {case.id} "
                          f"{path:11} {mark}{o.verdict}[/]")

    report = {
        "run_at": datetime.now(UTC).isoformat(),
        "provider": provider.name,
        "model": provider.model,
        "llm_cache_enabled": provider.use_cache,
        "provenance": provenance(provider),
        "summary": summarise(outcomes),
        "outcomes": [asdict(o) for o in outcomes],
    }

    out_dir = PROJECT_ROOT / "reports" / "eval"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    (out_dir / f"narration-{stamp}.json").write_text(
        json.dumps(report, indent=2, default=str), encoding="utf-8")
    (out_dir / "narration-latest.json").write_text(
        json.dumps(report, indent=2, default=str), encoding="utf-8")
    return report


def print_summary(report: dict) -> None:
    from rich.table import Table

    s = report["summary"]
    console.print(f"\n[bold]Narration faithfulness[/bold]  model="
                  f"{report['model']}  dataset={report['provenance']['dataset_version']}")

    t = Table(show_header=True, header_style="bold")
    t.add_column("Metric")
    t.add_column("Baseline", justify="right")
    t.add_column("Structured", justify="right")
    t.add_column("Delta (pp)", justify="right")
    rows = [
        ("Faithful", "faithful_pct"),
        ("Valid refusal", "valid_refusal_pct"),
        ("Unsupported number", "unsupported_number_pct"),
        ("Numerically wrong", "numerically_wrong_pct"),
        ("Semantically wrong", "semantically_wrong_pct"),
        ("Guard rejected (correctly)", "grounding_rejected_correct_pct"),
        ("Guard rejected (true but absent)", "grounding_rejected_strict_pct"),
        ("Escaped to user", "escaped_to_user_pct"),
        ("Answer delivered at all", "delivered_pct"),
    ]
    for label, key in rows:
        d = s["delta_pp"].get(key)
        t.add_row(label, f"{s['baseline'].get(key)}%", f"{s['structured'].get(key)}%",
                  f"{d:+.1f}" if d is not None else "-")
    console.print(t)

    p = s.get("paired", {})
    if p:
        colour = "green" if p.get("significant_at_005") else "yellow"
        console.print(
            f"\n[bold]Paired comparison[/bold]  {p['n_cases']} cases, "
            f"{p['n_discordant']} discordant "
            f"({p['structured_only']} favour structured, "
            f"{p['baseline_only']} favour baseline)")
        console.print(f"  exact McNemar p = [{colour}]{p['mcnemar_exact_p']}[/{colour}]"
                      f"  significant at 0.05: {p['significant_at_005']}")
        console.print(f"  [dim]{p['interpretation']}[/dim]")


def run_sync(provider: LLMProvider, **kw) -> dict:
    """Narration needs no database, so a plain event loop is fine here."""
    return asyncio.run(run(provider, **kw))
