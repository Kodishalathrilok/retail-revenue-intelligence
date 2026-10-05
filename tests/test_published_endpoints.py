"""Endpoints that must behave on the published tier, where the scientific stack
(statsmodels, pandas, numpy) is deliberately not installed.

GET /api/v1/causal/validation imported rrip.ai.causal_validate unconditionally
and returned 500 in production. On the published tier it must now refuse
explicitly, without ever reaching the simulation code.
"""

from __future__ import annotations

import sys
import types

import pytest
from fastapi.testclient import TestClient

from rrip.api.db import readonly_dsn
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


# --- edge caching: shared cache for the published read-only dashboards ----------

def test_post_responses_are_never_edge_cached(monkeypatch) -> None:
    from rrip.api import limits

    monkeypatch.setattr(settings, "tier", "published")
    monkeypatch.setattr(limits, "mode", lambda: limits.OFF)
    r = TestClient(app, raise_server_exceptions=False).post(
        "/api/v1/ai/query", json={"question": "Delete every transaction from the database"})
    assert r.status_code == 200
    assert "s-maxage" not in r.headers.get("cache-control", "")


def test_published_dashboard_gets_are_edge_cacheable_but_health_and_status_are_not(
        monkeypatch) -> None:
    if not readonly_dsn():
        pytest.skip("no RRIP_PG_READONLY_DSN")
    monkeypatch.setattr(settings, "tier", "published")
    with TestClient(app) as c:                       # lifespan opens the pool
        cached = c.get("/api/v1/departments")
        assert cached.status_code == 200
        assert "s-maxage=300" in cached.headers.get("cache-control", "")
        # Readiness must be live, and the protection-mode report must never be
        # served from a cache -- a stale "on" would hide a broken deployment.
        assert "s-maxage" not in c.get("/health").headers.get("cache-control", "")
        assert "s-maxage" not in c.get("/api/v1/ai/status").headers.get("cache-control", "")
