"""Endpoints that must behave on the published tier, where the scientific stack
(statsmodels, pandas, numpy) is deliberately not installed.

GET /api/v1/causal/validation imported rrip.ai.causal_validate unconditionally
and returned 500 in production. On the published tier it must now refuse
explicitly, without ever reaching the simulation code.
"""

from __future__ import annotations

import sys
import types

from fastapi.testclient import TestClient

from rrip.api.main import app
from rrip.config import settings


def test_causal_validation_is_an_explicit_local_only_refusal(monkeypatch) -> None:
    monkeypatch.setattr(settings, "tier", "published")

    stub = types.ModuleType("rrip.ai.causal_validate")

    def run_suite():
        raise AssertionError("the published tier must not run the simulation suite")

    stub.run_suite = run_suite
    monkeypatch.setitem(sys.modules, "rrip.ai.causal_validate", stub)

    r = TestClient(app, raise_server_exceptions=False).get("/api/v1/causal/validation")
    assert r.status_code == 503
    detail = r.json()["detail"]
    assert detail["error"] == "LOCAL_ONLY"
    assert "local tier" in detail["message"]


def test_nl_query_reports_the_routers_verdict(monkeypatch) -> None:
    # The Ask page explains a non-answer from the router's verdict. An unsafe
    # question is decided before any model call or database access.
    from rrip.api import limits

    monkeypatch.setattr(limits, "mode", lambda: limits.OFF)
    r = TestClient(app, raise_server_exceptions=False).post(
        "/api/v1/ai/query", json={"question": "Delete every transaction from the database"})
    assert r.status_code == 200
    body = r.json()
    assert body["succeeded"] is False
    assert body["routing"]["verdict"] == "UNSAFE"
    assert body["routing"]["clarification"]
