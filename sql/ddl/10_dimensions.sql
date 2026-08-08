-- Dimensions.
--
-- Every non-obvious choice here traces to a measurement in
-- docs/dataset-profile.md, produced by `rrip profile` over the raw files.
-- Where a Phase 0 finding could otherwise be lost, it is encoded as a column or
-- a constraint rather than left in prose.

BEGIN;

-- ---------------------------------------------------------------------------
-- dim_week -- 102 rows
--
-- Exists as its own dimension because fact_causal is keyed on week_no and has
-- no day column. Without it, fact_causal would have no dimension to reference
-- and its 36.8M week values would be unconstrained. dim_date cannot serve:
-- week_no is not unique there (711 days over 102 weeks).
-- ---------------------------------------------------------------------------
CREATE TABLE dim_week (
    week_no         SMALLINT    PRIMARY KEY,
    start_day       SMALLINT    NOT NULL,
    end_day         SMALLINT    NOT NULL,
    start_date      DATE        NOT NULL,
    end_date        DATE        NOT NULL,
    day_count       SMALLINT    NOT NULL,
    -- Week 1 spans days 1-5, not 7. The panel does not start on a week
    -- boundary. Flagged so a five-day week reads as documented fact rather
    -- than as a data error someone "fixes".
    is_partial_week BOOLEAN     NOT NULL,

    CONSTRAINT dim_week_range_valid CHECK (end_day >= start_day),
    CONSTRAINT dim_week_day_count   CHECK (day_count = end_day - start_day + 1),
    CONSTRAINT dim_week_partial_iff CHECK (is_partial_week = (day_count <> 7))
);

COMMENT ON TABLE dim_week IS
    'Week grain. week_no = (day + 8) / 7 integer division -- verified exact on '
    'all 2,595,732 source transactions. Week 1 is a partial 5-day week.';

-- ---------------------------------------------------------------------------
-- dim_date -- 711 rows
--
-- ANCHOR: day 6 -> Monday, which makes day 1 a Wednesday.
--
-- This is not arbitrary. dunnhumby publishes no calendar start, but it does
-- ship WEEK_NO, and causal_data (36.8M rows) is keyed on it. Anchoring day 1
-- to a Monday -- the intuitive choice -- shifts every derived week two days out
-- of alignment with WEEK_NO, silently corrupting every transaction-to-promotion
-- join. See docs/methodology-notes.md.
--
-- The absolute calendar year is arbitrary and carries no meaning. Elapsed
-- intervals, month boundaries and YoY comparisons are real; weekday LABELS are
-- a modelling convention.
-- ---------------------------------------------------------------------------
CREATE TABLE dim_date (
    date_key        DATE        PRIMARY KEY,
    day_number      SMALLINT    NOT NULL UNIQUE,     -- source DAY, 1..711
    week_no         SMALLINT    NOT NULL REFERENCES dim_week (week_no),
    day_of_week     SMALLINT    NOT NULL,            -- 1=Mon .. 7=Sun (ISO)
    day_name        TEXT        NOT NULL,
    is_weekend      BOOLEAN     NOT NULL,
    month_number    SMALLINT    NOT NULL,
    month_name      TEXT        NOT NULL,
    month_start     DATE        NOT NULL,
    quarter_number  SMALLINT    NOT NULL,
    year_number     SMALLINT    NOT NULL,
    -- Denormalised from dim_week: Power BI connects directly to these views and
    -- a flattened date dimension avoids forcing a join for a common filter.
    is_partial_week BOOLEAN     NOT NULL,

    CONSTRAINT dim_date_day_range CHECK (day_number BETWEEN 1 AND 711),
    -- The rule, enforced in the schema rather than trusted to the loader.
    CONSTRAINT dim_date_week_rule CHECK (week_no = (day_number + 8) / 7)
);

COMMENT ON COLUMN dim_date.day_of_week IS
    'Derived from the day-6-is-Monday anchor. Weekday labels are a modelling '
    'convention, not source data -- do not make day-of-week claims from them.';

