"""The individual load steps, in dependency order.

Ordering note: dim_household and dim_store cannot be loaded from their own
files. A household is defined by appearing in transactions -- only 801 of the
2,500 have demographics -- and dim_store.has_promo_coverage depends on which
stores appear in causal_data. Both large files are therefore staged before the
dimensions that derive from them.
"""

from __future__ import annotations

from pathlib import Path

import psycopg

STAGING_DDL = """
CREATE UNLOGGED TABLE IF NOT EXISTS stg_transactions (
    household_key int, basket_id bigint, day smallint, product_id int,
    quantity int, sales_value numeric(10,2), store_id int,
    retail_disc numeric(10,2), trans_time smallint, week_no smallint,
    coupon_disc numeric(10,2), coupon_match_disc numeric(10,2));

CREATE UNLOGGED TABLE IF NOT EXISTS stg_causal (
    product_id int, store_id int, week_no smallint, display text, mailer text);

CREATE UNLOGGED TABLE IF NOT EXISTS stg_product (
    product_id int, manufacturer int, department text, brand text,
    commodity_desc text, sub_commodity_desc text, curr_size_of_product text);

CREATE UNLOGGED TABLE IF NOT EXISTS stg_demo (
    age_desc text, marital_status_code text, income_desc text,
    homeowner_desc text, hh_comp_desc text, household_size_desc text,
    kid_category_desc text, household_key int);

CREATE UNLOGGED TABLE IF NOT EXISTS stg_campaign_desc (
    description text, campaign int, start_day smallint, end_day smallint);

CREATE UNLOGGED TABLE IF NOT EXISTS stg_campaign_table (
    description text, household_key int, campaign int);

CREATE UNLOGGED TABLE IF NOT EXISTS stg_coupon (
    coupon_upc bigint, product_id int, campaign int);

CREATE UNLOGGED TABLE IF NOT EXISTS stg_coupon_redempt (
    household_key int, day smallint, coupon_upc bigint, campaign int);
"""

# (logical file name, staging table, column list in FILE order)
COPY_SPECS: dict[str, tuple[str, list[str]]] = {
    "transactions": ("stg_transactions", [
        "household_key", "basket_id", "day", "product_id", "quantity",
        "sales_value", "store_id", "retail_disc", "trans_time", "week_no",
        "coupon_disc", "coupon_match_disc"]),
    "causal": ("stg_causal", ["product_id", "store_id", "week_no", "display", "mailer"]),
    "products": ("stg_product", [
        "product_id", "manufacturer", "department", "brand", "commodity_desc",
        "sub_commodity_desc", "curr_size_of_product"]),
    "households": ("stg_demo", [
        "age_desc", "marital_status_code", "income_desc", "homeowner_desc",
        "hh_comp_desc", "household_size_desc", "kid_category_desc", "household_key"]),
    "campaign_desc": ("stg_campaign_desc", [
        "description", "campaign", "start_day", "end_day"]),
    "campaign_members": ("stg_campaign_table", ["description", "household_key", "campaign"]),
    "coupons": ("stg_coupon", ["coupon_upc", "product_id", "campaign"]),
    "coupon_redemptions": ("stg_coupon_redempt", [
        "household_key", "day", "coupon_upc", "campaign"]),
}


# ---------------------------------------------------------------------------
# assertions -- run against staged data, before anything reaches a fact table
# ---------------------------------------------------------------------------

