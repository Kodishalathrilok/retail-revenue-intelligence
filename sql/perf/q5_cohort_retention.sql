-- Q5 -- MECHANISM: repeated full-scan aggregation
--
-- Monthly cohort retention: for each acquisition-month cohort, the share still
-- active in each later month.
--
-- Every call scans fact_transactions twice -- once to derive each household's
-- first purchase, once to measure activity -- then aggregates COUNT(DISTINCT)
-- per cohort/month pair and applies a window function. Nothing about the inputs
-- changes between calls, so the whole computation is repeated work.
--
-- ANALYTICAL CAVEAT: dunnhumby is a panel, so most households transact from the
-- start of the observation period and the cohorts are heavily concentrated in
-- the first month. That limits what the retention curve means analytically, and
-- Phase 3 addresses it. It does not affect what this query costs to run, which
-- is what Phase 2 is measuring.
WITH first_purchase AS (
    SELECT household_key, min(date_key) AS first_date
    FROM fact_transactions
    GROUP BY household_key
),
cohort AS (
    SELECT household_key,
           date_trunc('month', first_date)::date AS cohort_month
    FROM first_purchase
),
activity AS (
    SELECT c.cohort_month,
           date_trunc('month', ft.date_key)::date AS active_month,
           count(DISTINCT ft.household_key)       AS active_households
    FROM fact_transactions ft
    JOIN cohort c ON c.household_key = ft.household_key
    GROUP BY 1, 2
)
SELECT cohort_month,
       active_month,
       ((EXTRACT(YEAR  FROM age(active_month, cohort_month)) * 12)
      +  EXTRACT(MONTH FROM age(active_month, cohort_month)))::int AS month_offset,
       active_households,
       round(100.0 * active_households
             / first_value(active_households)
               OVER (PARTITION BY cohort_month ORDER BY active_month), 2) AS retention_pct
FROM activity
ORDER BY cohort_month, active_month;
