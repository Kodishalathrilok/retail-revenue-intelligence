-- =============================================================================
-- 03 -- Purchase funnel: order of acquisition within a basket
-- =============================================================================
-- BUSINESS QUESTION
--   Within a shopping trip, which departments do households buy first, and what
--   tends to follow? Early-basket departments are destination categories; late
--   ones are impulse or top-up.
--
-- TECHNIQUE
--   LAG() and LEAD() over each basket, ordered by the sequence in which lines
--   were added. dunnhumby has no add-to-cart order, so sequence is reconstructed
--   from transaction time within the basket, with product_id as a deterministic
--   tiebreak so the result is reproducible rather than dependent on scan order.
--
-- HONEST LIMITATION
--   trans_time is recorded per transaction, not per line, so most lines in a
--   basket share a timestamp. This measures department adjacency within a trip
--   rather than true chronological order. Stated here because the query would
--   otherwise look like it knows more than it does.
-- =============================================================================

WITH basket_lines AS (
    SELECT ft.basket_id,
           ft.household_key,
           p.department,
           ft.sales_value,
           row_number() OVER (PARTITION BY ft.basket_id
                              ORDER BY ft.trans_time, ft.product_id) AS line_seq,
           count(*)     OVER (PARTITION BY ft.basket_id)             AS basket_lines
    FROM fact_transactions ft
    JOIN dim_product p ON p.product_id = ft.product_id
    WHERE p.department IS NOT NULL
      AND ft.quantity BETWEEN 1 AND 1000        -- exclude returns and weighted goods
),

-- Only multi-line baskets can have a sequence worth analysing.
sequenced AS (
    SELECT basket_id,
           department,
           line_seq,
           basket_lines,
           lag(department)  OVER (PARTITION BY basket_id ORDER BY line_seq) AS prev_department,
           lead(department) OVER (PARTITION BY basket_id ORDER BY line_seq) AS next_department
    FROM basket_lines
    WHERE basket_lines BETWEEN 2 AND 50
),

position_stats AS (
    SELECT department,
           count(*)                                                    AS lines,
           round(avg(line_seq::numeric / basket_lines), 3)             AS avg_relative_position,
           count(*) FILTER (WHERE line_seq = 1)                        AS times_first,
           count(*) FILTER (WHERE next_department IS NULL)             AS times_last
    FROM sequenced
    GROUP BY department
),

-- The most common department to follow each department.
top_successor AS (
    SELECT DISTINCT ON (department)
           department,
           next_department AS most_common_next,
           count(*)        AS transitions
    FROM sequenced
    WHERE next_department IS NOT NULL
      AND next_department <> department
    GROUP BY department, next_department
    ORDER BY department, count(*) DESC
)

SELECT ps.department,
       ps.lines,
       ps.avg_relative_position,
       ps.times_first,
       ps.times_last,
       ts.most_common_next,
       ts.transitions
FROM position_stats ps
LEFT JOIN top_successor ts ON ts.department = ps.department
WHERE ps.lines >= 1000
ORDER BY ps.avg_relative_position;
