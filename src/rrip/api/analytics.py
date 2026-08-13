"""Analytical endpoints over the Phase 3 query library.

The SQL here is parameterised from sql/analytics/ rather than duplicated, where
the shape allows. Where an endpoint needs a filtered variant, the difference is
a WHERE clause on the same structure, not a reimplementation -- so the API and
the documented query library cannot drift apart.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException, Query

from rrip.api import queries as Q
from rrip.api.db import fetch, fetch_one, resolve_week_range
from rrip.api.models import (
    PANEL_END,
    PANEL_START,
    Cursor,
    ExecutiveOverview,
    Meta,
    Page,
)
from rrip.config import settings

router = APIRouter(prefix="/api/v1", tags=["analytics"])


@router.get("/overview", response_model=ExecutiveOverview)
async def overview(
    date_from: date = Query(PANEL_START),
    date_to: date = Query(PANEL_END),
    department: str | None = Query(None),
) -> ExecutiveOverview:
    row = await fetch_one(
        Q.pick(Q.OVERVIEW_LOCAL, Q.OVERVIEW_PUBLISHED),
        {"date_from": date_from, "date_to": date_to, "department": department},
        timeout_ms=30_000)

    if not row or row["total_revenue"] is None:
        raise HTTPException(404, "no data in the requested window")

    payload = dict(row)
    if settings.is_published:
        # Panel-wide on this tier -- see the note in queries.OVERVIEW_PUBLISHED.
        # Stated in the response rather than silently applied.
        payload["window_basis"] = (
            "Published tier: totals cover the whole 711-day panel. A distinct "
            "household count cannot be reconstructed from weekly aggregates, so "
            "the date filter is not applied to this endpoint here.")
    return ExecutiveOverview(**payload, meta=Meta())


@router.get("/revenue/weekly")
async def revenue_weekly(
    date_from: date = Query(PANEL_START),
    date_to: date = Query(PANEL_END),
    department: str | None = Query(None),
) -> dict:
    """Weekly revenue with cumulative total and a centred rolling average.

    Mirrors sql/analytics/04_running_totals.sql. The rolling window is centred
    rather than trailing, because a trailing average lags the series by half its
    width and misplaces turning points.
    """
    rows = await fetch(
        Q.pick(Q.WEEKLY_LOCAL, Q.WEEKLY_PUBLISHED),
        {"date_from": date_from, "date_to": date_to, "department": department},
        timeout_ms=30_000)
    return {"items": rows, "meta": Meta().model_dump()}


@router.get("/segments/rfm")
async def rfm_segments() -> dict:
    """RFM segmentation. Recency is measured against panel end, not today."""
    rows = await fetch(
        Q.pick(Q.RFM_LOCAL, Q.RFM_PUBLISHED), timeout_ms=30_000)
    return {"items": rows, "meta": Meta().model_dump()}


@router.get("/retention/tenure")
async def retention_tenure() -> dict:
    """Retention by relative tenure.

    Deliberately NOT calendar cohorts. dunnhumby is a panel: 99.8% of households
    first purchase within 180 days of a 711-day window, so calendar cohorts
    would contrast early recruits against six stragglers. The response carries
    that basis so a chart cannot render the numbers without it.
    """
    rows = await fetch(
        Q.pick(Q.RETENTION_LOCAL, Q.RETENTION_PUBLISHED), timeout_ms=30_000)
    return {"items": rows,
            "basis": "relative tenure, not calendar cohorts (panel data)",
            "meta": Meta().model_dump()}


@router.get("/promotions/exposure")
async def promo_exposure(
    date_from: date = Query(PANEL_START),
    date_to: date = Query(PANEL_END),
) -> dict:
    """Promotional display rate by department.

    The date window is resolved to week numbers HERE, before the analytical
    query is planned. fact_causal is partitioned on week_no; passing the window
    as dates and joining dim_week inside the query leaves all 102 partitions in
    the plan, because a scalar subquery is not evaluated until execution. Phase
    2 measured the difference at 2.09x. See docs/performance.md.
    """
    week_from, week_to = await resolve_week_range(date_from, date_to)
    rows = await fetch(
        Q.pick(Q.PROMO_LOCAL, Q.PROMO_PUBLISHED),
        {"week_from": week_from, "week_to": week_to},
        timeout_ms=60_000)
    return {"items": rows,
            "week_range": [week_from, week_to],
            "pruning_note": (f"date window resolved to weeks {week_from}-{week_to} "
                             "before planning, so partition pruning applies"),
            "meta": Meta().model_dump()}


@router.get("/products/pareto")
async def products_pareto(
    limit: int = Query(200, ge=1, le=1000),
    cursor: str | None = Query(None),
) -> Page:
    """Products by revenue with cumulative share, keyset-paginated.

    Keyset rather than OFFSET: 91,907 products with revenue, and OFFSET
    re-scans every skipped row on each page.
    """
    try:
        cur = Cursor.decode(cursor)
    except ValueError:
        raise HTTPException(400, "malformed cursor") from None

    after_rank = int(cur.last_values[0]) if cur else 0
    rows = await fetch(
        Q.pick(Q.PARETO_LOCAL, Q.PARETO_PUBLISHED),
        {"after_rank": after_rank, "limit": limit + 1},
        timeout_ms=60_000)

    has_more = len(rows) > limit
    items = rows[:limit]
    nxt = (Cursor(last_values=[items[-1]["revenue_rank"]]).encode()
           if has_more and items else None)
    return Page(items=items, next_cursor=nxt, has_more=has_more)


@router.get("/departments")
async def departments() -> dict:
    rows = await fetch(Q.pick(Q.DEPARTMENTS_LOCAL, Q.DEPARTMENTS_PUBLISHED))
    return {"items": rows}
