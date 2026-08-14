"""Baselines, metrics, intervals, models and serialisation.

Synthetic data throughout, with expected values computed independently of the
code under test wherever the arithmetic allows it.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from rrip.forecast import baselines as B
from rrip.forecast import contract as C
from rrip.forecast import explain as E
from rrip.forecast import features as F
from rrip.forecast import intervals as I
from rrip.forecast import metrics as M
from rrip.forecast import models as MD
from tests.test_forecast_features import DEPTS, make_panel


@pytest.fixture(scope="module")
def frame() -> pd.DataFrame:
    rng = np.random.default_rng(7)

    def rev(dept, week):
        base = {"ALPHA": 4000.0, "BETA": 700.0, "GAMMA": 90.0}[dept]
        season = 1.0 + 0.15 * np.sin(2 * np.pi * week / 4)
        return float(max(base * season * (1 + 0.2 * rng.standard_normal()), 0.0))

    p = make_panel(revenue=rev)
    return F.build_features(p, F.fit_department_stats(p, DEPTS))


# --- metrics ----------------------------------------------------------------

def test_perfect_forecast_scores_zero():
    y = np.array([10.0, 20.0, 30.0])
    s = M.score(y, y)
    assert s.mae == 0 and s.rmse == 0 and s.wape == 0 and s.smape == 0


def test_wape_is_the_pooled_ratio():
    y = np.array([100.0, 200.0, 300.0])
    p = np.array([110.0, 180.0, 330.0])
    # |10| + |20| + |30| = 60 over 600
    assert M.score(y, p).wape == pytest.approx(10.0)


def test_score_rejects_non_finite_predictions():
    with pytest.raises(ValueError, match="NaN or inf"):
        M.score([1.0, 2.0], [1.0, np.nan])


def test_score_rejects_mismatched_shapes():
    with pytest.raises(ValueError, match="shape mismatch"):
        M.score([1.0, 2.0], [1.0])


def test_smape_is_defined_when_both_are_zero():
    """A correct prediction of nothing is not an error."""
    s = M.score([0.0, 100.0], [0.0, 100.0])
    assert s.smape == 0.0


def test_macro_wape_differs_from_pooled_when_sizes_differ():
    frame = pd.DataFrame({
        "department": ["BIG"] * 3 + ["SMALL"] * 3,
        "actual": [1000.0, 1000.0, 1000.0, 10.0, 10.0, 10.0],
        "pred": [1050.0, 1050.0, 1050.0, 15.0, 15.0, 15.0],
    })
    per = M.score_by(frame, "actual", "pred", "department")
    pooled = M.score(frame.actual, frame.pred).wape
    macro = M.macro_wape(per)
    assert pooled == pytest.approx(5.446, abs=0.01)
    assert macro == pytest.approx(27.5, abs=0.01)
    assert macro > pooled, (
        "macro must expose the small series that pooled WAPE hides")


def test_diebold_mariano_reports_no_difference_for_identical_forecasts():
    y = np.array([10.0, 12.0, 9.0, 11.0, 13.0, 8.0])
    d = M.diebold_mariano(y, y * 1.1, y * 1.1)
    assert not np.isfinite(d["statistic"]) or d["mean_loss_diff"] == 0


def test_diebold_mariano_detects_a_clear_difference():
    rng = np.random.default_rng(3)
    y = rng.normal(100, 10, 200)
    good = y + rng.normal(0, 1, 200)
    bad = y + rng.normal(0, 20, 200)
    d = M.diebold_mariano(y, good, bad)
    assert d["mean_loss_diff"] < 0
    assert d["p_value"] < 0.01


# --- baselines --------------------------------------------------------------

def test_trailing_mean_is_exactly_the_scale(frame):
    """The identity the whole ratio parameterisation rests on."""
    assert np.allclose(B.trailing_mean(frame), B.clipped_scale(frame))


def test_naive_recovers_the_previous_week(frame):
    expected = frame.rev_lag1_ratio.to_numpy(float) * B.clipped_scale(frame)
    assert np.allclose(B.naive(frame), expected)


def test_seasonal_naive_uses_the_measured_lag(frame):
    expected = frame.rev_lag4_ratio.to_numpy(float) * B.clipped_scale(frame)
    assert np.allclose(B.seasonal_naive(frame), expected)


def test_drift_never_predicts_negative_revenue():
    def rev(dept, week):
        return max(5000.0 - 60.0 * week, 0.0)   # falls hard

    p = make_panel(revenue=rev)
    f = F.build_features(p, F.fit_department_stats(p, DEPTS))
    assert (B.drift(f) >= 0).all()


def test_every_baseline_returns_finite_values_of_the_right_length(frame):
    for name, fn in B.BASELINES.items():
        out = fn(frame)
        assert len(out) == len(frame), name
        assert np.isfinite(out).all(), name


def test_primary_baseline_is_registered():
    assert B.PRIMARY_BASELINE in B.BASELINES


# --- models -----------------------------------------------------------------

def _fit(model, frame):
    return model.fit(F.feature_matrix(frame),
                     frame.target_ratio.to_numpy(float),
                     B.clipped_scale(frame))


def test_ridge_predictions_are_deterministic(frame):
    a = _fit(MD.RidgeRatio(alpha=1.0), frame).predict(F.feature_matrix(frame))
    b = _fit(MD.RidgeRatio(alpha=1.0), frame).predict(F.feature_matrix(frame))
    assert np.allclose(a, b)


def test_gradient_boosting_is_deterministic_under_its_seed(frame):
    a = _fit(MD.GradientBoostRatio(), frame).predict(F.feature_matrix(frame))
    b = _fit(MD.GradientBoostRatio(), frame).predict(F.feature_matrix(frame))
    assert np.allclose(a, b)


def test_linear_spec_round_trips_and_scores_identically(frame):
    model = _fit(MD.RidgeRatio(alpha=1.0), frame)
    spec = MD.LinearSpec.from_dict(model.spec.to_dict())
    assert MD.is_finite_spec(spec)

    row = frame.iloc[10]
    values = {n: float(row[n]) for n in C.FEATURE_NAMES}
    vector = model.predict(F.feature_matrix(frame.iloc[[10]]))[0]
    assert spec.score_one(values) == pytest.approx(vector, rel=1e-9)


def test_linear_contributions_sum_to_the_prediction(frame):
    """The exactness claim the explanation layer makes for linear models."""
    model = _fit(MD.RidgeRatio(alpha=1.0), frame)
    spec = model.spec
    row = frame.iloc[25]
    values = {n: float(row[n]) for n in C.FEATURE_NAMES}
    total = spec.intercept + sum(v for _, v in spec.contributions(values))
    assert total == pytest.approx(spec.score_one(values), rel=1e-12)


def test_linear_spec_requires_every_feature_by_name(frame):
    """A reordered vector produces a plausible wrong number; a missing name raises."""
    model = _fit(MD.RidgeRatio(alpha=1.0), frame)
    values = {n: 0.0 for n in C.FEATURE_NAMES}
    del values[C.FEATURE_NAMES[3]]
    with pytest.raises(KeyError, match=C.FEATURE_NAMES[3]):
        model.spec.score_one(values)


def test_winsorisation_bounds_come_from_training_not_from_the_input(frame):
    """Bounds are fitted once and stored, so test rows are clipped with them."""
    model = _fit(MD.RidgeRatio(alpha=1.0), frame)
    spec = model.spec
    extreme = {n: 1e9 for n in C.FEATURE_NAMES}
    clipped = {n: float(u) for n, u in zip(spec.feature_names, spec.upper,
                                           strict=True)}
    assert spec.score_one(extreme) == pytest.approx(spec.score_one(clipped),
                                                     rel=1e-9)


def test_to_dollars_floors_at_zero():
    out = MD.to_dollars(np.array([-5.0, 0.5, 2.0]), np.array([100.0, 100.0, 100.0]))
    assert list(out) == [0.0, 50.0, 200.0]


def test_unfitted_models_refuse_to_predict():
    with pytest.raises(RuntimeError, match="not fitted"):
        MD.RidgeRatio().predict(np.zeros((1, len(C.FEATURE_NAMES))))
    with pytest.raises(RuntimeError, match="not fitted"):
        MD.GradientBoostRatio().predict(np.zeros((1, len(C.FEATURE_NAMES))))


def test_candidate_names_are_unique():
    names = [m.name for m in MD.candidates()]
    assert len(set(names)) == len(names)


# --- intervals --------------------------------------------------------------

def test_conformal_coverage_is_close_to_nominal_on_exchangeable_data():
    """The guarantee holds when its assumption does. This is the sanity check."""
    rng = np.random.default_rng(5)
    n = 2000
    scale = rng.uniform(50, 5000, n)
    point = scale.copy()
    actual = point + rng.normal(0, 0.2, n) * scale

    ci = I.calibrate(actual[:1000], point[:1000], scale[:1000], 0.80, (1, 2))
    lo, hi = ci.apply(point[1000:], scale[1000:])
    cov = I.coverage(actual[1000:], lo, hi)
    assert 0.76 <= cov["coverage"] <= 0.84


def test_intervals_are_asymmetric_when_residuals_are_skewed():
    rng = np.random.default_rng(9)
    n = 1000
    scale = np.full(n, 100.0)
    point = scale.copy()
    actual = point + np.abs(rng.normal(0, 30, n))   # only ever above
    ci = I.calibrate(actual, point, scale, 0.80, (1, 2))
    assert ci.upper_q > 0
    assert abs(ci.upper_q) > abs(ci.lower_q)


def test_lower_bound_is_floored_at_zero():
    ci = I.ConformalIntervals(0.8, -5.0, 5.0, 100, (1, 2))
    lo, hi = ci.apply(np.array([10.0]), np.array([100.0]))
    assert lo[0] == 0.0
    assert hi[0] == 510.0


def test_calibration_refuses_a_useless_sample():
    with pytest.raises(ValueError, match="usable sample"):
        I.calibrate(np.ones(5), np.ones(5), np.ones(5), 0.8, (1, 2))


def test_calibration_rejects_an_impossible_level():
    with pytest.raises(ValueError, match="level"):
        I.calibrate(np.ones(50), np.ones(50), np.ones(50), 1.5, (1, 2))


def test_intervals_round_trip():
    ci = I.ConformalIntervals(0.8, -0.31, 0.29, 322, (74, 87))
    back = I.ConformalIntervals.from_dict(ci.to_dict())
    assert back == ci


def test_wider_level_gives_wider_interval():
    rng = np.random.default_rng(4)
    n = 800
    scale = np.full(n, 200.0)
    point = scale.copy()
    actual = point + rng.normal(0, 40, n)
    narrow = I.calibrate(actual, point, scale, 0.80, (1, 2))
    wide = I.calibrate(actual, point, scale, 0.95, (1, 2))
    assert (wide.upper_q - wide.lower_q) > (narrow.upper_q - narrow.lower_q)


# --- explanation ------------------------------------------------------------

def test_permutation_importance_is_reproducible(frame):
    model = _fit(MD.GradientBoostRatio(), frame)
    a = E.permutation_importance(model, frame, repeats=3)
    b = E.permutation_importance(model, frame, repeats=3)
    assert [i.feature for i in a] == [i.feature for i in b]
    assert [i.importance for i in a] == pytest.approx([i.importance for i in b])


def test_every_feature_has_a_human_label():
    for name in C.FEATURE_NAMES:
        assert E.label(name) != name, f"{name} has no human-readable label"


def test_tree_contributions_are_marked_non_additive(frame):
    model = _fit(MD.GradientBoostRatio(), frame)
    row = frame.iloc[30]
    values = {n: float(row[n]) for n in C.FEATURE_NAMES}
    contribs, additive = E.local_contributions(
        model, values, float(row.scale_usd), E.reference_values(frame))
    assert additive is False, (
        "reference ablation is not a decomposition and must not claim to be")
    assert len(contribs) == 5


def test_linear_contributions_are_marked_additive(frame):
    model = _fit(MD.RidgeRatio(alpha=1.0), frame)
    row = frame.iloc[30]
    values = {n: float(row[n]) for n in C.FEATURE_NAMES}
    _, additive = E.local_contributions(
        model, values, float(row.scale_usd), E.reference_values(frame))
    assert additive is True
