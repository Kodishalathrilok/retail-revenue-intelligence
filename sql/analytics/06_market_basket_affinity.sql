-- =============================================================================
-- 06 -- Market basket affinity (co-occurrence, support, confidence, lift)
-- =============================================================================
-- BUSINESS QUESTION
--   Which product pairs are bought together more often than chance would
--   predict? Those pairs are candidates for placement, bundling and
--   cross-promotion.
--
-- TECHNIQUE
--   Self-join on basket_id with product_a < product_b to generate each unordered
--   pair exactly once, then the standard association-rule measures:
--
--     support(A,B)    = baskets containing both / all baskets
--     confidence(A→B) = baskets containing both / baskets containing A
--     lift(A,B)       = support(A,B) / (support(A) * support(B))
--
--   Lift is the measure that matters. Confidence alone rewards popular
--   products: bananas follow everything, which says nothing about bananas.
--   Lift divides out both base rates, so lift > 1 means genuinely more often
--   than independence predicts.
--
--   Analysis runs at COMMODITY level rather than product level. 92,353 products
--   produce mostly pairs seen two or three times, where lift is dominated by
--   sampling noise; commodities give populated cells and interpretable rules.
-- =============================================================================

WITH basket_commodity AS (
    -- One row per (basket, commodity) -- a basket with three yoghurts counts once.
    SELECT DISTINCT
           ft.basket_id,
           p.commodity_desc AS commodity
    FROM fact_transactions ft
    JOIN dim_product p ON p.product_id = ft.product_id
    WHERE p.commodity_desc IS NOT NULL
      AND ft.quantity BETWEEN 1 AND 1000
),

totals AS (
    SELECT count(DISTINCT basket_id) AS all_baskets FROM basket_commodity
),

item_support AS (
    SELECT commodity,
           count(*)                                             AS baskets,
           count(*)::numeric / (SELECT all_baskets FROM totals)  AS support
    FROM basket_commodity
    GROUP BY commodity
    HAVING count(*) >= 500          -- ignore commodities too rare to be stable
),

pairs AS (
    SELECT a.commodity AS commodity_a,
           b.commodity AS commodity_b,
           count(*)    AS pair_baskets
    FROM basket_commodity a
    JOIN basket_commodity b
      ON a.basket_id = b.basket_id
     AND a.commodity < b.commodity
    JOIN item_support sa ON sa.commodity = a.commodity
    JOIN item_support sb ON sb.commodity = b.commodity
    GROUP BY a.commodity, b.commodity
    HAVING count(*) >= 200
)

SELECT p.commodity_a,
       p.commodity_b,
       p.pair_baskets,
       round(p.pair_baskets::numeric
             / (SELECT all_baskets FROM totals), 5)          AS support,
       round(p.pair_baskets::numeric / sa.baskets, 4)        AS confidence_a_to_b,
       round(p.pair_baskets::numeric / sb.baskets, 4)        AS confidence_b_to_a,
       round((p.pair_baskets::numeric / (SELECT all_baskets FROM totals))
             / (sa.support * sb.support), 3)                 AS lift
FROM pairs p
JOIN item_support sa ON sa.commodity = p.commodity_a
JOIN item_support sb ON sb.commodity = p.commodity_b
WHERE (p.pair_baskets::numeric / (SELECT all_baskets FROM totals))
      / (sa.support * sb.support) > 1.0
ORDER BY lift DESC
LIMIT 100;
