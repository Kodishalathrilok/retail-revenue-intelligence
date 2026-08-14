"""Serving, governance and provenance.

Most of these build a synthetic artifact in a tmp_path rather than depending on
the trained one, so they run in CI without a database. The tests that DO need
the real artifact skip visibly when it is absent -- a test that silently passes
when it cannot check anything is the failure it exists to prevent, which is the
same rule tests/test_readonly_role.py follows.
"""

from __future__ import annotations

import json

import pytest

from rrip.forecast import contract as C
from rrip.forecast import registry as R
from rrip.forecast import service as FS

DEPTS = ["ALPHA", "BETA", "TINY"]
FORECAST_WEEK = C.LAST_TARGET_WEEK + 1


def _metadata(**over) -> dict:
    base = {
        "model_type": "trailing_mean_4",
        "hyperparameters": {"window_weeks": 4},
        "random_seed": 1,
        "trained_at": "2026-08-14T00:00:00+00:00",
        "git_sha": "abcdef1234567890",
        "contract_version": C.CONTRACT_VERSION,
        "feature_version": C.FEATURE_VERSION,
        "contract": C.CONTRACT.to_dict(),
        "dataset": {"rows": 100},
        "splits": {},
        "department_stats": {},
        "selection": {},
        "deployment": {"deployed_kind": "baseline",
                       "deployed_predictor": "trailing_mean_4",
                       "rationale": "baseline wins"},
        "challenger": {"name": "hgb"},
        "metrics": {"test": {"scores": {"wape": 10.0},
                             "per_department": {"ALPHA": {"wape": 8.0},
                                                "BETA": {"wape": 22.0},
                                                "TINY": {"wape": 60.0}}}},
        "conformal": {"calibration": {"0.80": {"level": 0.8,
                                               "calibration_weeks": [74, 87]}},
                      "test_coverage": {"0.80": {"coverage": 0.81}}},
        "importance": [{"label": "Last week's revenue", "importance": 3.0}],
        "reference_values": {},
        "robustness": {"time_drift": {"drift_pp": 0.2}},
        "leakage_audit": {"passed": True},
    }
    base.update(over)
    return base


def _serving_csv(rows: list[dict]) -> str:
    cols = ["department", "week_no", "split", "revenue", "scale_usd",
            "prediction", "lower", "upper", "baseline_prediction",
            "baseline_name", "model_prediction", "servable", "is_partial_week",
            "input_week_lag1_usd", "input_week_lag2_usd",
            "input_week_lag3_usd", "input_week_lag4_usd"]
    lines = [",".join(cols)]
    for r in rows:
        lines.append(",".join(str(r.get(c, "")) for c in cols))
    return "\n".join(lines) + "\n"


def _row(dept, week, *, actual="", pred=1000.0, scale=4000.0, servable=True,
         partial=False, split="test"):
    return {"department": dept, "week_no": week, "split": split,
            "revenue": actual, "scale_usd": scale, "prediction": pred,
            "lower": pred * 0.7, "upper": pred * 1.3,
            "baseline_prediction": pred, "baseline_name": "trailing_mean_4",
            "model_prediction": pred * 1.01, "servable": servable,
            "is_partial_week": partial,
            "input_week_lag1_usd": pred, "input_week_lag2_usd": pred,
            "input_week_lag3_usd": pred, "input_week_lag4_usd": pred}


@pytest.fixture
def artifact(tmp_path):
    rows = []
    for dept, scale in (("ALPHA", 4000.0), ("BETA", 300.0), ("TINY", 2.0)):
        for wk in (C.TEST_WEEKS[1] - 1, C.TEST_WEEKS[1]):
            rows.append(_row(dept, wk, actual=scale * 1.02, pred=scale,
                             scale=scale, servable=scale >= 5))
        rows.append(_row(dept, FORECAST_WEEK, actual="", pred=scale,
                         scale=scale, servable=scale >= 5, partial=True,
                         split="none"))

    (tmp_path / "metadata.json").write_text(json.dumps(_metadata()),
                                            encoding="utf-8")
    (tmp_path / "serving.csv").write_text(_serving_csv(rows), encoding="utf-8")
    FS.load_store(tmp_path, force=True)
    yield tmp_path
    FS._store = None


# --- happy path -------------------------------------------------------------

def test_forecast_defaults_to_the_week_after_the_last_observed(artifact):
    out = FS.forecast("ALPHA", directory=artifact)
    assert out["forecast_week"] == FORECAST_WEEK
    assert out["forecast_origin_week"] == FORECAST_WEEK - 1
    assert out["horizon_weeks"] == 1


