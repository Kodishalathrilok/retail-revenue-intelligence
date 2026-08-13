"""Tier-aware SQL for the analytics endpoints.

Every endpoint has two variants:

  LOCAL      -- computes from the star schema (fact_transactions, fact_causal)
  PUBLISHED  -- reads a pub_* table that already holds the computed result

They are NOT interchangeable, and the split is not a convenience. The published
tier contains no fact tables at all: 3,676 MB of them stays local. An endpoint
written against facts does not degrade on the published tier, it returns 500.

The published variants are deliberately dull -- mostly `SELECT * FROM pub_x`.
That is the design working. All the computation happened once, locally, at
publish time, and `pub_manifest` records when.
"""

from __future__ import annotations

from rrip.config import settings

# --------------------------------------------------------------------------
# overview
# --------------------------------------------------------------------------

OVERVIEW_LOCAL = """
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
"""

# Aggregated from the weekly tables. total_units is not published -- summing
# quantity requires the fact table, and the dashboard does not use it.
OVERVIEW_PUBLISHED = """
    SELECT total_revenue, total_baskets, total_households, avg_basket_value,
           total_units, weeks_covered
    FROM pub_overview_totals
    WHERE department = coalesce(%(department)s::text, '(all)')
"""

# NOTE on the date window: the published overview ignores date_from/date_to and
# reports the whole panel. That is a real limitation, not an oversight.
#
# COUNT(DISTINCT household_key) over an arbitrary window cannot be reconstructed
# from weekly aggregates -- summing double-counts households active in several
# weeks, and max() undercounts them. An earlier version used max() and reported
# 1,428 households against the correct 2,500; the cross-tier comparison caught
# it. Publishing an exact figure per window would mean publishing every window.
#
# The endpoint therefore returns panel-wide totals on this tier and says so in
# the response, rather than returning a number that is quietly wrong.

# --------------------------------------------------------------------------
# weekly revenue
# --------------------------------------------------------------------------

WEEKLY_LOCAL = """
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
"""

# The all-departments case reads the precomputed cumulative and rolling values
# directly. The filtered case recomputes the window functions over the
# department slice -- 102 rows, so the cost is trivial and the alternative is
# publishing a cumulative column per department.
WEEKLY_PUBLISHED = """
    WITH base AS (
        SELECT r.week_no, dw.start_date, dw.is_partial_week,
               r.revenue, r.baskets, r.households
        FROM pub_weekly_revenue r
        JOIN pub_dim_week dw ON dw.week_no = r.week_no
        WHERE %(department)s::text IS NULL
          AND dw.end_date >= %(date_from)s AND dw.start_date <= %(date_to)s
        UNION ALL
        SELECT d.week_no, dw.start_date, dw.is_partial_week,
               d.revenue, d.baskets, d.households
        FROM pub_weekly_revenue_by_dept d
        JOIN pub_dim_week dw ON dw.week_no = d.week_no
        WHERE d.department = %(department)s::text
          AND dw.end_date >= %(date_from)s AND dw.start_date <= %(date_to)s
    )
    SELECT week_no, start_date, is_partial_week, revenue, baskets, households,
           sum(revenue) OVER (ORDER BY week_no
                              ROWS BETWEEN UNBOUNDED PRECEDING
                                       AND CURRENT ROW) AS cumulative_revenue,
           round(avg(revenue) OVER (ORDER BY week_no
                                    ROWS BETWEEN 3 PRECEDING
                                             AND 3 FOLLOWING), 2) AS rolling_7wk_avg
    FROM base ORDER BY week_no
"""

# --------------------------------------------------------------------------
# the rest -- published variants are straight reads
# --------------------------------------------------------------------------

RFM_PUBLISHED = """
    SELECT segment, households, pct_of_panel, avg_recency_days, avg_baskets,
           avg_lifetime_value, segment_revenue, pct_of_revenue
    FROM pub_rfm_segments ORDER BY segment_revenue DESC
"""

RETENTION_PUBLISHED = """
    SELECT segment, tenure_month, cohort_size, active_households, retention_pct
    FROM pub_retention_tenure ORDER BY segment, tenure_month
"""

PROMO_PUBLISHED = """
    SELECT department, sum(promo_rows)::bigint AS promo_rows,
           sum(on_display)::bigint AS on_display,
           sum(in_mailer)::bigint  AS in_mailer,
           round(100.0 * sum(on_display) / nullif(sum(promo_rows), 0), 2) AS display_pct
    FROM pub_promo_exposure
    WHERE week_no BETWEEN %(week_from)s AND %(week_to)s
    GROUP BY department ORDER BY promo_rows DESC
"""

PARETO_PUBLISHED = """
    SELECT product_id, commodity_desc, department, revenue, revenue_rank,
           cumulative_pct
    FROM pub_pareto_products
    WHERE revenue_rank > %(after_rank)s
    ORDER BY revenue_rank LIMIT %(limit)s
"""

DEPARTMENTS_PUBLISHED = """
    SELECT department, count(*) AS products FROM pub_dim_product
    WHERE department IS NOT NULL GROUP BY department ORDER BY department
"""

ANOMALIES_PUBLISHED = """
    SELECT week_no, start_date, revenue, is_partial_week, z_score, mean_revenue
    FROM pub_anomalies
    WHERE abs(z_score) >= %(z)s
    ORDER BY abs(z_score) DESC
"""

WEEK_RANGE_PUBLISHED = """
    SELECT coalesce(min(week_no), 1) AS week_from,
           coalesce(max(week_no), 102) AS week_to
    FROM pub_dim_week
    WHERE end_date >= %(date_from)s AND start_date <= %(date_to)s
"""


def pick(local: str, published: str) -> str:
    """Return the variant matching the configured tier."""
    return published if settings.is_published else local


# --------------------------------------------------------------------------
# Local variants for the remaining endpoints. These mirror sql/analytics/,
# and are the versions that compute from the star schema.
# --------------------------------------------------------------------------

RFM_LOCAL = """
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
"""

RETENTION_LOCAL = """
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
"""

PROMO_LOCAL = """
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
"""

PARETO_LOCAL = """
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
"""

DEPARTMENTS_LOCAL = """
    SELECT department, count(*) AS products FROM dim_product
    WHERE department IS NOT NULL GROUP BY department ORDER BY department
"""

ANOMALIES_LOCAL = """
    WITH weekly AS (
        SELECT w.week_no, w.start_date, w.is_partial_week,
               round(sum(ft.sales_value), 2) AS revenue
        FROM fact_transactions ft
        JOIN dim_week w ON w.week_no = ft.week_no
        GROUP BY w.week_no, w.start_date, w.is_partial_week
    ),
    stats AS (
        SELECT avg(revenue) AS mean_rev, stddev_samp(revenue) AS sd_rev
        FROM weekly WHERE NOT is_partial_week
    )
    SELECT w.week_no, w.start_date, w.revenue, w.is_partial_week,
           round(((w.revenue - s.mean_rev) / nullif(s.sd_rev, 0))::numeric, 3) AS z_score,
           round(s.mean_rev, 2) AS mean_revenue
    FROM weekly w CROSS JOIN stats s
    WHERE abs((w.revenue - s.mean_rev) / nullif(s.sd_rev, 0)) >= %(z)s
    ORDER BY abs((w.revenue - s.mean_rev) / nullif(s.sd_rev, 0)) DESC
"""

WEEK_RANGE_LOCAL = """
    SELECT coalesce(min(week_no), 1)   AS week_from,
           coalesce(max(week_no), 102) AS week_to
    FROM dim_week
    WHERE end_date >= %(date_from)s AND start_date <= %(date_to)s
"""
