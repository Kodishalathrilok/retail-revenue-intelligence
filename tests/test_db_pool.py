"""The API pool must survive the server dropping its idle connections.

Production, after ~13 idle minutes: Neon had closed both pooled connections, the
pool handed one to the next request, the query failed, and the pool noticed only
on return ("discarding closed connection"). The request after that hit Vercel's
60 s limit.

The DB test reproduces the drop with the server's own idle_session_timeout set
per connection, which terminates every idle pooled session the way Neon does,
without touching the role. Skipped without a read-only DSN, except where
RRIP_REQUIRE_RO_DB is set (the CI job with a database), where that is a failure.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest
from psycopg.conninfo import make_conninfo
from psycopg_pool import AsyncConnectionPool

from rrip.api import db
from rrip.eval.verify_role import ro_dsn

DSN = ro_dsn()

if DSN is None and os.getenv("RRIP_REQUIRE_RO_DB"):
    raise RuntimeError("RRIP_REQUIRE_RO_DB is set but RRIP_PG_READONLY_DSN is not")

NEON_IDLE_S = 300  # Neon scales to zero after 5 idle minutes, dropping connections
VERCEL_MAX_S = json.loads(
    (Path(__file__).parents[1] / "frontend/vercel.json").read_text()
)["functions"]["api/index.py"]["maxDuration"]

# Long enough that a replacement connection is handed out before the server
# drops it too: the pool retries a failed check at once, then backs off from ~1 s.
IDLE_KILL_S = 2


def run(coro_fn):
    return asyncio.run(coro_fn(), loop_factory=asyncio.SelectorEventLoop)


@pytest.mark.skipif(DSN is None, reason="no read-only DSN; set RRIP_PG_READONLY_DSN")
def test_a_connection_the_server_dropped_while_idle_is_never_handed_out(monkeypatch):
    monkeypatch.setattr(db, "_pool", None)
    monkeypatch.setattr(db, "conninfo", lambda: make_conninfo(
        DSN, options=f"-c idle_session_timeout={IDLE_KILL_S * 1000}"))

    async def scenario():
        try:
            before = await db.fetch_one("SELECT pg_backend_pid() AS pid")
            await asyncio.sleep(IDLE_KILL_S + 1)  # every pooled session is now dead
            after = await db.fetch_one("SELECT pg_backend_pid() AS pid")
        finally:
            await db.close_pool()
        return before["pid"], after["pid"]

    before, after = run(scenario)
    assert after != before, "the dropped connection was reused"


def test_idle_connections_are_released_before_neon_drops_them(monkeypatch):
    monkeypatch.setattr(db, "_pool", None)
    monkeypatch.setattr(db, "conninfo", lambda: "host=unused")

    async def no_connect(self, *args, **kwargs):
        return None

    monkeypatch.setattr(AsyncConnectionPool, "open", no_connect)
    pool = run(db.open_pool)

    assert pool.min_size == 0, "an idle instance should hold no connections"
    # The shrink runs every max_idle seconds and needs a whole unused window, so
    # an idle connection can live up to 2 * max_idle.
    assert 2 * pool.max_idle < NEON_IDLE_S
    # A reconnect stuck across a frozen instance must fail and retry well
    # inside the function's time limit (psycopg's own default is 130 s).
    assert pool.kwargs["connect_timeout"] * 2 < VERCEL_MAX_S
