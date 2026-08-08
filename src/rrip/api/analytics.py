"""Analytical endpoints over the Phase 3 query library.

The SQL here is parameterised from sql/analytics/ rather than duplicated, where
the shape allows. Where an endpoint needs a filtered variant, the difference is
a WHERE clause on the same structure, not a reimplementation -- so the API and
the documented query library cannot drift apart.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException, Query

from rrip.api.db import fetch, fetch_one, resolve_week_range
from rrip.api.models import (
    PANEL_END,
    PANEL_START,
    Cursor,
    ExecutiveOverview,
    Meta,
    Page,
)

router = APIRouter(prefix="/api/v1", tags=["analytics"])


@router.get("/overview", response_model=ExecutiveOverview)
async def overview(
    date_from: date = Query(PANEL_START),
    date_to: date = Query(PANEL_END),
    department: str | None = Query(None),
) -> ExecutiveOverview:
    row = await fetch_one(
        """
        SELECT round(sum(ft.sales_value), 2)            AS total_revenue,
               count(DISTINCT ft.basket_id)             AS total_baskets,
               count(DISTINCT ft.household_key)         AS total_households,
               round(sum(ft.sales_value)
                     / nullif(count(DISTINCT ft.basket_id), 0), 2) AS avg_basket_value,
               coalesce(sum(ft.quantity) FILTER (WHERE NOT ft.is_weighted_item
                                             AND NOT ft.is_return), 0) AS total_units,
               count(DISTINCT ft.week_no)               AS weeks_covered
        FROM fact_transactions ft
        JOIN dim_product p ON p.product_id = ft.product_id
        WHERE ft.date_key BETWEEN %(date_from)s AND %(date_to)s
          AND (%(department)s::text IS NULL OR p.department = %(department)s::text)
        """,
        {"date_from": date_from, "date_to": date_to, "department": department},
        timeout_ms=30_000)

    if not row or row["total_revenue"] is None:
        raise HTTPException(404, "no data in the requested window")
    return ExecutiveOverview(**{k: v for k, v in row.items()}, meta=Meta())


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
        """
        WITH weekly AS (
            SELECT w.week_no, w.start_date, w.is_partial_week,
                   round(sum(ft.sales_value), 2)    AS revenue,
                   count(DISTINCT ft.basket_id)     AS baskets,
                   count(DISTINCT ft.household_key) AS households
            FROM fact_transactions ft
            JOIN dim_week w    ON w.week_no    = ft.week_no
            JOIN dim_product p ON p.product_id = ft.product_id
            WHERE ft.date_key BETWEEN %(date_from)s AND %(date_to)s
              AND (%(department)s::text IS NULL OR p.department = %(department)s::text)
            GROUP BY w.week_no, w.start_date, w.is_partial_week
        )
        SELECT week_no, start_date, is_partial_week, revenue, baskets, households,
               sum(revenue) OVER (ORDER BY week_no
                                  ROWS BETWEEN UNBOUNDED PRECEDING
                                           AND CURRENT ROW) AS cumulative_revenue,
               round(avg(revenue) OVER (ORDER BY week_no
                                        ROWS BETWEEN 3 PRECEDING
                                                 AND 3 FOLLOWING), 2) AS rolling_7wk_avg
        FROM weekly ORDER BY week_no
        """,
        {"date_from": date_from, "date_to": date_to, "department": department},
        timeout_ms=30_000)
    return {"items": rows, "meta": Meta().model_dump()}


@router.get("/segments/rfm")
async def rfm_segments() -> dict:
    """RFM segmentation. Recency is measured against panel end, not today."""
    rows = await fetch(
        """
        WITH bounds AS (SELECT max(day_number) AS last_day FROM fact_transactions),
        household_metrics AS (
            SELECT household_key,
                   (SELECT last_day FROM bounds) - max(day_number) AS recency_days,
                   count(DISTINCT basket_id)                       AS frequency_baskets,
                   round(sum(sales_value), 2)                      AS monetary_value
            FROM fact_transactions GROUP BY household_key
        ),
        scored AS (
            SELECT *, ntile(5) OVER (ORDER BY recency_days DESC) AS r_score,
                      ntile(5) OVER (ORDER BY frequency_baskets) AS f_score,
                      ntile(5) OVER (ORDER BY monetary_value)    AS m_score
            FROM household_metrics
        ),
        segmented AS (
            SELECT *, CASE
                WHEN r_score >= 4 AND f_score >= 4 AND m_score >= 4 THEN 'Champions'
                WHEN r_score >= 3 AND f_score >= 3                  THEN 'Loyal'
                WHEN r_score >= 4 AND f_score <= 2                  THEN 'New / Promising'
                WHEN r_score <= 2 AND f_score >= 4 AND m_score >= 4 THEN 'At Risk - high value'
                WHEN r_score <= 2 AND f_score >= 3                  THEN 'At Risk'
                WHEN r_score <= 2 AND f_score <= 2                  THEN 'Lapsed'
                ELSE 'Needs Attention' END AS segment
            FROM scored
        )
        SELECT segment, count(*) AS households,
               round(100.0 * count(*) / sum(count(*)) OVER (), 1) AS pct_of_panel,
               round(avg(recency_days))      AS avg_recency_days,
               round(avg(frequency_baskets)) AS avg_baskets,
               round(avg(monetary_value), 2) AS avg_lifetime_value,
               round(sum(monetary_value), 2) AS segment_revenue,
               round(100.0 * sum(monetary_value)
                     / sum(sum(monetary_value)) OVER (), 1) AS pct_of_revenue
        FROM segmented GROUP BY segment ORDER BY segment_revenue DESC
        """, timeout_ms=30_000)
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
        """
        WITH first_purchase AS (
            SELECT household_key, min(date_key) AS first_date,
                   min(day_number) AS first_day
            FROM fact_transactions GROUP BY household_key
        ),
        first_basket AS (
            SELECT fp.household_key, fp.first_day,
                   sum(ft.sales_value) AS first_basket_value
            FROM first_purchase fp
            JOIN fact_transactions ft ON ft.household_key = fp.household_key
                                     AND ft.date_key      = fp.first_date
            GROUP BY fp.household_key, fp.first_day
        ),
        cohort AS (
            SELECT household_key, first_day,
                   ntile(3) OVER (ORDER BY first_basket_value) AS spend_tercile
            FROM first_basket
        ),
        activity AS (
            SELECT c.spend_tercile, (ft.day_number - c.first_day) / 30 AS tenure_month,
                   count(DISTINCT ft.household_key) AS active_households
            FROM fact_transactions ft
            JOIN cohort c ON c.household_key = ft.household_key
            WHERE (ft.day_number - c.first_day) / 30 <= 23
            GROUP BY 1, 2
        ),
        cohort_size AS (SELECT spend_tercile, count(*) AS households FROM cohort GROUP BY 1)
        SELECT CASE a.spend_tercile WHEN 1 THEN 'Smallest first basket'
                                    WHEN 2 THEN 'Middle'
                                    ELSE 'Largest first basket' END AS segment,
               a.tenure_month, s.households AS cohort_size, a.active_households,
               round(100.0 * a.active_households / s.households, 1) AS retention_pct
        FROM activity a JOIN cohort_size s ON s.spend_tercile = a.spend_tercile
        ORDER BY a.spend_tercile, a.tenure_month
        """, timeout_ms=30_000)
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
        """
        SELECT p.department,
               count(*)                                  AS promo_rows,
               count(*) FILTER (WHERE fc.display <> '0') AS on_display,
               count(*) FILTER (WHERE fc.mailer  <> '0') AS in_mailer,
               round(100.0 * count(*) FILTER (WHERE fc.display <> '0')
                     / nullif(count(*), 0), 2)           AS display_pct
        FROM fact_causal fc
        JOIN dim_product p ON p.product_id = fc.product_id
        WHERE fc.week_no BETWEEN %(week_from)s AND %(week_to)s
        GROUP BY p.department ORDER BY promo_rows DESC
        """,
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
        """
        WITH product_revenue AS (
            SELECT ft.product_id, p.commodity_desc, p.department,
                   round(sum(ft.sales_value), 2) AS revenue
            FROM fact_transactions ft
            JOIN dim_product p ON p.product_id = ft.product_id
            WHERE ft.sales_value > 0
            GROUP BY ft.product_id, p.commodity_desc, p.department
        ),
        ranked AS (
            SELECT *, row_number() OVER (ORDER BY revenue DESC) AS revenue_rank,
                   sum(revenue) OVER (ORDER BY revenue DESC
                                      ROWS BETWEEN UNBOUNDED PRECEDING
                                               AND CURRENT ROW) AS cumulative_revenue,
                   sum(revenue) OVER () AS total_revenue
            FROM product_revenue
        )
        SELECT product_id, commodity_desc, department, revenue, revenue_rank,
               cumulative_revenue,
               round(100.0 * cumulative_revenue / total_revenue, 4) AS cumulative_pct
        FROM ranked
        WHERE revenue_rank > %(after_rank)s
        ORDER BY revenue_rank
        LIMIT %(limit)s
        """,
        {"after_rank": after_rank, "limit": limit + 1},
        timeout_ms=60_000)

    has_more = len(rows) > limit
    items = rows[:limit]
    nxt = (Cursor(last_values=[items[-1]["revenue_rank"]]).encode()
           if has_more and items else None)
    return Page(items=items, next_cursor=nxt, has_more=has_more)


@router.get("/departments")
async def departments() -> dict:
    rows = await fetch(
        """SELECT department, count(*) AS products FROM dim_product
           WHERE department IS NOT NULL GROUP BY department ORDER BY department""")
    return {"items": rows}
