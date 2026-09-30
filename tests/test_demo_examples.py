"""The Ask page must only advertise questions the hosted demo can answer.

The hosted tier exposes pub_* aggregates only. The page once offered "Which 10
commodities have the highest reorder rate?", which that schema cannot answer
(reorder is published per department). Every positive example is therefore
tied to a published-tier benchmark case by id, with identical wording, so the
benchmark vouches for the demo and removing or re-tiering a case breaks this
test rather than the page.

Source of truth: frontend/lib/examples.ts (moved there from the Ask page so the
Brief can reuse the same verified list).
"""

from __future__ import annotations

import re
from pathlib import Path

from rrip.ai.router import UNSAFE, classify
from rrip.eval.cases import BOTH, CASES, CORRECT, LOCAL, PUBLISHED, REFUSES

SOURCE = Path(__file__).resolve().parents[1] / "frontend/lib/examples.ts"
BY_ID = {c.id: c for c in CASES}


def _examples() -> list[dict]:
    text = SOURCE.read_text(encoding="utf-8")
    block = re.search(r"export const EXAMPLES: Example\[\] = \[(.*?)\n\];", text, re.S)
    assert block, "EXAMPLES array not found in frontend/lib/examples.ts"
    out = []
    for entry in re.findall(r"\{(.*?)\}", block.group(1), re.S):
        question = re.search(r'question:\s*"([^"]+)"', entry)
        benchmark = re.search(r'benchmark:\s*(?:"([^"]+)"|null)', entry)
        assert question and benchmark, f"unparseable example entry: {entry!r}"
        out.append({"question": question.group(1), "benchmark": benchmark.group(1),
                    "refusal": bool(re.search(r"refusal:\s*true", entry))})
    return out


def test_parser_sees_every_example() -> None:
    examples = _examples()
    assert len([e for e in examples if not e["refusal"]]) >= 3
    assert len([e for e in examples if e["refusal"]]) == 1


def test_every_positive_example_is_its_published_benchmark_case_verbatim() -> None:
    for e in (e for e in _examples() if not e["refusal"]):
        case = BY_ID.get(e["benchmark"])
        assert case, f"{e['benchmark']}: no such benchmark case"
        assert case.expectation == CORRECT, f"{case.id}: not a graded (CORRECT) case"
        assert case.tier in (PUBLISHED, BOTH), f"{case.id}: not answerable on the hosted tier"
        assert e["question"] == case.question, f"{case.id}: demo wording drifted from the case"


def test_no_demo_is_a_question_the_hosted_tier_cannot_answer() -> None:
    hosted = [c for c in CASES if c.tier in (PUBLISHED, BOTH)]
    refused_on_hosted = {c.question for c in hosted if c.expectation == REFUSES}
    # A question that exists only as a local-tier case needs the full star
    # schema; the same wording can also be a published case (pub-02 is).
    local_only = ({c.question for c in CASES if c.tier == LOCAL}
                  - {c.question for c in hosted})
    advertised = {e["question"] for e in _examples() if not e["refusal"]}
    assert not advertised & refused_on_hosted
    assert not advertised & local_only


def test_the_refusal_example_is_refused_before_any_model_call() -> None:
    refusal = next(e for e in _examples() if e["refusal"])
    assert refusal["benchmark"] is None
    assert classify(refusal["question"]).verdict == UNSAFE
