-- Q2 -- MECHANISM: sort / hash aggregate spill to disk
--
-- Market basket co-occurrence: how often two products appear in the same basket.
--
-- 276,484 baskets self-joined on basket_id produce roughly 12M candidate pairs.
-- Aggregating those pairs needs far more than work_mem = 32MB, so the aggregate
-- spills to temporary files. basket_id is not the partition key, so the join
-- also crosses all 24 fact_transactions partitions.
--
-- Expect: "Sort Method: external merge  Disk: NNNkB", or a HashAggregate
-- reporting Batches > 1 with Disk Usage, and non-zero temp_read/temp_written.
SELECT a.product_id                AS product_a,
       b.product_id                AS product_b,
       count(*)                    AS co_occurrence,
       count(DISTINCT a.household_key) AS households
FROM fact_transactions a
JOIN fact_transactions b
  ON a.basket_id  = b.basket_id
 AND a.product_id < b.product_id
GROUP BY a.product_id, b.product_id
HAVING count(*) >= 50
ORDER BY co_occurrence DESC
LIMIT 200;
