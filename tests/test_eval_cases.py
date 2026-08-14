"""Validates the benchmark's answer key against the real database.

A reference query that errors, or that returns nothing, makes every case graded
against it fail -- and the failure looks like the model getting the question
wrong. That is the worst failure mode a benchmark has, because the number it
produces is wrong in the direction that flatters nobody and misleads everyone.

Skipped when the database is unreachable, so the unit suite still runs without
Postgres.
"""

from __future__ import annotations

import pytest

from rrip.eval.cases import CORRECT, LOCAL, PUBLISHED, for_tier

psycopg = pytest.importorskip("psycopg")


def _conn():
    from rrip.db.connection import connect
    return connect()


def _reachable(probe: str) -> bool:
    try:
        with _conn() as c, c.cursor() as cur:
            cur.execute("SELECT to_regclass(%s)", (probe,))
            return cur.fetchone()[0] is not None
    except Exception:
        return False


LOCAL_READY = _reachable("fact_transactions")
PUBLISHED_READY = _reachable("pub_weekly_revenue")

GRADED_LOCAL = [c for c in for_tier(LOCAL) if c.expectation == CORRECT]
GRADED_PUBLISHED = [c for c in for_tier(PUBLISHED) if c.expectation == CORRECT]


@pytest.mark.skipif(not LOCAL_READY, reason="star schema not loaded")
@pytest.mark.parametrize("case", GRADED_LOCAL, ids=lambda c: c.id)
def test_local_reference_sql_runs_and_returns_rows(case) -> None:
    with _conn() as c, c.cursor() as cur:
        cur.execute(case.reference_sql)
        rows = cur.fetchall()
    assert rows, f"{case.id}: reference query returned no rows -- it cannot grade anything"


@pytest.mark.skipif(not PUBLISHED_READY, reason="pub_* tier not built")
@pytest.mark.parametrize("case", GRADED_PUBLISHED, ids=lambda c: c.id)
def test_published_reference_sql_runs_and_returns_rows(case) -> None:
    with _conn() as c, c.cursor() as cur:
        cur.execute(case.reference_sql)
        rows = cur.fetchall()
    assert rows, f"{case.id}: reference query returned no rows"


@pytest.mark.skipif(not LOCAL_READY, reason="star schema not loaded")
def test_reference_queries_are_cheap_enough_to_grade_with() -> None:
    """The answer key runs on every graded case, so it must not dominate runtime.

    This is not a performance test of the database -- it is a guard against
    someone adding a reference query whose cost makes the benchmark unusable.
    """
    slow = []
    with _conn() as c:
        for case in GRADED_LOCAL:
            with c.cursor() as cur:
                cur.execute(f"EXPLAIN (FORMAT JSON) {case.reference_sql}")
                cost = cur.fetchone()[0][0]["Plan"]["Total Cost"]
            if cost > 5_000_000:
                slow.append((case.id, cost))
    assert not slow, f"reference queries above the validator's own cost ceiling: {slow}"
