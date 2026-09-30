"""The NL Query page must only advertise questions the hosted demo can answer.

The hosted tier exposes pub_* aggregates only. The page previously offered
"Which 10 commodities have the highest reorder rate?", which that schema cannot
answer (reorder is published per department). Tying every positive example to
a published-tier benchmark case means the benchmark is what vouches for the
demo, and removing or re-tiering a case breaks this test rather than the page.
"""

from __future__ import annotations

import re
from pathlib import Path

from rrip.ai.router import UNSAFE, classify
from rrip.eval.cases import BOTH, CASES, CORRECT, PUBLISHED

PAGE = Path(__file__).resolve().parents[1] / "frontend/app/query/page.tsx"


def _examples() -> list[str]:
    block = re.search(r"const EXAMPLES = \[(.*?)\];", PAGE.read_text(encoding="utf-8"), re.S)
    assert block, "EXAMPLES array not found in query/page.tsx"
    return re.findall(r"'([^']+)'", block.group(1))


def test_every_positive_example_is_a_published_benchmark_case() -> None:
    graded = {c.question for c in CASES
              if c.expectation == CORRECT and c.tier in (PUBLISHED, BOTH)}
    positives = _examples()[:-1]
    assert positives, "no positive examples"
    missing = [q for q in positives if q not in graded]
    assert not missing, f"not a graded published-tier case: {missing}"


def test_the_adversarial_example_is_refused_before_any_model_call() -> None:
    assert classify(_examples()[-1]).verdict == UNSAFE
