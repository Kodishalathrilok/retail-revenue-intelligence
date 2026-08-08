"""FastAPI application."""

from __future__ import annotations

import asyncio
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from rrip.api import ai_routes, analytics, causal_routes
from rrip.api.db import close_pool, open_pool

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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(analytics.router)
app.include_router(ai_routes.router)
app.include_router(causal_routes.router)


@app.get("/health")
async def health() -> dict:
    from rrip.api.db import fetch_one
    row = await fetch_one("SELECT count(*) AS n FROM fact_transactions")
    return {"status": "ok", "fact_transactions_rows": row["n"] if row else 0}
