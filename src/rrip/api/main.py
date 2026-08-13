"""FastAPI application."""

from __future__ import annotations

import asyncio
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from rrip.api import ai_routes, analytics, causal_routes
from rrip.api.db import close_pool, open_pool
from rrip.config import settings

# psycopg's async driver does not work with the ProactorEventLoop that Python
# uses by default on Windows -- the pool never finishes initialising and times
# out. The selector policy must be set before any loop is created.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


@asynccontextmanager
async def lifespan(app: FastAPI):
    await open_pool()
    yield
    await close_pool()


app = FastAPI(
    title="Retail Revenue Intelligence Platform",
    version="0.1.0",
    description=(
        "Analytics over the dunnhumby Complete Journey panel.\n\n"
        "**The LLM never computes a number.** SQL and Python compute; the model "
        "proposes SQL, explains computed results, and suggests confounders. "
        "Every figure traces to a query."),
    lifespan=lifespan,
)

# Origins come from RRIP_CORS_ORIGINS so deploying does not require a code
# change. A CORS mismatch fails only in the browser -- curl against the same
# endpoint succeeds and the server log is clean -- so it is easy to misdiagnose
# as a frontend bug.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(analytics.router)
app.include_router(ai_routes.router)
app.include_router(causal_routes.router)


@app.get("/health")
async def health() -> dict:
    """Health check that works on BOTH tiers.

    It previously counted fact_transactions, which does not exist on the
    published tier -- so the health endpoint itself would have been the first
    thing to 500 in production.
    """
    from rrip.api.db import fetch_one
    from rrip.config import settings

    if settings.is_published:
        row = await fetch_one(
            "SELECT count(*) AS n, max(published_at) AS published_at FROM pub_manifest")
        return {"status": "ok", "tier": "published",
                "published_tables": row["n"] if row else 0,
                "published_at": row["published_at"] if row else None}

    row = await fetch_one("SELECT count(*) AS n FROM fact_transactions")
    return {"status": "ok", "tier": "local",
            "fact_transactions_rows": row["n"] if row else 0}
