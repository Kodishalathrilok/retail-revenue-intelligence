"""The NL->SQL gates and execution against a real PostgreSQL as rrip_ro.

The synchronous gates are unit-tested in test_sql_guard.py without a database.
This file is the other half: that the EXPLAIN cost gate and the guarded
execution behave against a real server, and -- the point of it -- that a
multi-statement payload is refused by PostgreSQL itself, not merely by the text
gates. The gates are defence in depth; `binary=True` forces the extended query
protocol, where the server rejects a second command in one statement. That is
the boundary this file exercises, so a future change to the gates cannot quietly
remove it.

Skipped without a read-only DSN, except where RRIP_REQUIRE_RO_DB is set (the CI
job that builds a database), where a missing DSN is a failure rather than a
skip -- a test that passes by not connecting would recreate the gap it guards.
"""

from __future__ import annotations

import asyncio
import os

import pytest

from rrip.ai.nl2sql import answer, execute_guarded, validate_cost
from rrip.ai.provider import FakeProvider
from rrip.api import db
from rrip.eval.verify_role import ro_dsn

DSN = ro_dsn()

if DSN is None and os.getenv("RRIP_REQUIRE_RO_DB"):
    raise RuntimeError("RRIP_REQUIRE_RO_DB is set but RRIP_PG_READONLY_DSN is not")

requires_role = pytest.mark.skipif(
    DSN is None, reason="no read-only DSN; set RRIP_PG_READONLY_DSN")


def run(coro):
    """Run one coroutine on a SelectorEventLoop (psycopg's async driver needs it
    on Windows), closing the pool afterwards so each test starts clean."""
    async def scenario():
        try:
            return await coro()
        finally:
            await db.close_pool()
    return asyncio.run(scenario(), loop_factory=asyncio.SelectorEventLoop)


@requires_role
def test_a_legitimate_query_executes_and_returns_rows() -> None:
    async def scenario():
        p = FakeProvider(["SELECT 1 AS n"])
        return await answer("give me a one", p, use_router=False)

    result = run(scenario)
    assert result.succeeded, result.failure_reason
    assert result.rows == [{"n": 1}]
    # All six gates passed: shape, keywords, functions, explain, execute, result.
    assert [s.passed for a in result.attempts for s in a.stages] == [True] * 6


@requires_role
def test_a_rejected_first_attempt_is_retried_and_can_succeed() -> None:
    async def scenario():
        # First draft is multi-statement (rejected at the shape gate, fed back),
        # second is clean. The retry loop should recover.
        p = FakeProvider(["SELECT 1; SELECT 2", "SELECT 2 AS n"])
        return await answer("q", p, max_attempts=2, use_router=False)

    result = run(scenario)
    assert result.succeeded, result.failure_reason
    assert len(result.attempts) == 2
    assert result.attempts[0].rejected_reason
    assert result.rows == [{"n": 2}]


@requires_role
def test_the_cost_gate_rejects_a_plan_over_the_ceiling() -> None:
    async def scenario():
        # A real plan, priced against a ceiling of zero so any plan exceeds it.
        return await validate_cost("SELECT count(*) FROM pub_dim_week"
                                   if await _has("pub_dim_week")
                                   else "SELECT count(*) FROM dim_week",
                                   max_cost=0.0)

    stage, cost = run(scenario)
    assert not stage.passed
    assert "exceeds ceiling" in stage.detail


@requires_role
def test_a_second_statement_is_refused_by_the_server_not_just_the_gates() -> None:
    """The boundary, exercised directly: execute_guarded bypasses the shape and
    keyword gates, so this is purely what the server does with two commands over
    the extended protocol. It must refuse, and the first command must not have
    committed anything either."""
    async def scenario():
        stage, rows, cols = await execute_guarded("SELECT 1; SELECT 2")
        return stage

    stage = run(scenario)
    assert not stage.passed, "the server ran a multi-statement query"
    # psycopg surfaces Postgres's "cannot insert multiple commands into a
    # prepared statement"; assert on the stable part of that message.
    assert "multiple commands" in stage.detail.lower()


async def _has(table: str) -> bool:
    from rrip.api.db import fetch_one
    row = await fetch_one("SELECT to_regclass(%(t)s) IS NOT NULL AS ok",
                          {"t": table})
    return bool(row and row["ok"])
