-- Fact tables and their partitions.
--
-- Foreign keys and secondary indexes are deliberately NOT here -- they are
-- created after bulk load (30_constraints.sql, 40_indexes.sql). Validating FKs
-- and maintaining indexes row-by-row during COPY of 36.8M rows is the single
-- most expensive way to load this data.

BEGIN;

-- ---------------------------------------------------------------------------
-- fact_transactions -- 2,595,732 rows
--
-- GRAIN: one row per (basket, product).
--
-- Phase 0 confirmed (basket_id, product_id) is unique across all 2,595,732
-- rows -- zero duplicates -- so this is a natural key and no surrogate is
-- introduced. date_key leads the PK because Postgres requires the partition
-- key to be part of it.
--
-- PARTITIONING: monthly by date_key, ~24 partitions of ~110k rows.
--
-- Honest note: at 2.6M rows this is close to over-engineering. A well-indexed
-- unpartitioned table would perform comparably for most queries here. It earns
-- its place on the time-series access pattern -- MoM/YoY and rolling windows
-- prune cleanly -- and not on size. The real partitioning payoff in this
-- schema is fact_causal, which is 14x larger. docs/performance.md reports the
-- measured difference rather than asserting a win.
-- ---------------------------------------------------------------------------
CREATE TABLE fact_transactions (
    date_key          DATE     NOT NULL,
    basket_id         BIGINT   NOT NULL,
    product_id        INTEGER  NOT NULL,
    household_key     INTEGER  NOT NULL,
    store_id          INTEGER  NOT NULL,
    day_number        SMALLINT NOT NULL,
    week_no           SMALLINT NOT NULL,
    trans_time        SMALLINT,                       -- HHMM as recorded

    quantity          INTEGER  NOT NULL,
    -- NUMERIC, not float: these get summed into revenue figures and binary
    -- floating point does not sum money exactly.
    sales_value       NUMERIC(10,2) NOT NULL,
    retail_disc       NUMERIC(10,2) NOT NULL,
    coupon_disc       NUMERIC(10,2) NOT NULL,
    coupon_match_disc NUMERIC(10,2) NOT NULL,

    -- sales_value is the NET amount charged; retail_disc is stored negative
    -- (1,303,026 negative vs 36 positive rows). Encoding the convention as a
    -- generated column stops it being re-derived inconsistently downstream --
    -- the sign is easy to get backwards and the result still looks plausible.
    gross_value NUMERIC(10,2)
        GENERATED ALWAYS AS (sales_value - retail_disc) STORED,

    -- 23,101 rows record weighted goods in GRAMS, not units. Any "units sold"
    -- metric that sums quantity across these is adding grams to counts.
    is_weighted_item BOOLEAN GENERATED ALWAYS AS (quantity > 1000) STORED,

    -- Returns appear as quantity <= 0 (14,466 rows). They are NOT negative
    -- sales_value -- there are zero negative sales_value rows in the source.
    is_return BOOLEAN GENERATED ALWAYS AS (quantity <= 0) STORED,

    PRIMARY KEY (date_key, basket_id, product_id)
) PARTITION BY RANGE (date_key);

COMMENT ON TABLE fact_transactions IS
    'Grain: one row per (basket, product). Natural PK -- 0 duplicate '
    '(basket_id, product_id) pairs in 2,595,732 source rows.';

-- ---------------------------------------------------------------------------
-- fact_causal -- 36,786,524 rows
--
-- GRAIN: one row per (product, store, week) -- promotional exposure.
--
-- PARTITIONING: by week_no, one partition per week. This is where partitioning
-- genuinely pays: campaign-window and weekly queries prune ~99% of 36.8M rows,
-- and the partition key is exactly what the analysis filters on.
--
-- Source covers weeks 9-101 of 1-102, and 115 of 582 stores -- but those stores
-- carry 98.6% of transactions, so 95.8% of transactions still join.
-- ---------------------------------------------------------------------------
CREATE TABLE fact_causal (
    week_no    SMALLINT NOT NULL,
    product_id INTEGER  NOT NULL,
    store_id   INTEGER  NOT NULL,

    -- TEXT, not integer. These are categorical codes mixing digits and letters
    -- ('0'-'9', 'A'-'Z'). Integer inference silently corrupted them during
    -- profiling: pandas typed some chunks int and others str, so the distinct
    -- value set came back with both 0 and '0' as separate members.
    display    TEXT,
    mailer     TEXT,

    PRIMARY KEY (week_no, product_id, store_id)
) PARTITION BY RANGE (week_no);

COMMENT ON COLUMN fact_causal.display IS
    'Categorical code, not a number. In-store display location.';
COMMENT ON COLUMN fact_causal.mailer IS
    'Categorical code, not a number. Mailer/circular placement.';

-- ---------------------------------------------------------------------------
-- fact_coupon_redemption -- 2,318 rows
--
-- Small, but the only direct evidence of a household ACTING on an offer rather
-- than merely receiving one. Not partitioned -- 2,318 rows.
-- ---------------------------------------------------------------------------
CREATE TABLE fact_coupon_redemption (
    household_key   INTEGER  NOT NULL,
    day_number      SMALLINT NOT NULL,
    redemption_date DATE     NOT NULL,
    coupon_upc      BIGINT   NOT NULL,
    campaign_id     INTEGER  NOT NULL,
    PRIMARY KEY (household_key, day_number, coupon_upc, campaign_id)
);

COMMIT;

-- ---------------------------------------------------------------------------
-- Partition creation
--
-- Generated rather than hand-written: 102 weekly + ~24 monthly partitions is
-- too many to maintain by hand, and generation keeps the boundaries provably
-- contiguous.
-- ---------------------------------------------------------------------------

-- fact_causal: one partition per week, 1..102.
DO $$
DECLARE w SMALLINT;
BEGIN
    FOR w IN 1..102 LOOP
        EXECUTE format(
            'CREATE TABLE IF NOT EXISTS fact_causal_w%s '
            'PARTITION OF fact_causal FOR VALUES FROM (%s) TO (%s)',
            lpad(w::text, 3, '0'), w, w + 1
        );
    END LOOP;
END $$;

-- fact_transactions: monthly, spanning the full 711-day panel. Bounds are
-- derived from dim_date so they cannot drift from the anchor.
DO $$
DECLARE
    lo DATE;
    hi DATE;
    m  DATE;
BEGIN
    SELECT min(month_start), max(month_start) INTO lo, hi FROM dim_date;
    IF lo IS NULL THEN
        RAISE EXCEPTION 'dim_date is empty -- load it before creating partitions';
    END IF;

    m := lo;
    WHILE m <= hi LOOP
        EXECUTE format(
            'CREATE TABLE IF NOT EXISTS fact_transactions_%s '
            'PARTITION OF fact_transactions FOR VALUES FROM (%L) TO (%L)',
            to_char(m, 'YYYYMM'), m, m + INTERVAL '1 month'
        );
        m := m + INTERVAL '1 month';
    END LOOP;
END $$;
