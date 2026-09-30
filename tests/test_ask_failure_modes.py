"""/api/v1/ai/ask when the model is needed: every way it can go.

The model is consulted only when the deterministic matcher cannot tell which
department a forecast question is about. The failure hierarchy is:

    Gemini primary model -> Gemini fallback models (inside one call)
      -> retries (complete())
        -> all failed: an explicit 503 AI_UNAVAILABLE, never "Which department?"

Replying "Which department?" when the provider was down presented an outage as
a flaw in the visitor's question; that is the regression pinned here. No real
quota is spent: providers are FakeProvider, a scripted flaky provider, or
Gemini over an httpx MockTransport.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from rrip.ai import provider as P
from rrip.api import limits
from rrip.api.main import app
from rrip.config import settings
from rrip.forecast import service as FS

NEEDS_MODEL = "What will revenue be next week for the department that sells steak?"
DETERMINISTIC = "What will GROCERY revenue be next week?"
DEPARTMENTS = ["GROCERY", "MEAT", "PRODUCE"]


@pytest.fixture(autouse=True)
def _stub_forecast(monkeypatch):
    monkeypatch.setattr(settings, "limiter_dsn", "")
    monkeypatch.setattr(settings, "tier", "local")
    monkeypatch.setattr(FS, "load_store", lambda *a, **k: SimpleNamespace(departments=DEPARTMENTS))
    monkeypatch.setattr(FS, "forecast", lambda department, horizon=1: {"department": department})

    async def no_sleep(*_):
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)       # retry backoff


def _use(monkeypatch, provider) -> None:
    monkeypatch.setattr(limits, "get_provider", lambda name=None, use_cache=True: provider)


def _ask(question: str = NEEDS_MODEL, origin: str | None = None):
    headers = {"origin": origin} if origin else {}
    return TestClient(app, raise_server_exceptions=False).post(
        "/api/v1/ai/ask", json={"question": question}, headers=headers)


class _FlakyThenMeat(P.LLMProvider):
    name, model = "flaky", "flaky-1"

    def __init__(self):
        super().__init__(api_key="k", use_cache=False)
        self.calls = 0

    async def _call(self, prompt, system, temperature):
        self.calls += 1
        if self.calls == 1:
            raise P.LLMError("transient upstream failure")
        return "MEAT"


def _gemini(monkeypatch, handler) -> P.GeminiProvider:
    real = httpx.AsyncClient

    def client(*a, **kw):
        kw["transport"] = httpx.MockTransport(handler)
        return real(*a, **kw)

    monkeypatch.setattr(P.httpx, "AsyncClient", client)
    return P.GeminiProvider(api_key="test-key", use_cache=False)


def _gemini_ok(text: str) -> httpx.Response:
    return httpx.Response(200, json={"candidates": [
        {"finishReason": "STOP", "content": {"parts": [{"text": text}]}}]})


def _assert_forecast_for(r, department: str) -> None:
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["succeeded"] is True
    assert body["intent"]["department"] == department
    assert body["intent"]["resolved_by"] == "llm"


def test_primary_provider_success(monkeypatch) -> None:
    _use(monkeypatch, P.FakeProvider(["MEAT"]))
    _assert_forecast_for(_ask(), "MEAT")


def test_transient_failure_is_retried_to_success(monkeypatch) -> None:
    p = _FlakyThenMeat()
    _use(monkeypatch, p)
    _assert_forecast_for(_ask(), "MEAT")
    assert p.calls == 2


def test_fallback_model_success(monkeypatch) -> None:
    def handler(req):
        if P.GeminiProvider.MODELS[0] in req.url.path:
            return httpx.Response(503, json={"error": {"status": "UNAVAILABLE"}})
        return _gemini_ok("MEAT")

    _use(monkeypatch, _gemini(monkeypatch, handler))
    _assert_forecast_for(_ask(), "MEAT")


def test_every_provider_failing_is_an_explicit_ai_unavailable(monkeypatch) -> None:
    _use(monkeypatch, _gemini(monkeypatch, lambda req: httpx.Response(503, text="busy")))
    r = _ask()
    assert r.status_code == 503
    body = r.json()
    assert body["error"] == "AI_UNAVAILABLE"
    assert body["available_departments"] == DEPARTMENTS
    assert "Which department" not in r.text
    assert "test-key" not in r.text and "busy" not in r.text


def test_no_configured_key_is_an_explicit_ai_unavailable(monkeypatch) -> None:
    _use(monkeypatch, P.GeminiProvider(api_key=None, use_cache=False))
    r = _ask()
    assert r.status_code == 503 and r.json()["error"] == "AI_UNAVAILABLE"


def test_a_working_model_that_finds_no_department_still_asks(monkeypatch) -> None:
    # The genuine case for the clarification prompt: the model answered NONE.
    _use(monkeypatch, P.FakeProvider(["NONE"]))
    r = _ask()
    assert r.status_code == 200
    assert r.json()["error"] == "DEPARTMENT_NOT_IDENTIFIED"


def test_a_named_department_needs_no_model_at_all(monkeypatch) -> None:
    _use(monkeypatch, P.GeminiProvider(api_key=None, use_cache=False))
    r = _ask(DETERMINISTIC)
    assert r.status_code == 200 and r.json()["forecast"]["department"] == "GROCERY"


def test_daily_cap_refusal(monkeypatch) -> None:
    monkeypatch.setattr(settings, "limiter_dsn", "host=unused")

    async def ok(ip, **k):
        return 1

    async def capped(**k):
        raise limits.DailyCapReached(limits._CAP, retry_after=60)

    monkeypatch.setattr(limits, "hit_ip", ok)
    monkeypatch.setattr(limits, "charge_llm_call", capped)
    _use(monkeypatch, P.FakeProvider(["MEAT"]))
    r = _ask()
    assert r.status_code == 429 and r.json()["error"] == "DAILY_LLM_CAP"


def test_rate_limit_refusal(monkeypatch) -> None:
    monkeypatch.setattr(settings, "limiter_dsn", "host=unused")

    async def over(ip, **k):
        raise limits.RateLimitExceeded(limits._RATE, retry_after=30)

    monkeypatch.setattr(limits, "hit_ip", over)
    r = _ask()
    assert r.status_code == 429 and r.json()["error"] == "RATE_LIMITED"


# --- the published tier: forecasts come from pub_forecast*, never the file store --
# Found by `rrip smoke` against production: /ask read the on-disk model
# artifact, which the Vercel function does not have, so every forecast question
# returned 503 there while GET /api/v1/forecast (tier-aware) worked.

def test_published_ask_never_touches_the_file_store(monkeypatch) -> None:
    from rrip.api import ai_routes, forecast_routes

    monkeypatch.setattr(settings, "tier", "published")

    def no_files(*a, **k):
        raise AssertionError("the published tier has no model artifact on disk")

    async def pub_departments():
        return DEPARTMENTS

    async def pub_forecast(department, week, horizon):
        return {"department": department, "source": "pub_forecast"}

    monkeypatch.setattr(FS, "load_store", no_files)
    monkeypatch.setattr(FS, "forecast", no_files)
    monkeypatch.setattr(ai_routes, "_published_forecast_departments", pub_departments)
    monkeypatch.setattr(forecast_routes, "_published_forecast", pub_forecast)
    # Protection is tested elsewhere; a published tier without a limiter
    # refuses by design, which is not what this test is about.
    monkeypatch.setattr(limits, "mode", lambda: limits.OFF)

    r = _ask(DETERMINISTIC)
    assert r.status_code == 200, r.text
    assert r.json()["forecast"] == {"department": "GROCERY", "source": "pub_forecast"}


def test_published_ask_against_the_real_pub_tables(monkeypatch) -> None:
    """End to end on the published tables in the local database, through the
    API's own read-only connection."""
    from rrip.api.db import readonly_dsn

    if not readonly_dsn():
        pytest.skip("no RRIP_PG_READONLY_DSN")
    monkeypatch.setattr(settings, "tier", "published")
    monkeypatch.setattr(limits, "mode", lambda: limits.OFF)

    def no_files(*a, **k):
        raise AssertionError("the published tier has no model artifact on disk")

    monkeypatch.setattr(FS, "load_store", no_files)
    monkeypatch.setattr(FS, "forecast", no_files)
    with TestClient(app) as c:                       # lifespan opens the pool
        r = c.post("/api/v1/ai/ask", json={"question": DETERMINISTIC})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["succeeded"] is True
    assert body["forecast"]["department"] == "GROCERY"
