-- =============================================================================
-- 08 -- Reorder rate by product and department
-- =============================================================================
-- BUSINESS QUESTION
--   Which products earn repeat purchases from the same household, and which are
--   bought once and never again? Reorder rate separates staples from
--   one-off or disappointing purchases, independent of raw popularity.
--
-- TECHNIQUE
--   Two definitions are computed, because they answer different questions and
--   are easy to conflate:
--
--     household_reorder_rate = households buying it more than once
--                            / households buying it at all
--       -- "of people who tried it, how many came back?"
--
--     line_reorder_rate      = purchase events beyond the first
--                            / all purchase events
--       -- "what share of its volume is repeat business?"
--
--   The first is a conversion measure, the second a volume measure. A product
--   bought weekly by a few households scores high on the second and moderately
--   on the first.
--
--   dunnhumby has no reordered flag (unlike Instacart), so both are derived by
--   counting distinct purchase days per household-product pair.
-- =============================================================================

WITH household_product AS (
    SELECT ft.household_key,
           ft.product_id,
           count(DISTINCT ft.date_key) AS purchase_occasions
    FROM fact_transactions ft
    WHERE ft.quantity BETWEEN 1 AND 1000     -- exclude returns and weighted goods
    GROUP BY ft.household_key, ft.product_id
),

product_stats AS (
    SELECT product_id,
           count(*)                                              AS households,
           count(*) FILTER (WHERE purchase_occasions > 1)        AS repeat_households,
           sum(purchase_occasions)                               AS total_occasions,
           sum(purchase_occasions - 1)                           AS repeat_occasions
    FROM household_product
    GROUP BY product_id
    HAVING count(*) >= 50           -- below this, the rate is noise
),

with_context AS (
    SELECT ps.*,
           p.department,
           p.commodity_desc,
           p.brand,
           round(100.0 * ps.repeat_households / ps.households, 1) AS household_reorder_rate,
           round(100.0 * ps.repeat_occasions  / ps.total_occasions, 1) AS line_reorder_rate
    FROM product_stats ps
    JOIN dim_product p ON p.product_id = ps.product_id
)

SELECT department,
       count(*)                                    AS products,
       round(avg(household_reorder_rate), 1)       AS avg_household_reorder_rate,
       round(avg(line_reorder_rate), 1)            AS avg_line_reorder_rate,
       round(percentile_cont(0.5) WITHIN GROUP (
             ORDER BY household_reorder_rate)::numeric, 1) AS median_household_reorder_rate,
       max(household_reorder_rate)                 AS best_rate,
       (array_agg(commodity_desc ORDER BY household_reorder_rate DESC))[1] AS most_reordered_commodity
FROM with_context
WHERE department IS NOT NULL
GROUP BY department
HAVING count(*) >= 10
ORDER BY avg_household_reorder_rate DESC;
