-- Q4 -- MECHANISM: join-order misestimate from correlated columns
--
-- Revenue and promotional exposure by department and week, joining transactions
-- to promo exposure on the full (product, store, week) key.
--
-- store_id and week_no are correlated in fact_causal: only 115 stores appear at
-- all, and coverage runs weeks 9-101 rather than 1-102. The planner assumes
-- column independence, so it multiplies selectivities and badly underestimates
-- the rows surviving the filters -- which drives it toward the wrong join order
-- and, on a 36.8M-row table, the wrong join strategy.
--
-- Expect: estimated vs actual rows diverging by more than 10x on the
-- fact_causal join node.
SELECT p.department,
       fc.week_no,
       count(*)                                   AS matched_lines,
       round(sum(ft.sales_value), 2)              AS revenue,
       count(*) FILTER (WHERE fc.mailer  <> '0')  AS mailer_lines,
       count(*) FILTER (WHERE fc.display <> '0')  AS display_lines
FROM fact_transactions ft
JOIN fact_causal fc
  ON  fc.product_id = ft.product_id
 AND  fc.store_id   = ft.store_id
 AND  fc.week_no    = ft.week_no
JOIN dim_product p ON p.product_id = ft.product_id
JOIN dim_store   s ON s.store_id   = ft.store_id
WHERE fc.week_no BETWEEN 40 AND 60
  AND s.has_promo_coverage
  AND ft.quantity BETWEEN 1 AND 1000
GROUP BY p.department, fc.week_no
ORDER BY revenue DESC NULLS LAST
LIMIT 200;
