"""AI endpoint protection against real Postgres: the counters themselves.

Needs sql/ddl/70_api_limits.sql applied and RRIP_TEST_LIMITER_DSN pointing at
rrip_limiter. Skipped without it -- except where RRIP_REQUIRE_LIMITER_DB is set
(the CI job that has a database), where a missing DSN is a failure, so these
cannot pass by quietly not running.

Isolation: every test uses its own random bucket keys, and the daily-cap tests
use their own random dates in the 1900s via the `at` parameter, so runs never
collide with each other or with live counters. Rows are removed afterwards.
"""

from __future__ import annotations

import asyncio
import os
import random
import uuid
from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from rrip.api import limits
from rrip.config import settings

DSN = os.getenv("RRIP_TEST_LIMITER_DSN")

if not DSN and os.getenv("RRIP_REQUIRE_LIMITER_DB"):
    raise RuntimeError("RRIP_REQUIRE_LIMITER_DB is set but RRIP_TEST_LIMITER_DSN is "
                       "not -- the limiter integration tests would not run")

pytestmark = pytest.mark.skipif(
    not DSN, reason="no RRIP_TEST_LIMITER_DSN; apply sql/ddl/70_api_limits.sql first")

T0 = datetime(2001, 1, 1, 12, 0, 0, tzinfo=UTC)
WINDOW = 60


@pytest.fixture(autouse=True)
def _limiter(monkeypatch):
    monkeypatch.setattr(settings, "limiter_dsn", DSN)
    keys: list[str] = []
    yield keys
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("DELETE FROM api_rate_limit WHERE bucket_key = ANY(%s)",
                     ([limits.bucket_key(k) for k in keys],))


def run(coro_fn):
    # Each counter statement opens its own connection, so gather() below puts
    # genuinely concurrent upserts on the database. A selector loop because
    # psycopg's async mode cannot run on Windows' default Proactor loop
    # (rrip.api.main sets the same policy for the server).
    return asyncio.run(coro_fn(), loop_factory=asyncio.SelectorEventLoop)


def new_ip(keys: list[str]) -> str:
    ip = f"test-{uuid.uuid4()}"
    keys.append(ip)
    return ip


@pytest.fixture
def day():
    """A date nobody else uses, and cleanup of its row. Needs the owner role:
    rrip_limiter deliberately holds no DELETE on llm_usage_daily."""
    d = datetime(1900, 1, 1, 12, tzinfo=UTC) + timedelta(days=random.randrange(36_000))
    yield d
    from rrip.db.connection import connect
    with connect(autocommit=True) as conn:
        conn.execute("DELETE FROM llm_usage_daily WHERE usage_date = %s", (d.date(),))


# --- per-IP ------------------------------------------------------------------------

def test_requests_within_the_limit_are_allowed_and_the_next_refused(_limiter) -> None:
    ip = new_ip(_limiter)

    async def go():
        counts = [await limits.hit_ip(ip, limit=3, window=WINDOW, at=T0) for _ in range(3)]
        with pytest.raises(limits.RateLimitExceeded) as exc:
            await limits.hit_ip(ip, limit=3, window=WINDOW, at=T0)
        return counts, exc.value.retry_after

    counts, retry_after = run(go)
    assert counts == [1, 2, 3]
    assert 0 < retry_after <= WINDOW


def test_the_next_window_starts_fresh(_limiter) -> None:
    ip = new_ip(_limiter)

    async def go():
        for _ in range(2):
            await limits.hit_ip(ip, limit=2, window=WINDOW, at=T0)
        with pytest.raises(limits.RateLimitExceeded):
            await limits.hit_ip(ip, limit=2, window=WINDOW, at=T0)
        return await limits.hit_ip(ip, limit=2, window=WINDOW,
                                   at=T0 + timedelta(seconds=WINDOW))

    assert run(go) == 1


