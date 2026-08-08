"""Integration tests against the loaded database.

These require a completed load and are skipped when the database is not
reachable, so the unit suite still runs in CI without Postgres.

They check the properties a load can violate silently: rows landing in the
wrong partition, generated columns encoding the wrong convention, referential
drift, and reconciliation against the raw files.
"""

from __future__ import annotations

import pytest

from rrip.config import settings
from rrip.profile.discovery import count_rows, discover

psycopg = pytest.importorskip("psycopg")


def _conn():
    from rrip.db.connection import connect
    return connect()


def _reachable() -> bool:
    try:
        with _conn() as c, c.cursor() as cur:
            cur.execute("SELECT to_regclass('fact_causal')")
            return cur.fetchone()[0] is not None
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _reachable(), reason="loaded database not available"
)


def scalar(sql: str):
    with _conn() as c, c.cursor() as cur:
        cur.execute(sql)
        return cur.fetchone()[0]


# --- reconciliation --------------------------------------------------------

def test_fact_transactions_matches_source_exactly() -> None:
    files = discover(settings.raw_dir)
    assert scalar("SELECT count(*) FROM fact_transactions") == count_rows(files["transactions"])


def test_fact_causal_accounts_for_every_source_row() -> None:
    """Loaded rows + deduplicated rows must equal the source line count.

    fact_causal is deliberately deduplicated on its natural key, so an exact
    match would be wrong. What must hold is that nothing vanished unexplained.
    """
    files = discover(settings.raw_dir)
    source = count_rows(files["causal"])
    loaded = scalar("SELECT count(*) FROM fact_causal")
    duplicates_removed = source - loaded
    assert duplicates_removed >= 0
    # Recompute the duplicate count from staging if it still exists.
    staged = scalar("SELECT to_regclass('stg_causal')")
    if staged:
        expected = scalar("""
            SELECT coalesce(sum(c - 1), 0) FROM (
                SELECT count(*) AS c FROM stg_causal
                GROUP BY week_no, product_id, store_id HAVING count(*) > 1) x""")
        assert duplicates_removed == expected


def test_dimension_counts() -> None:
    assert scalar("SELECT count(*) FROM dim_week") == 102
    assert scalar("SELECT count(*) FROM dim_date") == 711
    assert scalar("SELECT count(*) FROM dim_household") == 2500


# --- the time model --------------------------------------------------------

def test_week_rule_holds_in_dim_date() -> None:
    assert scalar("SELECT count(*) FROM dim_date WHERE week_no <> (day_number + 8) / 7") == 0


def test_week_one_is_partial_and_five_days() -> None:
    with _conn() as c, c.cursor() as cur:
        cur.execute("SELECT day_count, is_partial_week FROM dim_week WHERE week_no = 1")
        days, partial = cur.fetchone()
    assert days == 5
    assert partial is True


def test_day_six_is_a_monday() -> None:
    """The anchor the whole calendar depends on."""
    assert scalar("SELECT day_of_week FROM dim_date WHERE day_number = 6") == 1
    assert scalar("SELECT day_of_week FROM dim_date WHERE day_number = 1") == 3  # Wednesday


def test_fact_transactions_week_agrees_with_dim_date() -> None:
    assert scalar("""
        SELECT count(*) FROM fact_transactions f
        JOIN dim_date d ON d.date_key = f.date_key
        WHERE f.week_no <> d.week_no""") == 0


# --- partition routing -----------------------------------------------------

def test_causal_rows_land_in_the_right_partition() -> None:
    """Every row's week_no must match the partition it physically sits in."""
    bad = scalar("""
        SELECT count(*) FROM fact_causal f
        WHERE f.week_no <> substring(f.tableoid::regclass::text from 'w0*([0-9]+)$')::int""")
    assert bad == 0


def test_transaction_rows_land_in_the_right_partition() -> None:
    bad = scalar("""
        SELECT count(*) FROM fact_transactions f
        WHERE to_char(f.date_key, 'YYYYMM')
              <> substring(f.tableoid::regclass::text from '([0-9]{6})$')""")
    assert bad == 0


def test_every_causal_partition_is_attached() -> None:
    assert scalar("""
        SELECT count(*) FROM pg_inherits i
        JOIN pg_class p ON p.oid = i.inhparent WHERE p.relname = 'fact_causal'""") == 102


# --- referential integrity -------------------------------------------------

