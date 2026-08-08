-- =============================================================================
-- 05 -- Period-over-period comparison (MoM and YoY)
-- =============================================================================
-- BUSINESS QUESTION
--   How does each month compare with the month before, and with the same month
--   a year earlier?
--
-- TECHNIQUE
--   LAG() with two different offsets over a month-grain aggregate: OFFSET 1 for
--   month-over-month, OFFSET 12 for year-over-year. Both read from the same
--   ordered partition, so no self-join is needed.
--
-- WHY A CALENDAR SPINE
--   LAG(x, 12) is only a year-over-year comparison if the rows are contiguous
--   months. A month with no transactions would shift every subsequent
--   comparison by one position without any error. The month spine is generated
--   from dim_date and left-joined, so gaps become zero-revenue rows and the
--   offsets stay meaningful.
--
-- COVERAGE
--   The panel spans 711 days (~23 months), so only ~11 months have a
--   year-earlier counterpart. Those without are NULL rather than omitted, so
--   the absence is visible.
-- =============================================================================

WITH month_spine AS (
    SELECT DISTINCT month_start
    FROM dim_date
),

monthly AS (
    SELECT s.month_start,
           coalesce(round(sum(ft.sales_value), 2), 0)     AS revenue,
           count(DISTINCT ft.basket_id)                   AS baskets,
           count(DISTINCT ft.household_key)               AS households,
           coalesce(round(sum(ft.sales_value)
                          / nullif(count(DISTINCT ft.basket_id), 0), 2), 0) AS avg_basket
    FROM month_spine s
    LEFT JOIN dim_date d          ON d.month_start = s.month_start
    LEFT JOIN fact_transactions ft ON ft.date_key  = d.date_key
    GROUP BY s.month_start
),

compared AS (
    SELECT month_start,
           revenue,
           baskets,
           households,
           avg_basket,
           lag(revenue, 1)  OVER (ORDER BY month_start) AS prev_month_revenue,
           lag(revenue, 12) OVER (ORDER BY month_start) AS prev_year_revenue
    FROM monthly
)

SELECT month_start,
       revenue,
       baskets,
       households,
       avg_basket,

       prev_month_revenue,
       round(revenue - prev_month_revenue, 2) AS mom_change,
       round(100.0 * (revenue - prev_month_revenue)
             / nullif(prev_month_revenue, 0), 1) AS mom_pct,

       prev_year_revenue,
       round(revenue - prev_year_revenue, 2) AS yoy_change,
       round(100.0 * (revenue - prev_year_revenue)
             / nullif(prev_year_revenue, 0), 1) AS yoy_pct
FROM compared
ORDER BY month_start;
