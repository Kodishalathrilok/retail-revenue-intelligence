-- =============================================================================
-- 07 -- Pareto analysis: cumulative revenue share
-- =============================================================================
-- BUSINESS QUESTION
--   How concentrated is revenue? What share of products, and of households,
--   generates 80% of it? Concentration determines whether growth effort should
--   go into the head or the tail.
--
-- TECHNIQUE
--   Cumulative sum via a window function, divided by the grand total from an
--   unbounded window over the same partition. Both the running total and the
--   denominator come from window functions, so the whole calculation is a single
--   pass with no self-join and no correlated subquery.
--
--   The 80% cut is found with a boolean flag on the cumulative share rather than
--   a LIMIT, because the row count that reaches 80% is the answer, not an input.
-- =============================================================================

WITH product_revenue AS (
    SELECT ft.product_id,
           p.commodity_desc,
           p.department,
           round(sum(ft.sales_value), 2) AS revenue
    FROM fact_transactions ft
    JOIN dim_product p ON p.product_id = ft.product_id
    WHERE ft.sales_value > 0
    GROUP BY ft.product_id, p.commodity_desc, p.department
),

ranked AS (
    SELECT product_id,
           commodity_desc,
           department,
           revenue,
           row_number() OVER (ORDER BY revenue DESC)          AS revenue_rank,
           sum(revenue) OVER (ORDER BY revenue DESC
                              ROWS BETWEEN UNBOUNDED PRECEDING
                                       AND CURRENT ROW)        AS cumulative_revenue,
           sum(revenue) OVER ()                                AS total_revenue,
           count(*)     OVER ()                                AS total_products
    FROM product_revenue
),

shares AS (
    SELECT *,
           round(100.0 * cumulative_revenue / total_revenue, 4) AS cumulative_pct,
           round(100.0 * revenue_rank / total_products, 4)      AS product_pct
    FROM ranked
)

-- Headline: where the 80% line falls.
SELECT 'products to reach 80% of revenue' AS metric,
       min(revenue_rank)::text            AS value,
       min(product_pct)::text || '%'      AS share_of_catalogue
FROM shares
WHERE cumulative_pct >= 80

UNION ALL

SELECT 'products to reach 50% of revenue',
       min(revenue_rank)::text,
       min(product_pct)::text || '%'
FROM shares
WHERE cumulative_pct >= 50

UNION ALL

SELECT 'total products with revenue',
       max(total_products)::text,
       '100%'
FROM shares

UNION ALL

SELECT 'revenue from top 1% of products',
       round(max(cumulative_pct) FILTER (WHERE product_pct <= 1), 1)::text || '%',
       '1%'
FROM shares;
