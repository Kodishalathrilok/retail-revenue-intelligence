"""Input bounds and configuration guards added in the abuse/failure pass.

Each pins a way a malformed input or a config typo used to turn into a 500, a
silent mode change, or a drained model budget. None of these needs a database:
the rejection happens before any query runs.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from rrip.api import limits
from rrip.api.main import app
from rrip.config import Settings, settings


def _client() -> TestClient:
    # No `with`: the lifespan opens the pool, and every check below is refused
    # before a handler touches the database.
    return TestClient(app, raise_server_exceptions=False)


# --- RRIP_TIER: a typo must fail loudly, not silently mean "local" --------------

def test_an_unknown_tier_is_rejected_at_startup(monkeypatch) -> None:
    monkeypatch.setenv("RRIP_TIER", "production")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_a_tier_with_odd_casing_or_spacing_is_normalised(monkeypatch) -> None:
    monkeypatch.setenv("RRIP_TIER", "Published ")
    assert Settings(_env_file=None).is_published


# --- malformed inputs that used to 500 ------------------------------------------

def test_pareto_rejects_a_malformed_cursor() -> None:
    from rrip.api.models import Cursor

    # A value the server never issues (not an integer rank) decoded cleanly and
    # then raised on int() -> 500. And a cursor that is not valid base64/JSON.
    bad = Cursor(last_values=["not-a-rank"]).encode()
    assert _client().get(f"/api/v1/products/pareto?cursor={bad}").status_code == 400
    assert _client().get("/api/v1/products/pareto?cursor=@@@").status_code == 400


@pytest.mark.parametrize("z", ["nan", "999", "-1"])
def test_anomalies_rejects_an_out_of_range_threshold(z: str) -> None:
    r = _client().get(f"/api/v1/ai/anomalies?z_threshold={z}")
    assert r.status_code == 422


def test_ask_rejects_an_oversized_question(monkeypatch) -> None:
    # Protection off so the request reaches body validation rather than the
    # limiter; the 500-character bound is what must reject it.
    monkeypatch.setattr(settings, "limiter_dsn", "")
    monkeypatch.setattr(settings, "tier", "local")
    r = _client().post("/api/v1/ai/ask", json={"question": "x" * 600})
    assert r.status_code == 422


# --- the provider cannot be chosen by a client on a public deployment -----------

def test_provider_cannot_be_selected_on_the_published_tier(monkeypatch) -> None:
    monkeypatch.setattr(settings, "tier", "published")
    monkeypatch.setattr(limits, "mode", lambda: limits.OFF)   # isolate the provider check
    r = _client().post(
        "/api/v1/ai/query?provider=fake",
        json={"question": "Which 5 departments have the highest total revenue?"})
    assert r.status_code == 400


def test_provider_can_still_be_selected_on_the_local_tier(monkeypatch) -> None:
    from rrip.api import ai_routes

    monkeypatch.setattr(settings, "tier", "local")
    # No exception means _provider accepted the override; it returns a provider.
    assert ai_routes._provider("fake") is not None