def test_clients_do_not_share_a_bucket(_limiter) -> None:
    a, b = new_ip(_limiter), new_ip(_limiter)

    async def go():
        await limits.hit_ip(a, limit=1, window=WINDOW, at=T0)
        with pytest.raises(limits.RateLimitExceeded):
            await limits.hit_ip(a, limit=1, window=WINDOW, at=T0)
        return await limits.hit_ip(b, limit=1, window=WINDOW, at=T0)

    assert run(go) == 1


def test_concurrent_requests_get_distinct_counts_and_exactly_limit_pass(_limiter) -> None:
    ip, n, limit = new_ip(_limiter), 40, 10

    async def one():
        try:
            return await limits.hit_ip(ip, limit=limit, window=WINDOW, at=T0)
        except limits.RateLimitExceeded:
            return None

    async def go():
        return await asyncio.gather(*(one() for _ in range(n)))

    results = run(go)                               # 40 concurrent connections
    allowed = [r for r in results if r is not None]
    assert sorted(allowed) == list(range(1, limit + 1))
    with psycopg.connect(DSN) as conn:
        total = conn.execute(
            "SELECT request_count FROM api_rate_limit WHERE bucket_key = %s",
            (limits.bucket_key(ip),)).fetchone()[0]
    assert total == n                               # no lost increments


def test_expired_buckets_are_cleaned_up(_limiter) -> None:
    old, new = new_ip(_limiter), new_ip(_limiter)

    async def go():
        await limits.hit_ip(old, window=WINDOW, at=T0 - timedelta(days=3))
        await limits.hit_ip(new, window=WINDOW, at=T0)     # first hit -> cleanup

    run(go)
    with psycopg.connect(DSN) as conn:
        left = conn.execute("SELECT count(*) FROM api_rate_limit WHERE bucket_key = %s",
                            (limits.bucket_key(old),)).fetchone()[0]
    assert left == 0


# --- daily cap ---------------------------------------------------------------------

def test_calls_count_up_and_the_cap_refuses(day) -> None:
    async def go():
        counts = [await limits.charge_llm_call(cap=3, at=day) for _ in range(3)]
        with pytest.raises(limits.DailyCapReached) as exc:
            await limits.charge_llm_call(cap=3, at=day)
        return counts, exc.value.retry_after

    counts, retry_after = run(go)
    assert counts == [1, 2, 3]
    assert 0 < retry_after <= 86_400


def test_a_new_utc_day_starts_fresh(day) -> None:
    async def go():
        await limits.charge_llm_call(cap=1, at=day)
        with pytest.raises(limits.DailyCapReached):
            await limits.charge_llm_call(cap=1, at=day)
        return await limits.charge_llm_call(cap=1, at=day + timedelta(days=1))

    try:
        assert run(go) == 1
    finally:
        from rrip.db.connection import connect
        with connect(autocommit=True) as conn:
            conn.execute("DELETE FROM llm_usage_daily WHERE usage_date = %s",
                         ((day + timedelta(days=1)).date(),))


def test_concurrent_calls_cannot_overshoot_the_cap(day) -> None:
    n, cap = 40, 15

    async def one():
        try:
            return await limits.charge_llm_call(cap=cap, at=day)
        except limits.DailyCapReached:
            return None

    async def go():
        return await asyncio.gather(*(one() for _ in range(n)))

    results = run(go)
    allowed = [r for r in results if r is not None]
    assert sorted(allowed) == list(range(1, cap + 1))  # exactly `cap` calls proceed


# --- the role boundary -------------------------------------------------------------

def test_the_limiter_role_can_touch_nothing_else() -> None:
    with psycopg.connect(DSN, autocommit=True) as conn:
        for sql in ("SELECT 1 FROM dim_store LIMIT 1",
                    "TRUNCATE api_rate_limit",
                    "DELETE FROM llm_usage_daily",
                    "CREATE TABLE limiter_probe (x int)"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(sql)


def test_the_read_only_role_cannot_read_the_counters() -> None:
    from rrip.eval.verify_role import ro_dsn

    ro = ro_dsn()
    if not ro:
        pytest.skip("no read-only DSN configured")
    with psycopg.connect(ro, autocommit=True) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT count(*) FROM api_rate_limit")