def test_payload_carries_the_fields_the_contract_promises(artifact):
    out = FS.forecast("ALPHA", directory=artifact)
    for key in ("target", "unit", "department", "prediction", "lower_bound",
                "upper_bound", "interval", "baseline", "confidence",
                "confidence_reasons", "caveats", "explanation",
                "model_version", "trained_until_week", "accuracy"):
        assert key in out, key
    assert out["target"] == C.TARGET_NAME
    assert out["model_version"].startswith(C.CONTRACT_VERSION)


def test_prediction_lies_inside_its_own_interval(artifact):
    out = FS.forecast("ALPHA", directory=artifact)
    assert out["lower_bound"] <= out["prediction"] <= out["upper_bound"]


def test_serving_is_deterministic(artifact):
    a = FS.forecast("ALPHA", directory=artifact)
    b = FS.forecast("ALPHA", directory=artifact)
    assert a == b


def test_baseline_explanation_is_arithmetic_and_additive(artifact):
    out = FS.forecast("ALPHA", directory=artifact)
    exp = out["explanation"]
    assert exp["method"] == "arithmetic"
    assert exp["additive"] is True
    total = sum(c["value_usd"] * c["weight"] for c in exp["contributors"])
    assert total == pytest.approx(out["prediction"], abs=0.02)


# --- governance -------------------------------------------------------------

def test_unknown_department_lists_the_real_ones(artifact):
    with pytest.raises(FS.ForecastRequestError) as exc:
        FS.forecast("BAKERY", directory=artifact)
    d = exc.value.to_dict()
    assert d["error"] == "UNKNOWN_DEPARTMENT"
    assert d["available_departments"] == sorted(DEPTS)


@pytest.mark.parametrize("horizon", [0, 2, 4, 52])
def test_unsupported_horizon_is_refused(artifact, horizon):
    with pytest.raises(FS.ForecastRequestError) as exc:
        FS.forecast("ALPHA", horizon=horizon, directory=artifact)
    assert exc.value.code == "UNSUPPORTED_HORIZON"
    assert exc.value.to_dict()["supported_horizons"] == list(C.SUPPORTED_HORIZONS)


def test_insufficient_history_is_refused_not_extrapolated(artifact):
    with pytest.raises(FS.ForecastRequestError) as exc:
        FS.forecast("TINY", directory=artifact)
    d = exc.value.to_dict()
    assert d["error"] == "INSUFFICIENT_HISTORY"
    assert "Insufficient history" in d["message"]
    assert d["minimum_scale_usd"] == C.MIN_SERVABLE_SCALE_USD


def test_week_outside_the_stored_range_is_refused(artifact):
    with pytest.raises(FS.ForecastRequestError) as exc:
        FS.forecast("ALPHA", week=5, directory=artifact)
    assert exc.value.code == "OUT_OF_RANGE"


def test_missing_artifact_is_unavailable_not_a_crash(tmp_path):
    FS._store = None
    with pytest.raises(FS.ForecastUnavailable, match="no forecast artifact"):
        FS.load_store(tmp_path / "nothing", force=True)


def test_a_stale_contract_version_is_refused(tmp_path):
    (tmp_path / "metadata.json").write_text(
        json.dumps(_metadata(contract_version="forecast_v0")), encoding="utf-8")
    (tmp_path / "serving.csv").write_text(
        _serving_csv([_row("ALPHA", FORECAST_WEEK)]), encoding="utf-8")
    FS._store = None
    with pytest.raises(FS.ForecastUnavailable, match="contract"):
        FS.load_store(tmp_path, force=True)


def test_a_stale_feature_version_is_refused(tmp_path):
    """The silent failure: stored coefficients times a reordered matrix."""
    (tmp_path / "metadata.json").write_text(
        json.dumps(_metadata(feature_version="0.0.1")), encoding="utf-8")
    (tmp_path / "serving.csv").write_text(
        _serving_csv([_row("ALPHA", FORECAST_WEEK)]), encoding="utf-8")
    FS._store = None
    with pytest.raises(FS.ForecastUnavailable, match="feature version"):
        FS.load_store(tmp_path, force=True)


# --- confidence tiering -----------------------------------------------------

