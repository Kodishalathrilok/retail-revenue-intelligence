"""Feature construction, on a synthetic panel with known answers.

Synthetic rather than the real database, so these run in CI without Postgres
and so the expected values can be computed by hand. The panel is built with
revenue equal to a simple function of the week, which makes every lag and
rolling statistic checkable by arithmetic instead of by another implementation
of the same code.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from rrip.forecast import contract as C
from rrip.forecast import features as F
from rrip.forecast import panel as P

DEPTS = ["ALPHA", "BETA", "GAMMA"]


def make_panel(first: int = C.HISTORY_FLOOR_WEEK,
               last: int = C.LAST_TARGET_WEEK,
               revenue=None) -> pd.DataFrame:
    """A dense department x week panel with deterministic revenue."""
    rows = []
    for d_i, dept in enumerate(DEPTS):
        for w in range(first, last + 1):
            rev = (revenue(dept, w) if revenue
                   else 1000.0 * (d_i + 1) + 10.0 * w)
            rows.append({
                "department": dept, "week_no": w, "revenue": rev,
                "dept_baskets": 10, "dept_households": 5,
                "panel_revenue": 10_000.0 + 100.0 * w,
                "panel_baskets": 500, "panel_households": 300,
                "promo_rows": 100 + w, "on_display": 10 + (w % 7),
                "in_mailer": 20, "campaigns_active": w % 4,
                "campaign_households": 100 * (w % 4),
            })
    return pd.DataFrame(rows).sort_values(
        ["department", "week_no"]).reset_index(drop=True)


@pytest.fixture(scope="module")
def panel() -> pd.DataFrame:
    return make_panel()


@pytest.fixture(scope="module")
def stats(panel) -> F.FittedStats:
    return F.fit_department_stats(panel, DEPTS)


@pytest.fixture(scope="module")
def frame(panel, stats) -> pd.DataFrame:
    return F.build_features(panel, stats)


# --- shape and completeness -------------------------------------------------

def test_frame_starts_at_the_first_target_week(frame):
    assert int(frame.week_no.min()) == C.FIRST_TARGET_WEEK


def test_no_feature_is_nan(frame):
    for name in C.FEATURE_NAMES:
        assert not frame[name].isna().any(), name


def test_every_contract_feature_is_present_in_order(frame):
    mat = F.feature_matrix(frame)
    assert mat.shape == (len(frame), len(C.FEATURE_NAMES))
    assert list(frame[list(C.FEATURE_NAMES)].columns) == list(C.FEATURE_NAMES)


# --- the arithmetic ---------------------------------------------------------

def test_lag_features_are_the_previous_weeks(panel, frame):
    """rev_lagN_ratio * scale must equal revenue N weeks before the target."""
    row = frame[(frame.department == "ALPHA") & (frame.week_no == 60)].iloc[0]
    scale = max(row.scale_usd, C.SCALE_FLOOR_USD)
    src = panel[panel.department == "ALPHA"].set_index("week_no").revenue
    for i in (1, 2, 3, 4):
        assert row[f"rev_lag{i}_ratio"] * scale == pytest.approx(
            src[60 - i], rel=1e-9)


def test_scale_is_the_eight_week_trailing_mean(panel, frame):
    row = frame[(frame.department == "BETA") & (frame.week_no == 55)].iloc[0]
    src = panel[panel.department == "BETA"].set_index("week_no").revenue
    expected = src.loc[55 - C.SCALE_WINDOW_WEEKS:54].mean()
    assert row.scale_usd == pytest.approx(expected, rel=1e-12)


def test_target_ratio_reconstructs_the_target(panel, frame):
    """revenue == target_ratio * clipped scale, exactly."""
    scale = frame.scale_usd.clip(lower=C.SCALE_FLOOR_USD)
    assert np.allclose(frame.target_ratio * scale, frame.revenue, rtol=1e-12)


def test_trend_slope_matches_an_ols_fit(panel, frame):
    row = frame[(frame.department == "GAMMA") & (frame.week_no == 70)].iloc[0]
    src = panel[panel.department == "GAMMA"].set_index("week_no").revenue
    window = src.loc[70 - C.SCALE_WINDOW_WEEKS:69].to_numpy(float)
    expected = np.polyfit(np.arange(len(window)), window, 1)[0]
    scale = max(row.scale_usd, C.SCALE_FLOOR_USD)
    assert row.trend_slope_ratio * scale == pytest.approx(expected, rel=1e-9)


def test_zeros_in_window_counts_zero_revenue_weeks():
    def rev(dept, week):
        return 0.0 if week in (50, 51, 52) else 500.0

    p = make_panel(revenue=rev)
    st = F.fit_department_stats(p, DEPTS)
    f = F.build_features(p, st)
    row = f[(f.department == "ALPHA") & (f.week_no == 55)].iloc[0]
    # window for target 55 is weeks 47..54, which contains 50, 51, 52
    assert row.zeros_in_window == 3


# --- the leakage-adjacent structural guarantees -----------------------------

def test_a_gap_in_the_week_series_is_rejected(panel):
    """shift(1) means the previous ROW. A gap makes every lag mislabelled."""
    broken = panel[panel.week_no != 60]
    with pytest.raises(F.ContiguityError, match="not contiguous"):
        F.assert_contiguous_weeks(broken)


def test_a_department_missing_weeks_is_rejected(panel):
    """The panel must be dense: no sales in a week is zero, not absent."""
    broken = panel[~((panel.department == "BETA") & (panel.week_no > 90))]
    with pytest.raises(F.ContiguityError, match="dense"):
        F.assert_contiguous_weeks(broken)


def test_fitted_stats_use_only_training_weeks(panel):
    """The subtle leak: encoding a department using validation or test weeks.

    Changing revenue AFTER the training window must not change any fitted
    department statistic.
    """
    base = F.fit_department_stats(panel, DEPTS)

    tampered = panel.copy()
    after_train = tampered.week_no > C.TRAIN_WEEKS[1]
    tampered.loc[after_train, "revenue"] *= 50.0
    after = F.fit_department_stats(tampered, DEPTS)

    assert base.volatility == pytest.approx(after.volatility)
    assert base.persistence == pytest.approx(after.persistence)
    assert base.log_scale == pytest.approx(after.log_scale)


def test_fitted_stats_round_trip(panel, stats):
    restored = F.FittedStats.from_dict(stats.to_dict())
    assert restored.departments == stats.departments
    assert restored.fitted_on_weeks == stats.fitted_on_weeks
    for d in DEPTS:
        assert restored.volatility[d] == pytest.approx(stats.volatility[d], abs=1e-6)


def test_building_features_is_deterministic(panel, stats):
    a = F.build_features(panel, stats)
    b = F.build_features(panel, stats)
    pd.testing.assert_frame_equal(a, b)


def test_extend_for_forecast_adds_one_target_only(panel, stats):
    extended = P.extend_for_forecast(panel, C.LAST_TARGET_WEEK + 1)
    assert extended.week_no.max() == C.LAST_TARGET_WEEK + 1
    # The appended week carries no revenue -- NaN, not zero, because zero is a
    # real observation in this panel.
    added = extended[extended.week_no == C.LAST_TARGET_WEEK + 1]
    assert added.revenue.isna().all()

    f = F.build_features(extended, stats)
    row = f[f.week_no == C.LAST_TARGET_WEEK + 1]
    assert len(row) == len(DEPTS)
    for name in C.FEATURE_NAMES:
        assert not row[name].isna().any(), name


def test_extend_for_forecast_refuses_multi_step(panel):
    with pytest.raises(ValueError, match="one week"):
        P.extend_for_forecast(panel, C.LAST_TARGET_WEEK + 3)


def test_qualifying_departments_uses_training_weeks_only():
    """A department that only trades after the training window must not qualify.

    Otherwise the inclusion rule is selection on the outcome: test-period
    activity would decide which series are modellable.
    """
    def rev(dept, week):
        if dept == "GAMMA":
            return 0.0 if week <= C.TRAIN_WEEKS[1] else 5000.0
        return 800.0

    p = make_panel(revenue=rev)
    quals = P.qualifying_departments(p)
    assert "GAMMA" not in quals
    assert "ALPHA" in quals and "BETA" in quals
