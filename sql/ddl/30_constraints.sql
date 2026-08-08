-- Foreign keys, applied AFTER bulk load.
--
-- Validating referential integrity row-by-row during COPY of 36.8M rows is the
-- most expensive possible ordering. Adding the constraints afterwards validates
-- each table once, with a sequential scan the planner can parallelise.
--
-- These are real constraints, not documentation: Phase 0 measured 0 orphan
-- products and 0 orphan coupons, so a violation here means the load is wrong.

-- DROP IF EXISTS before each ADD: Postgres has no ADD CONSTRAINT IF NOT EXISTS,
-- and the loader resumes by re-running steps, so this file must be re-runnable.
-- A resumed load that failed here on "constraint already exists" is what
-- prompted this.
ALTER TABLE fact_transactions
    DROP CONSTRAINT IF EXISTS fk_ftx_date,
    ADD  CONSTRAINT fk_ftx_date     FOREIGN KEY (date_key)      REFERENCES dim_date (date_key),
    DROP CONSTRAINT IF EXISTS fk_ftx_product,
    ADD  CONSTRAINT fk_ftx_product  FOREIGN KEY (product_id)    REFERENCES dim_product (product_id),
    DROP CONSTRAINT IF EXISTS fk_ftx_hh,
    ADD  CONSTRAINT fk_ftx_hh       FOREIGN KEY (household_key) REFERENCES dim_household (household_key),
    DROP CONSTRAINT IF EXISTS fk_ftx_store,
    ADD  CONSTRAINT fk_ftx_store    FOREIGN KEY (store_id)      REFERENCES dim_store (store_id),
    DROP CONSTRAINT IF EXISTS fk_ftx_week,
    ADD  CONSTRAINT fk_ftx_week     FOREIGN KEY (week_no)       REFERENCES dim_week (week_no);

-- fact_causal gets referential and domain enforcement rather than the
-- recomputation applied to fact_transactions, and the reason is a constraint of
-- the source, not a design preference: causal_data has no day column, so there
-- is nothing to recompute week_no from. The FK to dim_week is what guarantees
-- its 36.8M week values stay inside the same time model dim_date defines.
ALTER TABLE fact_causal
    DROP CONSTRAINT IF EXISTS fk_fc_week,
    ADD  CONSTRAINT fk_fc_week    FOREIGN KEY (week_no)    REFERENCES dim_week (week_no),
    DROP CONSTRAINT IF EXISTS fk_fc_product,
    ADD  CONSTRAINT fk_fc_product FOREIGN KEY (product_id) REFERENCES dim_product (product_id),
    DROP CONSTRAINT IF EXISTS fk_fc_store,
    ADD  CONSTRAINT fk_fc_store   FOREIGN KEY (store_id)   REFERENCES dim_store (store_id);

ALTER TABLE fact_coupon_redemption
    DROP CONSTRAINT IF EXISTS fk_fcr_hh,
    ADD  CONSTRAINT fk_fcr_hh       FOREIGN KEY (household_key) REFERENCES dim_household (household_key),
    DROP CONSTRAINT IF EXISTS fk_fcr_date,
    ADD  CONSTRAINT fk_fcr_date     FOREIGN KEY (redemption_date) REFERENCES dim_date (date_key),
    DROP CONSTRAINT IF EXISTS fk_fcr_coupon,
    ADD  CONSTRAINT fk_fcr_coupon   FOREIGN KEY (coupon_upc)    REFERENCES dim_coupon (coupon_upc),
    DROP CONSTRAINT IF EXISTS fk_fcr_campaign,
    ADD  CONSTRAINT fk_fcr_campaign FOREIGN KEY (campaign_id)   REFERENCES dim_campaign (campaign_id);
