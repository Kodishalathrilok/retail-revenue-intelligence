"""Async connection pool and query execution for the API.

Every query runs under a statement timeout. An analytical endpoint that can hang
is an endpoint that can exhaust the pool, and Phase 2 measured queries taking
40+ seconds on this data -- so the timeout is a real guard, not a formality.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from rrip.config import settings

DEFAULT_TIMEOUT_MS = 15_000
MAX_TIMEOUT_MS = 120_000

_pool: AsyncConnectionPool | None = None


def conninfo() -> str:
    return (
        f"host={settings.pg_host} port={settings.pg_port} "
        f"user={settings.pg_user} password={settings.pg_password} "
        f"dbname={settings.pg_database}"
    )


async def open_pool() -> AsyncConnectionPool:
    global _pool
    if _pool is None:
        _pool = AsyncConnectionPool(
            conninfo(),
            min_size=2,
            max_size=10,
            kwargs={"row_factory": dict_row},
            open=False,
        )
        await _pool.open(wait=True, timeout=15)
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


@asynccontextmanager
async def acquire(timeout_ms: int = DEFAULT_TIMEOUT_MS) -> AsyncIterator[Any]:
    pool = await open_pool()
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"SET statement_timeout = {min(int(timeout_ms), MAX_TIMEOUT_MS)}")
        yield conn


async def fetch(sql: str, params: dict | None = None,
                timeout_ms: int = DEFAULT_TIMEOUT_MS) -> list[dict]:
    async with acquire(timeout_ms) as conn:
        async with conn.cursor() as cur:
            await cur.execute(sql, params or {})
            return await cur.fetchall()


async def fetch_one(sql: str, params: dict | None = None,
                    timeout_ms: int = DEFAULT_TIMEOUT_MS) -> dict | None:
    rows = await fetch(sql, params, timeout_ms)
    return rows[0] if rows else None


async def resolve_week_range(date_from: Any, date_to: Any) -> tuple[int, int]:
    """Resolve a date window to week numbers BEFORE the analytical query is planned.

    This is not a convenience. fact_causal is partitioned on week_no, and Phase 2
    measured that expressing the filter as a date range on dim_week leaves all
    102 partitions in the plan -- a scalar subquery is not evaluated until
    execution, so the planner has nothing to prune against.

    Resolving here means the analytical query receives plan-time integer
    constants and prunes to the weeks it needs (27 of 102 for a six-month
    window, 2.09x faster). Inlining this lookup back into the query would
    silently undo that.
    """
    row = await fetch_one(
        """SELECT coalesce(min(week_no), 1)   AS week_from,
                  coalesce(max(week_no), 102) AS week_to
           FROM dim_week
           WHERE end_date >= %(date_from)s AND start_date <= %(date_to)s""",
        {"date_from": date_from, "date_to": date_to})
    return int(row["week_from"]), int(row["week_to"])
