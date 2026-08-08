-- =============================================================================
-- 04 -- Running totals and rolling averages
-- =============================================================================
-- BUSINESS QUESTION
--   How is revenue accumulating over the observation period, and what does the
--   underlying trend look like once weekly noise is smoothed out?
--
-- TECHNIQUE
--   Two window frames on the same partition, which is the point of the query:
--     * ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW -- cumulative total
--     * ROWS BETWEEN 3 PRECEDING AND 3 FOLLOWING        -- centred 7-week average
--   A centred frame is used for the trend line because a trailing average lags
--   the series by half its width, which misplaces turning points.
--
--   The frame is stated explicitly rather than relying on the default. The
--   default for an ORDER BY window is RANGE UNBOUNDED PRECEDING TO CURRENT ROW,
--   which silently merges peer rows -- harmless here but wrong the moment
--   duplicate sort keys appear.
-- =============================================================================

WITH weekly AS (
    SELECT w.week_no,
           w.start_date,
           w.is_partial_week,
           round(sum(ft.sales_value), 2)     AS revenue,
           count(DISTINCT ft.basket_id)      AS baskets,
           count(DISTINCT ft.household_key)  AS households
    FROM fact_transactions ft
    JOIN dim_week w ON w.week_no = ft.week_no
    GROUP BY w.week_no, w.start_date, w.is_partial_week
),

windowed AS (
    SELECT week_no,
           start_date,
           is_partial_week,
           revenue,
           baskets,
           households,
           round(revenue / nullif(baskets, 0), 2) AS avg_basket_value,

           sum(revenue) OVER (ORDER BY week_no
                              ROWS BETWEEN UNBOUNDED PRECEDING
                                       AND CURRENT ROW)          AS cumulative_revenue,

           round(avg(revenue) OVER (ORDER BY week_no
                                    ROWS BETWEEN 3 PRECEDING
                                             AND 3 FOLLOWING), 2) AS rolling_7wk_avg,

           round(revenue - lag(revenue) OVER (ORDER BY week_no), 2) AS wow_change
    FROM weekly
)

SELECT week_no,
       start_date,
       revenue,
       baskets,
       households,
       avg_basket_value,
       cumulative_revenue,
       rolling_7wk_avg,
       wow_change,
       round(100.0 * cumulative_revenue
             / sum(revenue) OVER (), 1) AS pct_of_total_to_date,
       -- Weeks 1 and 102 are 5 and 6 days rather than 7; their revenue is not
       -- comparable to a full week and the flag travels with the row.
       is_partial_week
FROM windowed
ORDER BY week_no;
