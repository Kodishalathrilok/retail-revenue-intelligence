"""Forecast HTTP surface: status codes, error bodies and the NL dispatch.

The router is exercised through FastAPI's TestClient against the real artifact
when one exists. Every governance refusal has a distinct status AND a
machine-readable `error` code, because the caller most likely to hit one is the
NL layer, and "insufficient history" has to be distinguishable from "unknown
department" by something more reliable than matching on prose.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from rrip.api import forecast_routes
from rrip.forecast import registry as R
from rrip.forecast import service as FS


@pytest.fixture(scope="module")
def client():
    FS._store = None
    try:
        FS.load_store(R.MODEL_DIR, force=True)
    except FS.ForecastUnavailable as exc:
        pytest.skip(f"no trained forecast artifact: {exc}")

    app = FastAPI()
    app.include_router(forecast_routes.router)
    with TestClient(app) as c:
        yield c
    FS._store = None


def test_forecast_returns_a_complete_payload(client):
    r = client.get("/api/v1/forecast", params={"department": "GROCERY"})
    assert r.status_code == 200
    body = r.json()
    assert body["target"] == "weekly_department_revenue"
    assert body["department"] == "GROCERY"
    assert body["horizon_weeks"] == 1
    assert body["lower_bound"] <= body["prediction"] <= body["upper_bound"]
    assert body["interval"]["kind"] == "prediction interval"
    assert body["model_version"]
    assert body["baseline"]["name"]
    assert isinstance(body["caveats"], list)


def test_unknown_department_is_404_with_the_list(client):
    r = client.get("/api/v1/forecast", params={"department": "BAKERY"})
    assert r.status_code == 404
    detail = r.json()["detail"]
    assert detail["error"] == "UNKNOWN_DEPARTMENT"
    assert "GROCERY" in detail["available_departments"]


def test_unsupported_horizon_is_422(client):
    r = client.get("/api/v1/forecast",
                   params={"department": "GROCERY", "horizon": 3})
    assert r.status_code == 422
    assert r.json()["detail"]["error"] == "UNSUPPORTED_HORIZON"


def test_out_of_range_week_is_422(client):
    r = client.get("/api/v1/forecast",
                   params={"department": "GROCERY", "week": 400})
    assert r.status_code == 422
    assert r.json()["detail"]["error"] == "OUT_OF_RANGE"


def test_insufficient_history_is_422_not_404(client):
    """A real department the data cannot support is not an unknown department.

    Collapsing this to 404 would send someone looking for a spelling mistake.
    """
    r = client.get("/api/v1/forecast", params={"department": "GARDEN CENTER"})
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["error"] == "INSUFFICIENT_HISTORY"
    assert "Insufficient history" in detail["message"]


def test_departments_endpoint_carries_measured_error(client):
    r = client.get("/api/v1/forecast/departments")
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) >= 15
    by_name = {i["department"]: i for i in items}
    assert by_name["GROCERY"]["test_wape"] is not None
    assert by_name["GARDEN CENTER"]["servable"] is False


def test_history_marks_the_forecast_point(client):
    r = client.get("/api/v1/forecast/history/GROCERY", params={"weeks": 20})
    assert r.status_code == 200
    series = r.json()["series"]
    assert len(series) == 20
    assert sum(p["is_forecast"] for p in series) == 1
    assert series[-1]["is_forecast"] is True
    for p in series:
        assert p["lower_bound"] <= p["prediction"] <= p["upper_bound"]


def test_summary_reports_the_deployment_decision(client):
    r = client.get("/api/v1/forecast/summary")
    assert r.status_code == 200
    body = r.json()
    assert body["deployed_kind"] in ("baseline", "model")
    assert body["deployment_rationale"]
    assert body["leakage_audit_passed"] is True
    assert body["horizon_weeks"] == [1]


def test_missing_artifact_returns_503(monkeypatch):
    """Not 500. The service is fine; it has no model to serve."""
    def boom(*a, **kw):
        raise FS.ForecastUnavailable("no artifact")

    monkeypatch.setattr(FS, "forecast", boom)
    app = FastAPI()
    app.include_router(forecast_routes.router)
    with TestClient(app) as c:
        r = c.get("/api/v1/forecast", params={"department": "GROCERY"})
    assert r.status_code == 503
    detail = r.json()["detail"]
    assert detail["error"] == "MODEL_UNAVAILABLE"
    assert "forecast-train" in detail["remedy"]


# --- the NL dispatch --------------------------------------------------------

@pytest.fixture(scope="module")
def ai_client():
    FS._store = None
    try:
        FS.load_store(R.MODEL_DIR, force=True)
    except FS.ForecastUnavailable as exc:
        pytest.skip(f"no trained forecast artifact: {exc}")

    from rrip.api import ai_routes
    app = FastAPI()
    app.include_router(ai_routes.router)
    with TestClient(app) as c:
        yield c
    FS._store = None


def test_ask_routes_a_predictive_question_to_the_model(ai_client):
    r = ai_client.post("/api/v1/ai/ask",
                       json={"question": "What is next week's expected "
                                         "Grocery revenue?"})
    assert r.status_code == 200
    body = r.json()
    assert body["route"] == "forecast"
    assert body["succeeded"] is True
    assert body["routing"]["verdict"] == "FORECAST"
    assert body["intent"]["department"] == "GROCERY"
    assert body["forecast"]["prediction"] > 0
    assert body["intent"]["resolved_by"] == "deterministic"


def test_ask_sends_a_descriptive_question_to_sql(ai_client):
    r = ai_client.post("/api/v1/ai/ask",
                       json={"question": "What is the total revenue by "
                                         "department?"})
    assert r.status_code == 200
    body = r.json()
    assert body["route"] == "sql"
    assert body["routing"]["verdict"] == "ANSWERABLE"
    assert "forecast" not in body


def test_ask_refuses_an_unmodelled_metric(ai_client):
    r = ai_client.post("/api/v1/ai/ask",
                       json={"question": "Predict how many units we will sell "
                                         "next week."})
    body = r.json()
    assert body["route"] == "sql"
    assert body["routing"]["verdict"] == "UNSUPPORTED"
    assert body["routing"]["rule"] == "forecast_scope"


def test_ask_asks_which_department_rather_than_guessing(ai_client):
    r = ai_client.post("/api/v1/ai/ask",
                       json={"question": "Forecast next week's revenue."})
    body = r.json()
    assert body["succeeded"] is False
    assert body["error"] == "DEPARTMENT_NOT_IDENTIFIED"
    assert body["available_departments"]


def test_ask_surfaces_a_governance_refusal_intact(ai_client):
    r = ai_client.post("/api/v1/ai/ask",
                       json={"question": "Forecast Grocery revenue 3 weeks "
                                         "ahead."})
    body = r.json()
    assert body["succeeded"] is False
    assert body["error"] == "UNSUPPORTED_HORIZON"
    assert body["intent"]["horizon"] == 3


def test_ask_requires_a_question(ai_client):
    assert ai_client.post("/api/v1/ai/ask", json={}).status_code == 400


# --- the serverless constraint ----------------------------------------------

def test_the_api_does_not_import_the_scientific_stack():
    """The deployment claim, checked rather than asserted.

    The hosted API is a Vercel Python function under a 250 MB limit; the
    scientific stack is 332.9 MB. Adding the forecast router would have broken
    that if rrip.forecast.service imported pandas or scikit-learn, and it would
    have broken it at deploy time, in a build log, long after the code looked
    fine locally.

    Run in a subprocess because the test session itself has already imported
    pandas for the other forecast tests, so sys.modules here proves nothing.
    """
    import subprocess
    import sys

    probe = (
        "import sys; import rrip.api.main;"
        "heavy=[m for m in ('pandas','numpy','sklearn','scipy','statsmodels',"
        "'joblib','matplotlib','dowhy') if m in sys.modules];"
        "print(','.join(heavy))"
    )
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True,
                         text=True, check=True, timeout=120)
    assert out.stdout.strip() == "", (
        f"importing the API pulled in {out.stdout.strip()}, which would push "
        "the serverless bundle over its limit")
