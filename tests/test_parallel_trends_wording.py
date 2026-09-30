"""The parallel-trends verdict must say what the test can support.

check_parallel_trends fits one linear group x week interaction on weekly group
means -- a low-power test. A pass means the test did not detect a pre-trend,
not that the trends are parallel, and the verdict text used to say
"Parallel trends is supported".
"""

from __future__ import annotations

import pandas as pd

from rrip.ai.causal import check_parallel_trends


def _panel(treated_slope: float) -> pd.DataFrame:
    rows = []
    for week in range(1, 13):
        for treated in (0, 1):
            slope = treated_slope if treated else 1.0
            for hh in range(20):
                noise = ((hh * 7 + week * 3) % 5 - 2) * 0.1
                rows.append({"week_no": week, "treated": treated, "post": 0,
                             "spend": 50 + slope * week + noise})
    return pd.DataFrame(rows)


def test_a_pass_is_worded_as_non_rejection() -> None:
    pt = check_parallel_trends(_panel(treated_slope=1.0))
    assert pt.passed
    text = pt.verdict.lower()
    assert "did not reject" in text
    for overclaim in ("is supported", "trends hold", "proven", "confirmed"):
        assert overclaim not in text, overclaim


def test_a_failure_still_says_violated() -> None:
    pt = check_parallel_trends(_panel(treated_slope=4.0))
    assert not pt.passed
    assert "VIOLATED" in pt.verdict


def test_the_causal_page_badge_does_not_overclaim() -> None:
    # The verdict text was corrected, but the page's badge still read
    # "PARALLEL TRENDS HOLD" directly above it.
    from pathlib import Path

    page = (Path(__file__).resolve().parents[1] / "frontend/app/causal/page.tsx").read_text(
        encoding="utf-8")
    assert "'HOLD'" not in page
    assert "NOT REJECTED" in page
