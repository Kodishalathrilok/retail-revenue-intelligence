"""6b -- Grounded narration.

SQL and Python compute the results, the anomalies and the trend breaks. The
model receives ONLY the computed result set and returns structured JSON in which
every insight references specific computed values. Post-validation rejects any
response containing a number that is not present in the input.

WHY THIS IS ENFORCED STRUCTURALLY AND NOT BY PROMPTING

This project's own documents contain three instances of a plausible number being
written where a measured one belonged -- a 15.9 ms maximum that was 31.4 ms, a
130,537x misestimate that was 1x, and a "2,203 of 2,500 households" that was
427. All three were produced by careful writing, all three looked right in
context, and none was caught by review. They were caught by comparing the figure
to its source.

A language model is subject to the identical failure at higher volume. Asking it
to be careful is the control that already failed. So the guard is arithmetic:
extract every numeric literal from the response, and require each one to appear
in the input data or be derivable from it by an explicitly permitted operation.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from rrip.ai.provider import LLMProvider

# Numbers a narrative can legitimately contain without them appearing in the
# data: small ordinals and counts used to structure prose ("the top 3", "two of
# the five"). Kept deliberately short -- every entry is a hole in the guard.
ALLOWED_BARE = {0, 1, 2, 3, 4, 5, 10, 100}

NUMBER_RE = re.compile(r"-?\d[\d,]*\.?\d*")

SYSTEM = """\
You explain retail analytics results to a business audience.

ABSOLUTE RULE: you may ONLY use numbers that appear in the DATA block provided.
You must not calculate, estimate, round, infer, or introduce any figure that is
not literally present in that block. If you want to express a relationship the
data does not contain as a number, describe it in words instead.

Return ONLY valid JSON, no markdown fences, matching:

{
  "headline": "one sentence summarising the result",
  "insights": [
    {
      "statement": "what is true, in business language",
      "referenced_values": [<the exact numbers from DATA that this uses>],
      "so_what": "why it matters"
    }
  ],
  "caveats": ["limitations a reader must know"]
}

Every number in "statement" must also appear in "referenced_values", and every
value in "referenced_values" must appear in the DATA block."""


@dataclass
class GroundingViolation:
    value: float
    context: str
    reason: str


@dataclass
class NarrationResult:
    ok: bool
    narrative: dict | None = None
    violations: list[GroundingViolation] = field(default_factory=list)
    raw_response: str = ""
    provider: str | None = None
    rejected_reason: str | None = None


def extract_numbers(text: str) -> list[tuple[float, str]]:
    """Every numeric literal in the text, with surrounding context."""
    out: list[tuple[float, str]] = []
    for m in NUMBER_RE.finditer(text):
        raw = m.group(0).rstrip(".").replace(",", "")
        if not raw or raw in {"-", "."}:
            continue
        try:
            out.append((float(raw), text[max(0, m.start() - 40):m.end() + 40]))
        except ValueError:
            continue
    return out


def collect_allowed(data: Any) -> set[float]:
    """Every number appearing anywhere in the input, at several roundings.

    Rounded forms are permitted because presenting 45.63 as 45.6 is legitimate
    narration, not fabrication. Anything not reachable this way is rejected.
    """
    allowed: set[float] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                walk(k)
                walk(v)
        elif isinstance(node, (list, tuple)):
            for v in node:
                walk(v)
        elif isinstance(node, bool):
            return
        elif isinstance(node, (int, float)):
            _add(float(node))
        elif isinstance(node, str):
            for val, _ctx in extract_numbers(node):
                _add(val)

    def _add(v: float) -> None:
        allowed.add(v)
        for places in (0, 1, 2, 3):
            allowed.add(round(v, places))
        if v != 0:
            allowed.add(round(v))
            # An integer-valued float and its int form are the same number.
            if float(v).is_integer():
                allowed.add(int(v))

    walk(data)
    return allowed


def validate_grounding(narrative_text: str, data: Any) -> list[GroundingViolation]:
    allowed = collect_allowed(data) | {float(x) for x in ALLOWED_BARE}
    violations: list[GroundingViolation] = []
    for value, context in extract_numbers(narrative_text):
        # Membership only. Rounding is applied when BUILDING the allowed set --
        # every legitimate rounding of a data value is already in it.
        #
        # Do not also round the narrative value here. That inverts the direction
        # of the check and opens a hole: round(0.007) is 0, 0 is a permitted
        # bare number, so any invented value below 0.5 would be accepted. An
        # adversarial test caught exactly that.
        if value in allowed:
            continue
        violations.append(GroundingViolation(
            value=value,
            context=context.strip().replace("\n", " "),
            reason="number does not appear in the computed input data"))
    return violations


def _strip_fences(text: str) -> str:
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*", "", t, flags=re.IGNORECASE)
    return re.sub(r"\s*```$", "", t).strip()


async def narrate(data: Any, question: str, provider: LLMProvider) -> NarrationResult:
    """Produce a grounded narrative, or refuse to produce one at all.

    A response failing the guard is REJECTED, not repaired. Repairing it would
    mean deciding which invented figure to keep.
    """
    payload = json.dumps(data, default=str, indent=2)[:12000]
    prompt = (f"QUESTION\n{question}\n\nDATA\n{payload}\n\n"
              "Return the JSON described in the system instructions.")

    try:
        resp = await provider.complete(prompt, system=SYSTEM)
    except Exception as exc:
        return NarrationResult(ok=False, provider=provider.name,
                               rejected_reason=f"provider error: {exc}")

    raw = resp.text
    try:
        narrative = json.loads(_strip_fences(raw))
    except json.JSONDecodeError as exc:
        return NarrationResult(ok=False, raw_response=raw, provider=provider.name,
                               rejected_reason=f"response was not valid JSON: {exc}")

    if not isinstance(narrative, dict) or "insights" not in narrative:
        return NarrationResult(ok=False, raw_response=raw, provider=provider.name,
                               rejected_reason="response missing required 'insights' key")

    # Validate only the prose the model wrote. referenced_values are checked
    # separately: a value there that is absent from the data is also a violation.
    prose = " ".join(
        [str(narrative.get("headline", ""))]
        + [str(i.get("statement", "")) + " " + str(i.get("so_what", ""))
           for i in narrative.get("insights", []) if isinstance(i, dict)]
        + [str(c) for c in narrative.get("caveats", [])])

    violations = validate_grounding(prose, data)

    allowed = collect_allowed(data) | {float(x) for x in ALLOWED_BARE}
    for ins in narrative.get("insights", []):
        if not isinstance(ins, dict):
            continue
        for v in ins.get("referenced_values", []) or []:
            try:
                fv = float(str(v).replace(",", ""))
            except (TypeError, ValueError):
                continue
            if fv not in allowed and round(fv, 2) not in allowed:
                violations.append(GroundingViolation(
                    fv, str(ins.get("statement", ""))[:80],
                    "referenced_values contains a number absent from the data"))

    if violations:
        return NarrationResult(
            ok=False, narrative=narrative, violations=violations, raw_response=raw,
            provider=provider.name,
            rejected_reason=(f"grounding guard rejected {len(violations)} "
                             "ungrounded number(s)"))

    return NarrationResult(ok=True, narrative=narrative, raw_response=raw,
                           provider=provider.name)
