-- Q3 -- MECHANISM: index-unusable (non-sargable) predicate
--
-- Repeat-purchase behaviour for a commodity family.
--
-- dim_product carries a btree index on commodity_desc (ix_dim_product_commodity),
-- and under C collation a plain LIKE 'SOFT DRINK%' would use it. Wrapping the
-- column in upper() makes the predicate non-sargable: the index cannot be used
-- because it stores the raw values, not their uppercase form.
--
-- The index exists and is reasonable. The predicate form defeats it. That is a
-- different failure from simply lacking an index.
--
-- Expect: Seq Scan on dim_product with "Filter: (upper(commodity_desc) ~~ ...)"
-- and no Index Cond.
SELECT p.commodity_desc,
       count(*)                             AS lines,
       count(DISTINCT ft.household_key)     AS households,
       count(DISTINCT ft.basket_id)         AS baskets,
       round(sum(ft.sales_value), 2)        AS revenue,
       round(count(*)::numeric
             / nullif(count(DISTINCT ft.household_key), 0), 3) AS lines_per_household
FROM fact_transactions ft
JOIN dim_product p ON p.product_id = ft.product_id
WHERE upper(p.commodity_desc) LIKE 'SOFT DRINK%'
GROUP BY p.commodity_desc
ORDER BY lines DESC;