-- ---------------------------------------------------------------------------
-- dim_household -- 2,500 rows
-- ---------------------------------------------------------------------------
CREATE TABLE dim_household (
    household_key       INTEGER PRIMARY KEY,
    age_desc            TEXT,
    marital_status_code TEXT,
    income_desc         TEXT,
    homeowner_desc      TEXT,
    hh_comp_desc        TEXT,
    household_size_desc TEXT,
    kid_category_desc   TEXT,
    -- 801 of 2,500 households (32%) carry demographics, but they generate 55%
    -- of all transactions. This is a BEHAVIOURAL SELECTION EFFECT, not a
    -- coverage gap: demographic households shop substantially more than the
    -- panel average. Phase 6c must weight or condition on this -- treating the
    -- 801 as a random subsample biases any demographic confounder.
    has_demographics    BOOLEAN NOT NULL
);

COMMENT ON COLUMN dim_household.has_demographics IS
    'Selection effect, not coverage: 32% of households, 55% of transactions.';

-- ---------------------------------------------------------------------------
-- dim_store -- 582 rows
-- ---------------------------------------------------------------------------
CREATE TABLE dim_store (
    store_id           INTEGER PRIMARY KEY,
    -- fact_causal covers only 115 of 582 stores -- but those 115 carry 98.6%
    -- of transactions, and 95.8% of all transactions join to promo data on
    -- (store, week). Exposed as a column so that asymmetry is visible in the
    -- model instead of living only in a document: any promo-conditioned
    -- analysis must filter on it or explain why not.
    has_promo_coverage BOOLEAN NOT NULL
);

-- ---------------------------------------------------------------------------
-- dim_product -- 92,353 rows
-- ---------------------------------------------------------------------------
CREATE TABLE dim_product (
    product_id           INTEGER PRIMARY KEY,
    manufacturer_id      INTEGER,
    department           TEXT,
    brand                TEXT,
    commodity_desc       TEXT,
    sub_commodity_desc   TEXT,
    curr_size_of_product TEXT
);

-- ---------------------------------------------------------------------------
-- dim_campaign -- 30 rows
-- ---------------------------------------------------------------------------
CREATE TABLE dim_campaign (
    campaign_id   INTEGER  PRIMARY KEY,
    campaign_type TEXT     NOT NULL,          -- TypeA / TypeB / TypeC
    start_day     SMALLINT NOT NULL,
    end_day       SMALLINT NOT NULL,
    start_date    DATE     NOT NULL,
    end_date      DATE     NOT NULL,

    CONSTRAINT dim_campaign_range CHECK (end_day >= start_day)
);

-- ---------------------------------------------------------------------------
-- dim_coupon -- 1,135 rows
--
-- coupon.csv holds 124,548 rows but only 1,135 distinct coupons: it is a
-- denormalised cross product of coupon x product x campaign, with 5,164 exactly
-- duplicated rows. Splitting it into a dimension plus two bridges is what makes
-- the grain honest.
--
-- This matters for Phase 6c beyond tidiness. "Was this household offered a
-- coupon on a product it already buys?" is a finer and differently-selected
-- treatment than blanket campaign enrolment, and coupon targeting is plausibly
-- correlated with prior purchasing -- i.e. selection on past outcomes, exactly
-- the confounder that breaks difference-in-differences.
-- ---------------------------------------------------------------------------
CREATE TABLE dim_coupon (
    coupon_upc BIGINT PRIMARY KEY
);

-- A coupon covers many products: median 12, max 14,477.
CREATE TABLE bridge_coupon_product (
    coupon_upc BIGINT  NOT NULL REFERENCES dim_coupon (coupon_upc),
    product_id INTEGER NOT NULL REFERENCES dim_product (product_id),
    PRIMARY KEY (coupon_upc, product_id)
);

-- Many-to-many: 171 coupons appear in more than one campaign (max 6).
CREATE TABLE bridge_coupon_campaign (
    coupon_upc  BIGINT  NOT NULL REFERENCES dim_coupon (coupon_upc),
    campaign_id INTEGER NOT NULL REFERENCES dim_campaign (campaign_id),
    PRIMARY KEY (coupon_upc, campaign_id)
);

-- ---------------------------------------------------------------------------
-- bridge_campaign_household -- 7,208 rows
--
-- The treatment assignment table. Phase 6c reads its treated/control split from
-- here, so its integrity is load-bearing for the causal module.
-- ---------------------------------------------------------------------------
CREATE TABLE bridge_campaign_household (
    campaign_id   INTEGER NOT NULL REFERENCES dim_campaign (campaign_id),
    household_key INTEGER NOT NULL REFERENCES dim_household (household_key),
    PRIMARY KEY (campaign_id, household_key)
);

COMMIT;
