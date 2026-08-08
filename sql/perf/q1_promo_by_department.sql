-- Q1 -- MECHANISM: partition pruning failure
-- STATUS: OPTIMIZED (predicate rewrite, second attempt -- see note below)
--
-- Promotional display rate by department over a date window.
--
-- THE FIX: constrain the partition key with values known AT PLAN TIME.
--
-- fact_causal is range-partitioned on week_no. The original form expressed its
-- time filter as a date range on dim_week, so the partition key was never
-- constrained and all 102 partitions were scanned.
--
-- FIRST ATTEMPT, WHICH FAILED: adding
--     WHERE fc.week_no >= (SELECT min(week_no) FROM dim_week WHERE ...)
-- derived the bounds from dim_week so the query would survive a change to the
-- calendar anchor. It did constrain the partition key, and it did make the
-- query faster -- but it did NOT prune. All 102 partitions stayed in the plan.
-- A scalar subquery is not evaluated until execution, so the planner has no
-- value to prune against and must keep every partition. The speedup came from
-- switching some partitions to index scans, not from pruning.
--
-- Partition pruning at plan time requires plan-time constants. Callers must
-- therefore pass the week range as literals or bound parameters, resolving the
-- date window to week numbers before the query is planned -- which the API
-- layer does against dim_week.
--
-- Expect after: 27 of 102 partitions in the plan. Subplans Removed stays 0
-- because the partitions are eliminated at PLAN time and never enter the plan
-- at all; that counter reports EXECUTION-time pruning.
SELECT p.department,
       count(*)                                            AS promo_rows,
       count(*) FILTER (WHERE fc.display <> '0')           AS on_display,
       round(100.0 * count(*) FILTER (WHERE fc.display <> '0')
             / nullif(count(*), 0), 2)                     AS display_pct
FROM fact_causal fc
JOIN dim_week    w ON w.week_no    = fc.week_no
JOIN dim_product p ON p.product_id = fc.product_id
WHERE fc.week_no BETWEEN 22 AND 48          -- 2015-06-01 .. 2015-12-01
  AND w.start_date >= DATE '2015-06-01'
  AND w.end_date   <  DATE '2015-12-01'
GROUP BY p.department
ORDER BY promo_rows DESC;