def test_confidence_separates_departments_by_measured_error(artifact):
    """REGRESSION: an earlier version returned LIMITED for every department.

    Week-level caveats had been folded into the confidence flag, so week 102
    being partial made all 23 departments LIMITED and the flag carried no
    information. Confidence must reflect THIS department's measured error only.
    """
    alpha = FS.forecast("ALPHA", directory=artifact)      # 8.0 vs 10.0 pooled
    beta = FS.forecast("BETA", directory=artifact)        # 22.0 -> 2.2x
    assert alpha["confidence"] == FS.CONFIDENCE_NORMAL
    assert alpha["confidence_reasons"] == []
    assert beta["confidence"] == FS.CONFIDENCE_LIMITED
    assert beta["confidence_reasons"]


def test_week_level_caveats_are_separate_from_confidence(artifact):
    out = FS.forecast("ALPHA", directory=artifact)
    assert out["confidence"] == FS.CONFIDENCE_NORMAL
    assert any("partial week" in c for c in out["caveats"]), (
        "a 6-day target week must be flagged, but as a caveat rather than by "
        "collapsing the per-department confidence tier")


def test_low_confidence_for_a_department_far_above_pooled_error(tmp_path):
    meta = _metadata()
    meta["metrics"]["test"]["per_department"]["BETA"]["wape"] = 90.0
    (tmp_path / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
    # An observed week is needed as well as the forecast week: the default
    # target is derived from the last week that HAS an actual.
    (tmp_path / "serving.csv").write_text(
        _serving_csv([_row("BETA", C.TEST_WEEKS[1], actual=310.0, scale=300.0),
                      _row("BETA", FORECAST_WEEK, scale=300.0)]),
        encoding="utf-8")
    FS._store = None
    out = FS.forecast("BETA", directory=tmp_path)
    assert out["confidence"] == FS.CONFIDENCE_LOW
    FS._store = None


# --- history and summary ----------------------------------------------------

def test_history_marks_which_points_are_forecasts(artifact):
    h = FS.history("ALPHA", weeks=10, directory=artifact)
    assert h["department"] == "ALPHA"
    forecasts = [p for p in h["series"] if p["is_forecast"]]
    assert len(forecasts) == 1
    assert forecasts[0]["week_no"] == FORECAST_WEEK
    assert forecasts[0]["actual"] is None


def test_summary_reports_what_is_deployed_and_why(artifact):
    s = FS.summary(directory=artifact)
    assert s["deployed_kind"] == "baseline"
    assert s["deployment_rationale"]
    assert s["leakage_audit_passed"] is True
    assert s["departments"] == sorted(DEPTS)


# --- the real artifact, when one exists -------------------------------------

def _real_or_skip():
    FS._store = None
    try:
        return FS.load_store(R.MODEL_DIR, force=True)
    except FS.ForecastUnavailable as exc:
        pytest.skip(f"no trained forecast artifact: {exc}")


def test_real_artifact_serves_every_qualifying_department():
    store = _real_or_skip()
    served = refused = 0
    for dept in store.departments:
        try:
            out = FS.forecast(dept, directory=R.MODEL_DIR)
            assert out["lower_bound"] <= out["prediction"] <= out["upper_bound"]
            assert out["confidence"] in (FS.CONFIDENCE_NORMAL,
                                         FS.CONFIDENCE_LIMITED,
                                         FS.CONFIDENCE_LOW)
            served += 1
        except FS.ForecastRequestError as exc:
            assert exc.code == "INSUFFICIENT_HISTORY"
            refused += 1
    assert served >= 15, "most departments should be servable"
    FS._store = None


def test_real_artifact_confidence_is_not_uniform():
    """The regression guard on real data: the tiering must actually separate."""
    store = _real_or_skip()
    levels = set()
    for dept in store.departments:
        try:
            levels.add(FS.forecast(dept, directory=R.MODEL_DIR)["confidence"])
        except FS.ForecastRequestError:
            continue
    assert len(levels) > 1, (
        f"every servable department returned {levels}; a confidence flag with "
        "one value carries no information")
    FS._store = None


def test_real_artifact_metadata_carries_full_provenance():
    _real_or_skip()
    meta = R.load_metadata(R.MODEL_DIR)
    assert meta.git_sha and meta.git_sha != ""
    assert meta.trained_at
    assert meta.random_seed
    assert meta.contract_version == C.CONTRACT_VERSION
    assert meta.feature_version == C.FEATURE_VERSION
    assert meta.deployment["deployed_predictor"]
    assert meta.deployment["rule"]
    assert meta.leakage_audit["passed"] is True
    assert meta.dataset["inclusion_rule"]
    assert meta.splits["blocks"]
    FS._store = None