def assert_week_rule(conn: psycopg.Connection) -> None:
    """Recompute week_no from day and fail on any disagreement.

    This is the guard for the whole time model. If the rule drifts, every join
    between transactions and causal_data silently shifts and nothing errors --
    see docs/methodology-notes.md.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM stg_transactions WHERE week_no <> (day + 8) / 7")
        bad = int(cur.fetchone()[0])
    if bad:
        raise RuntimeError(
            f"week_no assertion FAILED: {bad:,} staged transactions disagree with "
            "(day + 8) / 7. The dim_date anchor and the causal_data join key are "
            "inconsistent -- do not proceed."
        )


def assert_causal_weeks_known(conn: psycopg.Connection) -> tuple[int, int, int]:
    """fact_causal has no day column, so it gets domain enforcement, not
    recomputation. Verify every staged week exists in dim_week."""
    with conn.cursor() as cur:
        cur.execute("""SELECT count(*) FROM (
                         SELECT DISTINCT week_no FROM stg_causal
                         EXCEPT SELECT week_no FROM dim_week) x""")
        unknown = int(cur.fetchone()[0])
        cur.execute("SELECT min(week_no), max(week_no) FROM stg_causal")
        lo, hi = cur.fetchone()
    if unknown:
        raise RuntimeError(f"{unknown} week_no values in causal_data are absent from dim_week")
    return unknown, int(lo), int(hi)


def check_causal_duplicates(conn: psycopg.Connection) -> int:
    """fact_causal's PK is (week_no, product_id, store_id). Unverified in Phase 0
    -- measured here before the PK can reject the insert."""
    with conn.cursor() as cur:
        cur.execute("""SELECT coalesce(sum(c - 1), 0) FROM (
                         SELECT count(*) AS c FROM stg_causal
                         GROUP BY week_no, product_id, store_id HAVING count(*) > 1) x""")
        return int(cur.fetchone()[0])


def anomaly_counts(conn: psycopg.Connection) -> dict[str, int]:
    """Known anomalies, recorded from the real load for the Phase 4 suite."""
    out: dict[str, int] = {}
    with conn.cursor() as cur:
        for name, sql in {
            "retail_disc_positive": "SELECT count(*) FROM stg_transactions WHERE retail_disc > 0",
            "gross_negative":
                "SELECT count(*) FROM stg_transactions WHERE sales_value - retail_disc < 0",
            "quantity_le_zero": "SELECT count(*) FROM stg_transactions WHERE quantity <= 0",
            "weighted_items": "SELECT count(*) FROM stg_transactions WHERE quantity > 1000",
            "sales_value_zero": "SELECT count(*) FROM stg_transactions WHERE sales_value = 0",
            "sales_value_negative": "SELECT count(*) FROM stg_transactions WHERE sales_value < 0",
        }.items():
            cur.execute(sql)  # type: ignore[arg-type]
            out[name] = int(cur.fetchone()[0])
    return out


# ---------------------------------------------------------------------------
# dimension population from staged data
# ---------------------------------------------------------------------------

DIM_SQL: dict[str, str] = {
    "dim_product": """
        INSERT INTO dim_product (product_id, manufacturer_id, department, brand,
            commodity_desc, sub_commodity_desc, curr_size_of_product)
        SELECT DISTINCT ON (product_id) product_id, manufacturer,
               nullif(trim(department),''), nullif(trim(brand),''),
               nullif(trim(commodity_desc),''), nullif(trim(sub_commodity_desc),''),
               nullif(trim(curr_size_of_product),'')
        FROM stg_product ORDER BY product_id
        ON CONFLICT (product_id) DO NOTHING""",

    # Households come from transactions; demographics are a left join because
    # only 801 of 2,500 have them.
    "dim_household": """
        INSERT INTO dim_household (household_key, age_desc, marital_status_code,
            income_desc, homeowner_desc, hh_comp_desc, household_size_desc,
            kid_category_desc, has_demographics)
        SELECT h.household_key, d.age_desc, d.marital_status_code, d.income_desc,
               d.homeowner_desc, d.hh_comp_desc, d.household_size_desc,
               d.kid_category_desc, (d.household_key IS NOT NULL)
        FROM (SELECT DISTINCT household_key FROM stg_transactions) h
        LEFT JOIN stg_demo d USING (household_key)
        ON CONFLICT (household_key) DO NOTHING""",

    # has_promo_coverage: 115 of 582 stores appear in causal_data, but they
    # carry 98.6% of transactions.
    "dim_store": """
        INSERT INTO dim_store (store_id, has_promo_coverage)
        SELECT s.store_id, s.store_id IN (SELECT DISTINCT store_id FROM stg_causal)
        FROM (SELECT DISTINCT store_id FROM stg_transactions) s
        ON CONFLICT (store_id) DO NOTHING""",

    "dim_campaign": """
        INSERT INTO dim_campaign (campaign_id, campaign_type, start_day, end_day,
                                  start_date, end_date)
        SELECT c.campaign, trim(c.description), c.start_day, c.end_day,
               d1.date_key, d2.date_key
        FROM stg_campaign_desc c
        JOIN dim_date d1 ON d1.day_number = c.start_day
        JOIN dim_date d2 ON d2.day_number = least(c.end_day, 711)
        ON CONFLICT (campaign_id) DO NOTHING""",

    # 124,548 raw rows collapse to 1,135 coupons; 5,164 rows are exact dupes.
    "dim_coupon": """
        INSERT INTO dim_coupon (coupon_upc)
        SELECT DISTINCT coupon_upc FROM stg_coupon
        ON CONFLICT (coupon_upc) DO NOTHING""",

    "bridge_coupon_product": """
        INSERT INTO bridge_coupon_product (coupon_upc, product_id)
        SELECT DISTINCT c.coupon_upc, c.product_id FROM stg_coupon c
        JOIN dim_product p ON p.product_id = c.product_id
        ON CONFLICT DO NOTHING""",

    # Many-to-many: 171 coupons appear in up to 6 campaigns.
    "bridge_coupon_campaign": """
        INSERT INTO bridge_coupon_campaign (coupon_upc, campaign_id)
        SELECT DISTINCT c.coupon_upc, c.campaign FROM stg_coupon c
        JOIN dim_campaign ca ON ca.campaign_id = c.campaign
        ON CONFLICT DO NOTHING""",

    "bridge_campaign_household": """
        INSERT INTO bridge_campaign_household (campaign_id, household_key)
        SELECT DISTINCT t.campaign, t.household_key
        FROM stg_campaign_table t
        JOIN dim_campaign c ON c.campaign_id = t.campaign
        JOIN dim_household h ON h.household_key = t.household_key
        ON CONFLICT DO NOTHING""",

    "fact_coupon_redemption": """
        INSERT INTO fact_coupon_redemption (household_key, day_number,
            redemption_date, coupon_upc, campaign_id)
        SELECT DISTINCT r.household_key, r.day, d.date_key, r.coupon_upc, r.campaign
        FROM stg_coupon_redempt r
        JOIN dim_date d ON d.day_number = r.day
        ON CONFLICT DO NOTHING""",
}


def fact_transactions_month_sql(month_start: object) -> str:
    return f"""
        INSERT INTO fact_transactions (
            date_key, basket_id, product_id, household_key, store_id,
            day_number, week_no, trans_time, quantity, sales_value,
            retail_disc, coupon_disc, coupon_match_disc)
        SELECT d.date_key, s.basket_id, s.product_id, s.household_key, s.store_id,
               s.day, s.week_no, s.trans_time, s.quantity, s.sales_value,
               s.retail_disc, s.coupon_disc, s.coupon_match_disc
        FROM stg_transactions s
        JOIN dim_date d ON d.day_number = s.day
        WHERE d.month_start = DATE '{month_start}'
        ON CONFLICT (date_key, basket_id, product_id) DO NOTHING"""


def fact_causal_week_sql(week: object) -> str:
    """Insert one week of promotional exposure.

    There is deliberately NO `ON CONFLICT` clause here, and its absence is not
    an oversight. Uniqueness on (week_no, product_id, store_id) is already
    guaranteed by two things together:

      * DISTINCT ON collapses duplicates within the batch -- and there are real
        ones: 15,245 rows across the file, so this is load-bearing, not
        defensive.
      * each batch selects exactly one week_no, and week_no is the leading
        column of the key, so two batches can never collide with each other.

    An ON CONFLICT clause would therefore add 36.8M index probes to guarantee
    something already guaranteed. Measured cost of having it: see
    docs/performance.md. Do not add it back without removing one of the two
    guarantees above.
    """
    return f"""
        INSERT INTO fact_causal (week_no, product_id, store_id, display, mailer)
        SELECT DISTINCT ON (week_no, product_id, store_id)
               week_no, product_id, store_id,
               nullif(trim(display),''), nullif(trim(mailer),'')
        FROM stg_causal WHERE week_no = {int(week)}
        ORDER BY week_no, product_id, store_id"""


def drop_staging(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        for t in ("stg_transactions", "stg_causal", "stg_product", "stg_demo",
                  "stg_campaign_desc", "stg_campaign_table", "stg_coupon",
                  "stg_coupon_redempt"):
            cur.execute(f"DROP TABLE IF EXISTS {t}")


def file_for(files: dict[str, Path], logical: str) -> Path:
    if logical not in files:
        raise FileNotFoundError(f"required source file missing: {logical}")
    return files[logical]
