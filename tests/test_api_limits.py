"""AI endpoint protection: the parts that need no database.

The counters themselves (atomicity, windows, the daily cap under concurrency)
are exercised against real Postgres in test_api_limits_db.py. Here: how a
client is identified, the origin check, the on/off/misconfigured decision,
fail-closed behaviour, the provider's per-call budget hook, and -- through the
real FastAPI app -- that every route able to reach a model is protected and
refuses in one safe, machine-readable shape.
"""

from __future__ import annotations

import asyncio
import inspect

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from starlette.requests import Request

from rrip.ai.provider import CallRefused, FakeProvider, LLMError, LLMProvider
from rrip.api import limits
from rrip.api.main import app
from rrip.config import settings

PROD = "https://rrip-demo.vercel.app"
SECRET_BITS = ("password", "dsn", "rrip_limiter", "traceback", "api_key", "sekrit")


def _request(headers: dict | None = None, client: str = "203.0.113.7") -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request({"type": "http", "method": "POST", "path": "/", "headers": raw,
                    "client": (client, 50000), "query_string": b""})


@pytest.fixture
def protection_on(monkeypatch):
    monkeypatch.setattr(settings, "limiter_dsn", "host=unused")
    monkeypatch.setattr(settings, "cors_origins", f"{PROD},http://localhost:3000")


# --- identifying the client -----------------------------------------------------

def test_ip_comes_from_the_socket_by_default(monkeypatch) -> None:
    monkeypatch.setattr(settings, "trusted_ip_header", "")
    assert limits.client_ip(_request()) == "203.0.113.7"


def test_a_forwarding_header_is_ignored_unless_trusted(monkeypatch) -> None:
    monkeypatch.setattr(settings, "trusted_ip_header", "")
    forged = _request({"x-real-ip": "198.51.100.1", "x-forwarded-for": "198.51.100.2"})
    assert limits.client_ip(forged) == "203.0.113.7"


def test_the_trusted_header_is_used_and_its_first_hop_taken(monkeypatch) -> None:
    monkeypatch.setattr(settings, "trusted_ip_header", "x-forwarded-for")
    r = _request({"x-forwarded-for": "198.51.100.2, 10.0.0.1"})
    assert limits.client_ip(r) == "198.51.100.2"


def test_ipv6_clients_share_a_bucket_per_64(monkeypatch) -> None:
    monkeypatch.setattr(settings, "trusted_ip_header", "")
    a = limits.client_ip(_request(client="2001:db8:1:2::1"))
    b = limits.client_ip(_request(client="2001:db8:1:2:ffff::9"))
    assert a == b == "2001:db8:1:2::/64"


def test_bucket_keys_are_distinct_and_hold_no_address() -> None:
    k1, k2 = limits.bucket_key("203.0.113.7"), limits.bucket_key("203.0.113.8")
    assert k1 != k2
    assert "203.0.113" not in k1


# --- origin ------------------------------------------------------------------------

@pytest.mark.parametrize("origin", [PROD, PROD + "/", "http://localhost:3000"])
def test_configured_origins_are_allowed(protection_on, origin) -> None:
    limits.check_origin(_request({"origin": origin}))


@pytest.mark.parametrize("origin", ["https://evil.example", "null",
                                    "https://rrip-demo.vercel.app.evil.example"])
def test_other_browser_origins_are_refused(protection_on, origin) -> None:
    with pytest.raises(limits.OriginNotAllowed):
        limits.check_origin(_request({"origin": origin}))


def test_no_origin_is_allowed_through(protection_on) -> None:
    # curl and server-to-server send none. The check is not authentication;
    # the rate limit and cap behind it are what bound such clients.
    limits.check_origin(_request())


# --- on / off / misconfigured, and failing closed ------------------------------

def test_mode(monkeypatch) -> None:
    monkeypatch.setattr(settings, "limiter_dsn", "")
    monkeypatch.setattr(settings, "tier", "local")
    assert limits.mode() == limits.OFF
    monkeypatch.setattr(settings, "tier", "published")
    assert limits.mode() == limits.MISCONFIGURED
    monkeypatch.setattr(settings, "limiter_dsn", "host=x")
    assert limits.mode() == limits.ON


def test_off_touches_nothing(monkeypatch) -> None:
    monkeypatch.setattr(settings, "limiter_dsn", "")
    monkeypatch.setattr(settings, "tier", "local")

    async def boom(*a, **k):
        raise AssertionError("the counter store must not be used when off")

    monkeypatch.setattr(limits, "_one", boom)
    asyncio.run(limits.protect(_request({"origin": "https://evil.example"})))


def test_published_without_a_limiter_refuses(monkeypatch) -> None:
    monkeypatch.setattr(settings, "limiter_dsn", "")
    monkeypatch.setattr(settings, "tier", "published")
    with pytest.raises(limits.ProtectionUnavailable):
        asyncio.run(limits.protect(_request()))


