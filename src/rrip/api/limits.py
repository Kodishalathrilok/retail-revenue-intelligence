"""Abuse protection for the endpoints that spend LLM calls.

    request -> origin check -> per-IP rate limit -> handler
                                                    |
                        each paid model attempt -> daily global cap -> provider

This is not authentication. There are no users; the demo is public on
purpose. What it bounds is cost: how fast one client can spend model calls, and
how many calls the whole deployment can spend in a day, so a scripted client
cannot run the provider key into its quota or its bill.

WHY POSTGRES, NOT MEMORY. The API runs as a Vercel function, so any number of
instances can serve concurrent requests, each with its own memory. A dict of
counters would give every instance its own limit -- N instances, N times the
allowance, and a fresh allowance on every cold start. The counters have to live
somewhere all instances share, and Postgres is already that place.

WHY NOT rrip_ro. The API's pool connects as rrip_ro, which holds SELECT and
nothing else, and that is what bounds the NL->SQL read bypass documented in
sql/ddl/60_readonly_role.sql. Counters need writes. Granting them to rrip_ro
would put a writable table behind the same connection that executes
model-proposed SQL, so the counters use their own role, rrip_limiter, which can
touch only these two tables, over its own connections.

FAILURE MODE. Protection on and the database unreachable -> refuse (503). This
is security infrastructure; silently skipping it whenever the counter store is
down would make an outage the easiest way to bypass it. Deterministic
endpoints never touch this module and keep working.

    RRIP_LIMITER_DSN set                -> on
    unset, RRIP_TIER=published          -> AI endpoints refuse: misconfigured
    unset, local tier                   -> off (development)
"""

from __future__ import annotations

import hashlib
import ipaddress
import logging
from datetime import datetime

import psycopg
from fastapi import Request

from rrip.ai.provider import CallRefused, LLMProvider, get_provider
from rrip.config import settings

logger = logging.getLogger(__name__)

ON, OFF, MISCONFIGURED = "on", "off", "misconfigured"


class RateLimitExceeded(CallRefused):
    status, code = 429, "RATE_LIMITED"


class DailyCapReached(CallRefused):
    status, code = 429, "DAILY_LLM_CAP"


class OriginNotAllowed(CallRefused):
    status, code = 403, "ORIGIN_NOT_ALLOWED"


class ProtectionUnavailable(CallRefused):
    status, code = 503, "AI_PROTECTION_UNAVAILABLE"


# Client-facing text. Deliberately says nothing about limits, windows, caps,
# DSNs or which check fired beyond the code -- none of that helps a visitor and
# all of it helps someone tuning a script.
_UNAVAILABLE = ("AI features are temporarily unavailable. The dashboards and "
                "precomputed analyses still work.")
_CAP = ("This demo's daily AI budget has been used up. The dashboards still "
        "work; AI questions resume at 00:00 UTC.")
_RATE = "Too many AI requests from your network. Please wait and try again."
_ORIGIN = "AI requests are accepted only from this project's web app."


def mode() -> str:
    if settings.limiter_dsn:
        return ON
    return MISCONFIGURED if settings.is_published else OFF


# --- the shared counter store --------------------------------------------------

async def _connect() -> psycopg.AsyncConnection:
    # A fresh connection per statement rather than a pool. A pool keeps
    # background reconnect tasks and is bound to the event loop that opened it,
    # and both bite on serverless: a warm instance can serve the next
    # invocation from a new loop, and a store that is down leaves reconnect
    # tasks that never finish. This way a failure is one bounded timeout.
    # ponytail: one connect per counter statement (tens of ms against an LLM
    # call of seconds); pool it if AI traffic ever makes connect time matter.
    return await psycopg.AsyncConnection.connect(
        settings.limiter_dsn, autocommit=True, connect_timeout=5)


async def _run(sql: str, params: dict) -> tuple | None:
    conn = await _connect()
    try:
        cur = await conn.execute(sql, params)
        return await cur.fetchone() if cur.description else None
    finally:
        await conn.close()


async def _one(sql: str, params: dict) -> tuple:
    """Run one counter statement; any failure is a refusal, never a bypass."""
    try:
        return await _run(sql, params)
    except Exception as exc:
        # The detail goes to the server log only; it can name hosts and roles.
        logger.error("limiter store unavailable: %s: %s", type(exc).__name__, exc)
        raise ProtectionUnavailable(_UNAVAILABLE) from None


# One statement per check. The upsert is atomic under concurrency: the row lock
# taken by ON CONFLICT DO UPDATE serialises increments to the same bucket, so
# every request gets a distinct count and exactly `limit` of them see a count
# within the limit. Time comes from the database clock, so instances with
# drifting clocks still agree on window boundaries. `at` exists for tests.
_IP_SQL = """
WITH w AS (
    SELECT ts, to_timestamp(floor(extract(epoch FROM ts) / %(window)s) * %(window)s) AS start
      FROM (SELECT coalesce(%(at)s::timestamptz, now()) AS ts) t
)
INSERT INTO api_rate_limit AS r (bucket_key, window_start, request_count)
SELECT %(key)s, start, 1 FROM w
ON CONFLICT (bucket_key, window_start)
DO UPDATE SET request_count = r.request_count + 1
RETURNING r.request_count,
          (SELECT ceil(extract(epoch FROM w.start + make_interval(secs => %(window)s) - w.ts))::int
             FROM w)
"""

