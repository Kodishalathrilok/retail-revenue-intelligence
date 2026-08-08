-- =============================================================================
-- 02 -- RFM segmentation
-- =============================================================================
-- BUSINESS QUESTION
--   Which households are most valuable, and which are slipping away? Group the
--   panel into actionable segments by Recency, Frequency and Monetary value.
--
-- TECHNIQUE
--   NTILE(5) over each of the three dimensions independently, producing scores
--   1-5, then a CASE expression mapping score combinations to named segments.
--   Quintiles are used rather than fixed thresholds so the segmentation adapts
--   to the distribution instead of encoding assumptions about it.
--
-- NOTE ON RECENCY
--   Recency is measured against the last day in the dataset (711), not today.
--   The panel ended at a fixed point, so "days since last purchase" is relative
--   to observation end.
-- =============================================================================

WITH bounds AS (
    SELECT max(day_number) AS last_day FROM fact_transactions
),

household_metrics AS (
    SELECT ft.household_key,
           (SELECT last_day FROM bounds) - max(ft.day_number) AS recency_days,
           count(DISTINCT ft.basket_id)                       AS frequency_baskets,
           round(sum(ft.sales_value), 2)                      AS monetary_value,
           round(sum(ft.sales_value)
                 / count(DISTINCT ft.basket_id), 2)           AS avg_basket_value
    FROM fact_transactions ft
    GROUP BY ft.household_key
),

-- Recency is reversed: fewer days since last purchase must score higher.
scored AS (
    SELECT *,
           ntile(5) OVER (ORDER BY recency_days DESC)      AS r_score,
           ntile(5) OVER (ORDER BY frequency_baskets)      AS f_score,
           ntile(5) OVER (ORDER BY monetary_value)         AS m_score
    FROM household_metrics
),

segmented AS (
    SELECT *,
           CASE
               WHEN r_score >= 4 AND f_score >= 4 AND m_score >= 4 THEN 'Champions'
               WHEN r_score >= 3 AND f_score >= 3                  THEN 'Loyal'
               WHEN r_score >= 4 AND f_score <= 2                  THEN 'New / Promising'
               WHEN r_score <= 2 AND f_score >= 4 AND m_score >= 4 THEN 'At Risk - high value'
               WHEN r_score <= 2 AND f_score >= 3                  THEN 'At Risk'
               WHEN r_score <= 2 AND f_score <= 2                  THEN 'Lapsed'
               ELSE 'Needs Attention'
           END AS segment
    FROM scored
)

SELECT segment,
       count(*)                                    AS households,
       round(100.0 * count(*) / sum(count(*)) OVER (), 1) AS pct_of_panel,
       round(avg(recency_days))                    AS avg_recency_days,
       round(avg(frequency_baskets))               AS avg_baskets,
       round(avg(monetary_value), 2)               AS avg_lifetime_value,
       round(avg(avg_basket_value), 2)             AS avg_basket_value,
       round(sum(monetary_value), 2)               AS segment_revenue,
       round(100.0 * sum(monetary_value)
             / sum(sum(monetary_value)) OVER (), 1) AS pct_of_revenue
FROM segmented
GROUP BY segment
ORDER BY segment_revenue DESC;