def test_an_unreachable_store_refuses_rather_than_bypassing(monkeypatch, protection_on) -> None:
    async def down():
        raise OSError("connection refused to host=10.9.9.9 user=rrip_limiter")

    monkeypatch.setattr(limits, "_connect", down)
    with pytest.raises(limits.ProtectionUnavailable) as exc:
        asyncio.run(limits.hit_ip("203.0.113.7"))
    assert "rrip_limiter" not in exc.value.public_message
    with pytest.raises(limits.ProtectionUnavailable):
        asyncio.run(limits.charge_llm_call())


def test_origin_is_checked_before_anything_is_counted(monkeypatch, protection_on) -> None:
    async def boom(*a, **k):
        raise AssertionError("a refused origin must not consume a bucket")

    monkeypatch.setattr(limits, "_one", boom)
    with pytest.raises(limits.OriginNotAllowed):
        asyncio.run(limits.protect(_request({"origin": "https://evil.example"})))


# --- the per-call budget hook ----------------------------------------------------

class _Flaky(LLMProvider):
    """Fails once, then answers -- so complete() retries exactly once."""

    name, model = "flaky", "flaky-1"

    def __init__(self):
        super().__init__(api_key="k", use_cache=False)
        self.calls = 0

    async def _call(self, prompt, system, temperature):
        self.calls += 1
        if self.calls == 1:
            raise LLMError("transient")
        return "ok"


def _counting_budget():
    n = {"charged": 0}

    async def budget():
        n["charged"] += 1

    return n, budget


def test_every_paid_attempt_is_charged_including_retries(monkeypatch) -> None:
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)      # skip retry backoff
    p = _Flaky()
    n, p.budget = _counting_budget()
    assert asyncio.run(p.complete("q")).text == "ok"
    assert n["charged"] == 2 == p.calls


async def _no_sleep(*_):
    return None


def test_a_cache_hit_is_not_charged(monkeypatch) -> None:
    p = _Flaky()
    n, p.budget = _counting_budget()
    monkeypatch.setattr(p, "_read_cache", lambda prompt, system: "cached")
    assert asyncio.run(p.complete("q")).cached
    assert n["charged"] == 0 and p.calls == 0


def test_a_refused_budget_stops_the_call_without_retrying() -> None:
    p = FakeProvider(["SELECT 1"])

    async def refuse():
        raise limits.DailyCapReached("cap")

    p.budget = refuse
    with pytest.raises(limits.DailyCapReached):
        asyncio.run(p.complete("q"))
    assert p.calls == []                # the model was never reached


def test_guarded_provider_is_rationed_only_when_protection_is_on(monkeypatch) -> None:
    monkeypatch.setattr(settings, "limiter_dsn", "")
    monkeypatch.setattr(settings, "tier", "local")
    assert limits.guarded_provider("fake").budget is None
    monkeypatch.setattr(settings, "limiter_dsn", "host=x")
    assert limits.guarded_provider("fake").budget is limits.charge_llm_call


# --- through the real app ----------------------------------------------------------

# Every route that can reach a model. The coverage test below fails if a route
# starts obtaining a provider without appearing here and being protected.
LLM_ROUTES = [
    ("POST", "/api/v1/ai/query",
     {"question": "Which 5 departments have the highest total revenue?"}),
    ("POST", "/api/v1/ai/narrate", {"data": [{"department": "GROCERY", "revenue": 1}]}),
    ("POST", "/api/v1/ai/ask", {"question": "What will GROCERY revenue be next week?"}),
    ("GET", "/api/v1/causal/analysis/26?propose=true", None),
]


def _client() -> TestClient:
    # No `with`: the lifespan (which opens the analytics pool) is not needed --
    # every refusal below happens before a handler touches the database.
    return TestClient(app, raise_server_exceptions=False)


def _call(c: TestClient, method: str, path: str, body):
    return _send(c, method, path, body, PROD)


def _send(c: TestClient, method: str, path: str, body, origin: str):
    headers = {"origin": origin}
    if method == "POST":
        return c.post(path, json=body, headers=headers)
    return c.get(path, headers=headers)


def _assert_safe(r) -> dict:
    body = r.json()
    assert set(body) == {"error", "message", "retry_after"}
    lowered = r.text.lower()
    for bit in SECRET_BITS:
        assert bit not in lowered, bit
    return body


@pytest.mark.parametrize("method,path,body", LLM_ROUTES)
def test_every_llm_route_is_rate_limited(monkeypatch, protection_on, method, path, body) -> None:
    async def over(ip, **k):
        raise limits.RateLimitExceeded(limits._RATE, retry_after=17)

    monkeypatch.setattr(limits, "hit_ip", over)
    r = _call(_client(), method, path, body)
    assert r.status_code == 429
    assert r.headers["retry-after"] == "17"
    assert _assert_safe(r)["error"] == "RATE_LIMITED"