# Expired buckets. Run only on a bucket's first hit, so at most once per client
# per window, and it is a range delete on the window_start index.
_IP_CLEANUP_SQL = """
DELETE FROM api_rate_limit
 WHERE window_start < coalesce(%(at)s::timestamptz, now()) - interval '1 day'
"""

_CAP_SQL = """
WITH t AS (SELECT coalesce(%(at)s::timestamptz, now()) AT TIME ZONE 'UTC' AS utc)
INSERT INTO llm_usage_daily AS u (usage_date, calls)
SELECT utc::date, 1 FROM t
ON CONFLICT (usage_date) DO UPDATE SET calls = u.calls + 1
RETURNING u.calls,
          (SELECT ceil(extract(epoch FROM (utc::date + 1)::timestamp - utc))::int FROM t)
"""


def client_ip(request: Request) -> str:
    """The address a request is bucketed under.

    A forwarding header is read only when RRIP_TRUSTED_IP_HEADER names it, so a
    client talking to the API directly cannot choose its own bucket by sending
    one. IPv6 is bucketed per /64: a single subscriber is usually handed a
    whole /64 and could otherwise rotate addresses within it.
    """
    raw = ""
    if settings.trusted_ip_header:
        raw = (request.headers.get(settings.trusted_ip_header) or "").split(",")[0].strip()
    if not raw:
        raw = request.client.host if request.client else "unknown"
    try:
        addr = ipaddress.ip_address(raw)
    except ValueError:
        return raw[:64]
    if addr.version == 6:
        return str(ipaddress.ip_network(f"{addr}/64", strict=False))
    return str(addr)


def bucket_key(ip: str) -> str:
    # Hashed so the table holds no raw addresses. Not anonymisation -- the
    # IPv4 space is small enough to enumerate -- but rows live a day at most.
    return "ip:" + hashlib.sha256(ip.encode()).hexdigest()[:32]


async def hit_ip(ip: str, *, limit: int | None = None, window: int | None = None,
                 at: datetime | None = None) -> int:
    """Count one request for this client; refuse past the limit."""
    limit = limit or settings.ai_rate_limit
    window = window or settings.ai_rate_window_seconds
    count, retry_after = await _one(
        _IP_SQL, {"key": bucket_key(ip), "window": window, "at": at})
    if count == 1:
        await _cleanup(at)
    if count > limit:
        raise RateLimitExceeded(_RATE, retry_after=max(int(retry_after), 1))
    return count


async def _cleanup(at: datetime | None) -> None:
    try:
        await _run(_IP_CLEANUP_SQL, {"at": at})
    except Exception as exc:
        # Housekeeping. The count above already succeeded, so a failed delete
        # costs table size, not protection.
        logger.warning("limiter cleanup failed: %s", type(exc).__name__)


async def charge_llm_call(*, cap: int | None = None, at: datetime | None = None) -> int:
    """Count one paid model attempt against today's global cap (UTC day).

    The counter keeps rising past the cap on refused calls, but a refused call
    never reaches the provider: each increment is a distinct value, and only
    the first `cap` of them proceed. So concurrent requests cannot overshoot
    the number of calls actually made.
    """
    cap = settings.llm_daily_cap if cap is None else cap
    calls, retry_after = await _one(_CAP_SQL, {"at": at})
    if calls > cap:
        raise DailyCapReached(_CAP, retry_after=max(int(retry_after), 1))
    return calls


async def _refuse_misconfigured() -> None:
    raise ProtectionUnavailable(_UNAVAILABLE)


def check_origin(request: Request) -> None:
    """Refuse browser requests from origins other than the app's own.

    Stops another website from spending this demo's model calls through its
    visitors' browsers. It is not authentication: a script sends whatever
    Origin it likes, or none -- which is why a missing Origin (curl, server to
    server) is allowed through, and why the rate limit and the cap sit behind
    this check rather than relying on it. Allowed origins are
    RRIP_CORS_ORIGINS, the same list CORS uses.
    """
    origin = request.headers.get("origin")
    if origin is None:
        return
    allowed = {o.rstrip("/") for o in settings.cors_origin_list}
    if origin.rstrip("/") not in allowed:
        raise OriginNotAllowed(_ORIGIN)


async def protect(request: Request) -> None:
    """FastAPI dependency for every route that can make a model call."""
    m = mode()
    if m == OFF:
        return
    if m == MISCONFIGURED:
        logger.error("RRIP_TIER=published without RRIP_LIMITER_DSN: AI endpoints "
                     "refuse until sql/ddl/70_api_limits.sql is applied and the "
                     "DSN is set")
        raise ProtectionUnavailable(_UNAVAILABLE)
    check_origin(request)
    await hit_ip(client_ip(request))


def guarded_provider(name: str | None = None) -> LLMProvider:
    """The only way API code should obtain a provider: rationed by the cap."""
    p = get_provider(name, use_cache=settings.llm_cache)
    m = mode()
    if m == ON:
        p.budget = charge_llm_call
    elif m == MISCONFIGURED:
        p.budget = _refuse_misconfigured
    return p
