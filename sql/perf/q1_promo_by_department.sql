-- Q1 -- MECHANISM: partition pruning failure
--
-- Promotional display rate by department over a date window.
--
-- fact_causal is range-partitioned on week_no across 102 partitions. This query
-- expresses its time filter as a date range on dim_week, so the partition key
-- is never constrained directly and the planner cannot prune at plan time. It
-- must scan every partition and discard most rows after the join.
--
-- Expect: "Partitions removed: 0", all 102 partitions appearing in the plan.
SELECT p.department,
       count(*)                                            AS promo_rows,
       count(*) FILTER (WHERE fc.display <> '0')           AS on_display,
       round(100.0 * count(*) FILTER (WHERE fc.display <> '0')
             / nullif(count(*), 0), 2)                     AS display_pct
FROM fact_causal fc
JOIN dim_week    w ON w.week_no    = fc.week_no
JOIN dim_product p ON p.product_id = fc.product_id
WHERE w.start_date >= DATE '2015-06-01'
  AND w.end_date   <  DATE '2015-12-01'
GROUP BY p.department
ORDER BY promo_rows DESC;
