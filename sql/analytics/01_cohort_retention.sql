-- =============================================================================
-- 01 -- Retention by acquisition tenure
-- =============================================================================
-- BUSINESS QUESTION
--   After a household's first purchase, what share is still buying N months
--   later? Does retention differ by the size of that first basket?
--
-- TECHNIQUE
--   Relative-tenure cohorts. Each household is placed on its own timeline from
--   its first purchase, and activity is measured in months elapsed since then.
--
-- WHY NOT CALENDAR COHORTS
--   dunnhumby is a PANEL: households are recruited at the start of observation
--   rather than acquired over time. Measured first-purchase distribution:
--     days 1-30    529 households (21.2%)
--     days 31-60   546 (21.8%)
--     days 61-90   687 (27.5%)
--     days 91-180  732 (29.3%)
--     day 181+       6 (0.2%)
--   Median first purchase is day 69; 99.8% arrive within 180 days of a 711-day
--   window. Calendar cohorts therefore crowd into the first six months, and a
--   "cohort comparison" would contrast early recruits against a handful of
--   stragglers whose late first purchase is an artefact of low shopping
--   frequency, not late acquisition.
--
--   Relative tenure is the honest framing for panel data: it measures how
--   engagement decays with time observed, and makes no acquisition claim.
--   The output carries the caveat in a column so it travels with the numbers.
-- =============================================================================

WITH first_purchase AS (
    SELECT household_key,
           min(date_key)   AS first_date,
           min(day_number) AS first_day
    FROM fact_transactions
    GROUP BY household_key
),

-- Segment on first-basket size, so retention can be compared across a genuine
-- behavioural split rather than an artificial time split.
first_basket AS (
    SELECT fp.household_key,
           fp.first_date,
           fp.first_day,
           sum(ft.sales_value) AS first_basket_value
    FROM first_purchase fp
    JOIN fact_transactions ft
      ON ft.household_key = fp.household_key
     AND ft.date_key      = fp.first_date
    GROUP BY fp.household_key, fp.first_date, fp.first_day
),

cohort AS (
    SELECT household_key,
           first_day,
           ntile(3) OVER (ORDER BY first_basket_value) AS spend_tercile
    FROM first_basket
),

-- Months of tenure, measured from each household's own first purchase.
activity AS (
    SELECT c.spend_tercile,
           (ft.day_number - c.first_day) / 30      AS tenure_month,
           count(DISTINCT ft.household_key)        AS active_households
    FROM fact_transactions ft
    JOIN cohort c ON c.household_key = ft.household_key
    WHERE (ft.day_number - c.first_day) / 30 <= 23
    GROUP BY 1, 2
),

cohort_size AS (
    SELECT spend_tercile, count(*) AS households
    FROM cohort
    GROUP BY spend_tercile
)

SELECT CASE a.spend_tercile
           WHEN 1 THEN '1 - smallest first basket'
           WHEN 2 THEN '2 - middle'
           WHEN 3 THEN '3 - largest first basket'
       END                                              AS segment,
       a.tenure_month,
       s.households                                     AS cohort_size,
       a.active_households,
       round(100.0 * a.active_households / s.households, 1) AS retention_pct,
       'relative tenure, not calendar cohort (panel data)' AS basis
FROM activity a
JOIN cohort_size s ON s.spend_tercile = a.spend_tercile
ORDER BY a.spend_tercile, a.tenure_month;
