"""Leakage regression tests.

Every bug found by the audit becomes a permanent test here. That is the same
rule the rest of this project's evaluation infrastructure follows: a defect
that was found once and not encoded is a defect waiting to be reintroduced by
whoever refactors next.

Currently encoded:

  * the ungrouped rolling window (found by the audit on its first run)
  * pandas' streaming rolling variance, which made that bug detectable at all
  * the whole-panel fitted-statistics leak
  * the test-set lock
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from rrip.forecast import contract as C
from rrip.forecast import features as F
from rrip.forecast import leakage as L
from rrip.forecast import split as S
from tests.test_forecast_features import DEPTS, make_panel


@pytest.fixture(scope="module")
def panel() -> pd.DataFrame:
    rng = np.random.default_rng(11)

    def rev(dept, week):
        base = {"ALPHA": 5000.0, "BETA": 900.0, "GAMMA": 120.0}[dept]
        return float(max(base * (1 + 0.25 * rng.standard_normal()), 0.0))

    return make_panel(revenue=rev)


@pytest.fixture(scope="module")
def stats(panel) -> F.FittedStats:
    return F.fit_department_stats(panel, DEPTS)


# --- the declared checks ----------------------------------------------------

def test_full_audit_passes(panel, stats):
    result = L.run_audit(panel, stats, strict=True)
    assert result["passed"]
    assert {c["name"] for c in result["checks"]} == {
        "declared_cutoff", "history_floor", "split_order", "fitted_scope",
        "target_independence", "future_window"}


def test_audit_raises_when_statistics_are_fitted_on_the_wrong_window(panel):
    """fitted_scope must fail if the encodings saw validation or test weeks."""
    bad = F.fit_department_stats(panel, DEPTS,
                                 weeks=(C.TRAIN_WEEKS[0], C.TEST_WEEKS[1]))
    with pytest.raises(L.LeakageError, match="fitted_scope"):
        L.run_audit(panel, bad, strict=True)


# --- REGRESSION: the ungrouped rolling window -------------------------------

def test_rolling_features_are_grouped_by_series(panel, stats):
    """A department's features must not depend on any other department's data.

    THE BUG THIS ENCODES. `df.groupby('department')[col].shift(1).rolling(8)`
    reads correctly and is wrong: SeriesGroupBy.shift returns a plain Series,
    so the rolling that follows runs across the whole frame and each
    department's early windows are filled with the previous department's tail.

    Here the panel is reordered -- BETA's rows are moved to the front -- and
    the features are rebuilt. A correctly grouped implementation is invariant
    to that; the buggy one is not.
    """
    base = F.build_features(panel, stats)

    order = {"BETA": 0, "GAMMA": 1, "ALPHA": 2}
    reordered = panel.copy()
    reordered["_k"] = reordered.department.map(order)
    reordered = (reordered.sort_values(["_k", "week_no"])
                 .drop(columns="_k").reset_index(drop=True))
    after = F.build_features(reordered, stats)

    a = base.sort_values(["department", "week_no"]).reset_index(drop=True)
    b = after.sort_values(["department", "week_no"]).reset_index(drop=True)

    for name in C.FEATURE_NAMES:
        assert np.allclose(a[name], b[name], rtol=1e-12, atol=1e-12), (
            f"{name} changed when the departments were reordered, so it is "
            "reading across series boundaries")


def test_rolling_std_is_exact_not_streaming(panel, stats):
    """REGRESSION: pandas' rolling variance is an add/remove accumulator.

    Once a large value has passed through it the running sum of squares carries
    the rounding error for the rest of the series, so the result depends on
    data that has already left the window. That made rollstd8_ratio move by
    7e-05 relative when an unrelated week was corrupted.

    This asserts the feature equals a two-pass numpy std over exactly the
    window the contract specifies.
    """
    frame = F.build_features(panel, stats)
    src = panel[panel.department == "ALPHA"].set_index("week_no").revenue

    for target in (C.FIRST_TARGET_WEEK, 60, C.LAST_TARGET_WEEK):
        row = frame[(frame.department == "ALPHA")
                    & (frame.week_no == target)].iloc[0]
        window = src.loc[target - C.SCALE_WINDOW_WEEKS:target - 1].to_numpy(float)
        expected = float(np.std(window, ddof=1))
        scale = max(row.scale_usd, C.SCALE_FLOOR_USD)
        assert row.rollstd8_ratio * scale == pytest.approx(expected, rel=1e-12), (
            f"rollstd8_ratio at week {target} does not match a two-pass std")


def test_corrupting_the_target_week_moves_no_feature(panel, stats):
    """The empirical check, independent of any declaration."""
    check = L.check_target_independence(panel, stats)
    assert check.passed, check.detail


def test_truncating_the_panel_at_the_cutoff_changes_nothing(panel, stats):
    check = L.check_future_window(panel, stats)
    assert check.passed, check.detail


# --- the oracle: a leak must actually be constructible ----------------------

def test_the_oracle_variant_really_does_differ(panel, stats):
    """A leakage test that cannot build a leak is testing nothing.

    allow_future_promo shifts the promotion features onto the target week. The
    promo features MUST differ; the revenue features must not.
    """
    strict = F.build_features(panel, stats, allow_future_promo=False)
    oracle = F.build_features(panel, stats, allow_future_promo=True)

    promo = ["promo_display_pct_lag1", "promo_mailer_pct_lag1",
             "promo_rows_ratio", "campaigns_active_lag1",
             "campaign_households_lag1"]
    for name in promo:
        assert not np.allclose(strict[name], oracle[name]), (
            f"{name} is identical in the oracle variant, so the leak was not "
            "constructed and the comparison would be meaningless")

    for name in ("rev_lag1_ratio", "roll4_ratio", "rollmed8_ratio"):
        assert np.allclose(strict[name], oracle[name], rtol=1e-12)


# --- the test-set lock ------------------------------------------------------

def test_test_frame_is_locked(panel, stats):
    frame = F.build_features(panel, stats)
    with pytest.raises(S.TestSetLocked, match="locked"):
        S.test_frame(frame)
    with pytest.raises(S.TestSetLocked):
        S.test_frame(frame, "please")

    unlocked = S.test_frame(frame, S.TEST_UNLOCK)
    assert int(unlocked.week_no.min()) == C.TEST_WEEKS[0]
    assert int(unlocked.week_no.max()) == C.TEST_WEEKS[1]


def test_no_rolling_origin_fold_reaches_the_test_window():
    for fold in S.rolling_origin_folds():
        assert fold.validate_weeks[1] < C.TEST_WEEKS[0], fold.describe()
        assert fold.train_weeks[1] < fold.validate_weeks[0], fold.describe()


def test_folds_are_expanding_and_ordered():
    folds = S.rolling_origin_folds()
    assert len(folds) == C.N_ORIGINS
    for a, b in zip(folds, folds[1:], strict=False):
        assert b.train_weeks[1] > a.train_weeks[1], "folds must expand"
        assert b.validate_weeks[0] > a.validate_weeks[0]
    assert folds[-1].validate_weeks[1] == C.VALIDATION_WEEKS[1]
