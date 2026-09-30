"""The parallel-trends verdict must say what the test can support.

check_parallel_trends fits one linear group x week interaction on weekly group
means -- a low-power test. A pass means the test did not detect a pre-trend,
not that the trends are parallel, and the verdict text used to say
"Parallel trends is supported".
"""

from __future__ import annotations

import pandas as pd

from rrip.ai.causal import check_parallel_trends

# One shared, non-linear deviation per week. check_parallel_trends regresses on
# WEEKLY GROUP MEANS, so the fixture's variance must survive that averaging.
#
# The previous fixture used per-household noise ((hh*7 + week*3) % 5 - 2) * 0.1,
# which over 20 households hits every residue exactly four times and averages
# to exactly zero each week. The weekly means were then perfectly linear, the
# regression's residual sum of squares was ~1e-26, and the interaction p-value
# was floating-point rounding noise: 0.055 on Windows, 0.045 on the Linux CI
# runner, and between 0.036 and 0.216 under one-ulp perturbations -- so the
# "non-rejection" case rejected on some platforms and runs.
#
# Here both groups share the same deviations (the treated group sits at a
# constant +5 level), so their pre-period trends are identical by
# construction: the interaction is exactly zero in exact arithmetic while the
# residuals are real (SSR ~2.5). The non-rejection case gets p = 1.000 and the
# divergent case p ~ 1e-25; neither sits anywhere near alpha = 0.05.
WEEKLY_DEVIATION = (0.4, -0.3, 0.1, -0.5, 0.3, 0.2, -0.4, 0.5, -0.1, -0.2, 0.3, -0.3)


def _panel(treated_slope: float) -> pd.DataFrame:
    rows = []
    for week, deviation in enumerate(WEEKLY_DEVIATION, start=1):
        for treated in (0, 1):
            slope = treated_slope if treated else 1.0
            level = 5.0 if treated else 0.0
            for _hh in range(20):
                rows.append({"week_no": week, "treated": treated, "post": 0,
                             "spend": 50 + level + slope * week + deviation})
    return pd.DataFrame(rows)


def test_a_pass_is_worded_as_non_rejection() -> None:
    pt = check_parallel_trends(_panel(treated_slope=1.0))
    assert pt.passed
    # Margin, not just a pass: a fixture near alpha is a coin flip across
    # platforms, which is how the previous one failed on the CI runner.
    assert pt.interaction_pvalue > 0.5
    text = pt.verdict.lower()
    assert "did not reject" in text
    for overclaim in ("is supported", "trends hold", "proven", "confirmed"):
        assert overclaim not in text, overclaim


def test_a_failure_still_says_violated() -> None:
    pt = check_parallel_trends(_panel(treated_slope=4.0))
    assert not pt.passed
    assert pt.interaction_pvalue < 1e-6
    assert "VIOLATED" in pt.verdict


def test_the_causal_page_badge_does_not_overclaim() -> None:
    # The verdict text was corrected, but the page's badge still read
    # "PARALLEL TRENDS HOLD" directly above it.
    from pathlib import Path

    page = (Path(__file__).resolve().parents[1] / "frontend/app/causal/page.tsx").read_text(
        encoding="utf-8")
    assert "'HOLD'" not in page
    assert "NOT REJECTED" in page
