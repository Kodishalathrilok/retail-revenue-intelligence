-- Secondary indexes, applied AFTER bulk load.
--
-- Building an index once over a finished table is far cheaper than maintaining
-- it across 36.8M individual inserts.
--
-- This is the Phase 1 baseline only -- the indexes the model obviously needs to
-- support its foreign keys and the most common access paths. Phase 2 adds
-- indexes in response to measured EXPLAIN (ANALYZE, BUFFERS) output, and
-- docs/performance.md records the before/after. Guessing at indexes now would
-- destroy that comparison by removing the "before".

-- fact_transactions: PK already covers (date_key, basket_id, product_id).
CREATE INDEX IF NOT EXISTS ix_ftx_household ON fact_transactions (household_key);
CREATE INDEX IF NOT EXISTS ix_ftx_product   ON fact_transactions (product_id);
CREATE INDEX IF NOT EXISTS ix_ftx_store     ON fact_transactions (store_id);
CREATE INDEX IF NOT EXISTS ix_ftx_week      ON fact_transactions (week_no);
-- Basket reconstruction (market basket affinity, Phase 3) always groups by it.
CREATE INDEX IF NOT EXISTS ix_ftx_basket    ON fact_transactions (basket_id);

-- fact_causal's PRIMARY KEY is created HERE rather than in the table DDL.
--
-- Building the unique index once over the finished table is dramatically
-- cheaper than maintaining it across 36.8M partitioned inserts -- the original
-- ordering, with the PK declared inline, ran 75.2 minutes. Postgres propagates
-- this to all 93 partitions automatically.
ALTER TABLE fact_causal
    ADD CONSTRAINT fact_causal_pkey PRIMARY KEY (week_no, product_id, store_id);

-- With the PK in place, week-leading access is covered. The reverse direction
-- -- "every week this product was promoted" -- is not, and is the join
-- direction Phase 6c uses.
CREATE INDEX IF NOT EXISTS ix_fc_product_week ON fact_causal (product_id, week_no);
CREATE INDEX IF NOT EXISTS ix_fc_store_week   ON fact_causal (store_id, week_no);

-- Bridges: PKs cover the leading column; index the trailing one for reverse
-- lookups ("which campaigns included this household").
CREATE INDEX IF NOT EXISTS ix_bch_household ON bridge_campaign_household (household_key);
CREATE INDEX IF NOT EXISTS ix_bcp_product   ON bridge_coupon_product (product_id);
CREATE INDEX IF NOT EXISTS ix_bcc_campaign  ON bridge_coupon_campaign (campaign_id);

-- dim_date is small but filtered constantly by the time-series queries.
CREATE INDEX IF NOT EXISTS ix_dim_date_month ON dim_date (month_start);
CREATE INDEX IF NOT EXISTS ix_dim_date_week  ON dim_date (week_no);

ANALYZE;