@pytest.mark.parametrize("method,path,body", LLM_ROUTES)
def test_every_llm_route_refuses_foreign_origins(monkeypatch, protection_on,
                                                 method, path, body) -> None:
    async def never(*a, **k):
        raise AssertionError("not reached")

    monkeypatch.setattr(limits, "hit_ip", never)
    r = _send(_client(), method, path, body, "https://evil.example")
    assert r.status_code == 403
    assert _assert_safe(r)["error"] == "ORIGIN_NOT_ALLOWED"


def test_the_daily_cap_surfaces_as_its_own_error(monkeypatch, protection_on) -> None:
    async def ok(ip, **k):
        return 1

    async def capped(**k):
        raise limits.DailyCapReached(limits._CAP, retry_after=3600)

    monkeypatch.setattr(limits, "hit_ip", ok)
    monkeypatch.setattr(limits, "charge_llm_call", capped)
    r = _client().post("/api/v1/ai/query?provider=fake", headers={"origin": PROD},
                       json={"question": "Which 5 departments have the highest total revenue?"})
    assert r.status_code == 429
    body = _assert_safe(r)
    assert body["error"] == "DAILY_LLM_CAP" and body["retry_after"] == 3600


def test_published_tier_without_limiter_refuses_ai_calls(monkeypatch) -> None:
    monkeypatch.setattr(settings, "limiter_dsn", "")
    monkeypatch.setattr(settings, "tier", "published")
    r = _client().post("/api/v1/ai/query", json={"question": "Which 5 departments?"})
    assert r.status_code == 503
    assert _assert_safe(r)["error"] == "AI_PROTECTION_UNAVAILABLE"


def test_oversized_narration_payloads_are_rejected() -> None:
    big = [{"k": "x" * 100}] * 500
    r = _client().post("/api/v1/ai/narrate", json={"data": big})
    assert r.status_code == 422


def _api_routes(routes):
    """Flatten the app's routes. FastAPI 0.141 keeps included routers wrapped
    (_IncludedRouter), so app.routes alone lists none of the API endpoints --
    which silently made this test check nothing on its first run."""
    for r in routes:
        if isinstance(r, APIRoute):
            yield r
        elif hasattr(r, "original_router"):
            yield from _api_routes(r.original_router.routes)


def test_every_route_that_can_reach_a_model_is_protected() -> None:
    """Static backstop: a new route that obtains a provider must be protected."""
    routes = list(_api_routes(app.routes))
    assert len(routes) > 10, "route discovery found too few routes to mean anything"
    unprotected = []
    for route in routes:
        if route.path == "/api/v1/ai/status":
            continue
        src = inspect.getsource(route.endpoint)
        if "provider(" not in src:
            continue
        by_dependency = any(d.call is limits.protect for d in route.dependant.dependencies)
        if not (by_dependency or "limits.protect(" in src):
            unprotected.append(route.path)
    assert not unprotected, f"routes reach a model without protection: {unprotected}"
    protected_paths = {p.split("?")[0] for _, p, _ in LLM_ROUTES}
    reaching = {r.path.replace("{campaign_id}", "26") for r in routes
                if r.path != "/api/v1/ai/status"
                and "provider(" in inspect.getsource(r.endpoint)}
    assert reaching == protected_paths, reaching ^ protected_paths


def test_refusals_share_one_base_so_call_sites_can_re_raise_them() -> None:
    for cls in (limits.RateLimitExceeded, limits.DailyCapReached,
                limits.OriginNotAllowed, limits.ProtectionUnavailable):
        assert issubclass(cls, CallRefused)


# --- daily-cap contract: what is and is not charged -----------------------------

def test_a_refused_request_charges_nothing(monkeypatch, protection_on) -> None:
    charged = []

    async def over(ip, **k):
        raise limits.RateLimitExceeded(limits._RATE, retry_after=5)

    async def charge(**k):
        charged.append(1)

    monkeypatch.setattr(limits, "hit_ip", over)
    monkeypatch.setattr(limits, "charge_llm_call", charge)
    for method, path, body in LLM_ROUTES:
        assert _call(_client(), method, path, body).status_code == 429
    assert charged == []


def test_cli_and_benchmark_traffic_is_outside_the_public_cap() -> None:
    # The CLI and the eval harness obtain providers directly; only the API goes
    # through guarded_provider(), which is what attaches the cap.
    from pathlib import Path

    from rrip.ai.provider import get_provider

    assert get_provider("fake").budget is None
    src = Path(__file__).resolve().parents[1] / "src/rrip"
    for rel in ("cli.py", "eval/runner.py", "eval/narration_bench.py"):
        assert "guarded_provider" not in (src / rel).read_text(encoding="utf-8"), rel


def test_liveness_needs_neither_database_nor_provider() -> None:
    # No lifespan here, so no pool exists: a DB-backed check would fail.
    r = _client().get("/health/live")
    assert r.status_code == 200 and r.json() == {"status": "ok"}