@pytest.mark.parametrize("child,col,parent,pcol", [
    ("fact_transactions", "product_id", "dim_product", "product_id"),
    ("fact_transactions", "household_key", "dim_household", "household_key"),
    ("fact_transactions", "store_id", "dim_store", "store_id"),
    ("fact_causal", "week_no", "dim_week", "week_no"),
    ("fact_causal", "product_id", "dim_product", "product_id"),
    ("fact_causal", "store_id", "dim_store", "store_id"),
])
def test_no_orphans(child: str, col: str, parent: str, pcol: str) -> None:
    assert scalar(
        f"SELECT count(*) FROM {child} c "
        f"LEFT JOIN {parent} p ON p.{pcol} = c.{col} WHERE p.{pcol} IS NULL") == 0


# --- encoded Phase 0 findings ---------------------------------------------

def test_gross_value_convention() -> None:
    """gross = sales_value - retail_disc, with retail_disc stored negative.

    The anomaly counts here are the NUMERIC(10,2) values measured at load, not
    the Phase 0 figures. Phase 0 profiled money as float32, whose ~7 significant
    digits put values near zero on the wrong side of these comparisons: it
    reported 36 positive retail_disc rows and 17 negative-gross rows against the
    exact-decimal 10 and 1. See docs/methodology-notes.md.
    """
    assert scalar("""
        SELECT count(*) FROM fact_transactions
        WHERE gross_value <> sales_value - retail_disc""") == 0
    assert scalar("SELECT count(*) FROM fact_transactions WHERE gross_value < 0") == 1
    assert scalar("SELECT count(*) FROM fact_transactions WHERE retail_disc > 0") == 10


def test_anomaly_counts_match_recorded_quality_values() -> None:
    """The Phase 4 suite reads these; they must reflect the loaded data."""
    for check, sql in {
        "quantity_le_zero": "SELECT count(*) FROM fact_transactions WHERE quantity <= 0",
        "weighted_items": "SELECT count(*) FROM fact_transactions WHERE quantity > 1000",
        "sales_value_zero": "SELECT count(*) FROM fact_transactions WHERE sales_value = 0",
    }.items():
        recorded = scalar(
            f"SELECT value FROM etl_data_quality WHERE check_name = '{check}'")  # noqa: S608
        assert scalar(sql) == recorded, f"{check} drifted from recorded value"


def test_returns_are_quantity_not_negative_money() -> None:
    assert scalar("SELECT count(*) FROM fact_transactions WHERE sales_value < 0") == 0
    assert scalar("SELECT count(*) FROM fact_transactions WHERE is_return") == 14466


def test_weighted_items_flagged() -> None:
    assert scalar("SELECT count(*) FROM fact_transactions WHERE is_weighted_item") == 23101


def test_demographics_selection_effect_is_visible() -> None:
    """32% of households, but 55% of transactions -- the asymmetry must survive."""
    hh = scalar("""SELECT round(100.0 * count(*) FILTER (WHERE has_demographics)
                   / count(*), 1) FROM dim_household""")
    tx = scalar("""SELECT round(100.0 * count(*) FILTER (WHERE h.has_demographics)
                   / count(*), 1) FROM fact_transactions f
                   JOIN dim_household h USING (household_key)""")
    assert float(hh) == pytest.approx(32.0, abs=0.5)
    assert float(tx) == pytest.approx(55.0, abs=1.0)


def test_store_promo_coverage_flag() -> None:
    covered = scalar("SELECT count(*) FROM dim_store WHERE has_promo_coverage")
    assert covered == 115
    share = scalar("""SELECT round(100.0 * count(*) FILTER (WHERE s.has_promo_coverage)
                      / count(*), 1) FROM fact_transactions f
                      JOIN dim_store s USING (store_id)""")
    assert float(share) == pytest.approx(98.6, abs=0.5)


def test_display_and_mailer_are_text_codes() -> None:
    """Letters must survive -- integer inference would have dropped them."""
    letters = scalar("SELECT count(*) FROM fact_causal WHERE display ~ '^[A-Z]$'")
    assert letters > 0


# --- idempotency -----------------------------------------------------------

def test_rerunning_a_dimension_insert_is_a_noop() -> None:
    """Load-twice safety: the dimension inserts must not duplicate rows."""
    from rrip.ingest.steps import DIM_SQL
    before = scalar("SELECT count(*) FROM dim_product")
    with _conn() as c, c.cursor() as cur:
        cur.execute(DIM_SQL["dim_product"])
        inserted = cur.rowcount
        c.rollback()
    assert inserted == 0
    assert scalar("SELECT count(*) FROM dim_product") == before


def test_control_table_marks_all_steps_done() -> None:
    assert scalar("SELECT count(*) FROM etl_load_control WHERE status <> 'done'") == 0
