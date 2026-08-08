"""Data quality assertions.

Each assertion is a SQL query returning a single numeric `observed` value,
compared against a threshold. Declarative rather than a pile of scripts, so the
suite can be listed, filtered and reported on.

THRESHOLDS: where a threshold encodes a known property of this dataset, it is
taken from `etl_data_quality` -- measured at load against NUMERIC(10,2) -- and
never from the Phase 0 profile, which computed the money columns in float32 and
got 36 positive retail_disc rows and 17 negative-gross rows against the exact
values of 10 and 1. Hardcoding the float32 figures here would bake a
floating-point artefact into the suite as a permanent expectation.

Where a threshold is a genuine tolerance rather than a measured constant, it is
stated as such with the reasoning.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Check:
    name: str
    category: str
    sql: str
    # 'zero'      -> observed must be 0
    # 'max'       -> observed must be <= threshold
    # 'min'       -> observed must be >= threshold
    # 'recorded'  -> observed must equal the value recorded in etl_data_quality
    rule: str
    threshold: float | None = None
    recorded_key: str | None = None
    severity: str = "error"          # 'error' fails the run; 'warn' does not
    rationale: str = ""


CHECKS: list[Check] = [

    # --- referential integrity ------------------------------------------
    Check("fk_transactions_product", "referential",
          """SELECT count(*) FROM fact_transactions f
             LEFT JOIN dim_product d ON d.product_id = f.product_id
             WHERE d.product_id IS NULL""",
          "zero", rationale="Phase 0 measured 0 orphan products; any drift is a load defect."),

    Check("fk_transactions_household", "referential",
          """SELECT count(*) FROM fact_transactions f
             LEFT JOIN dim_household d ON d.household_key = f.household_key
             WHERE d.household_key IS NULL""",
          "zero"),

    Check("fk_causal_week", "referential",
          """SELECT count(*) FROM fact_causal f
             LEFT JOIN dim_week d ON d.week_no = f.week_no
             WHERE d.week_no IS NULL""",
          "zero", rationale="fact_causal has no day column, so this FK is the "
                            "only guard keeping its 36.8M week values inside "
                            "the time model dim_date defines."),

    Check("fk_causal_store", "referential",
          """SELECT count(*) FROM fact_causal f
             LEFT JOIN dim_store d ON d.store_id = f.store_id
             WHERE d.store_id IS NULL""",
          "zero"),

    # --- the time model -------------------------------------------------
    Check("week_rule_dim_date", "time_model",
          "SELECT count(*) FROM dim_date WHERE week_no <> (day_number + 8) / 7",
          "zero", rationale="week_no = (day+8)/7 is the rule the causal_data "
                            "join depends on. Drift here silently shifts every "
                            "promotional analysis by days."),

    Check("week_rule_fact_transactions", "time_model",
          """SELECT count(*) FROM fact_transactions f
             JOIN dim_date d ON d.date_key = f.date_key
             WHERE f.week_no <> d.week_no""",
          "zero"),

    Check("day_six_is_monday", "time_model",
          "SELECT count(*) FROM dim_date WHERE day_number = 6 AND day_of_week <> 1",
          "zero", rationale="The calendar anchor. If this moves, every derived "
                            "week misaligns with the source WEEK_NO."),

    Check("dim_date_complete", "time_model",
          "SELECT 711 - count(*) FROM dim_date", "zero"),

    Check("dim_week_complete", "time_model",
          "SELECT 102 - count(*) FROM dim_week", "zero"),

    # --- duplicates / grain ---------------------------------------------
    Check("duplicate_transaction_grain", "duplicates",
          """SELECT coalesce(sum(c - 1), 0) FROM (
                 SELECT count(*) AS c FROM fact_transactions
                 GROUP BY date_key, basket_id, product_id HAVING count(*) > 1) x""",
          "zero", rationale="(basket_id, product_id) is the natural key -- "
                            "Phase 0 verified 0 duplicates across 2,595,732 rows."),

    Check("duplicate_causal_grain", "duplicates",
          """SELECT coalesce(sum(c - 1), 0) FROM (
                 SELECT count(*) AS c FROM fact_causal
                 GROUP BY week_no, product_id, store_id HAVING count(*) > 1) x""",
          "zero", rationale="Deduplicated on load; the PK enforces it afterwards."),

    # --- nulls ----------------------------------------------------------
    Check("null_transaction_keys", "nulls",
          """SELECT count(*) FROM fact_transactions
             WHERE date_key IS NULL OR basket_id IS NULL OR product_id IS NULL
                OR household_key IS NULL OR store_id IS NULL""",
          "zero"),

    Check("null_money_columns", "nulls",
          """SELECT count(*) FROM fact_transactions
             WHERE sales_value IS NULL OR retail_disc IS NULL""",
          "zero"),

    Check("product_department_null_rate", "nulls",
          """SELECT round(100.0 * count(*) FILTER (WHERE department IS NULL)
                          / nullif(count(*), 0), 2) FROM dim_product""",
          "max", threshold=1.0, severity="warn",
          rationale="Tolerance, not a measured constant: a small share of "
                    "products legitimately lack a department. Set at 1% so a "
                    "structural change in the source is visible."),

    # --- row counts / drift ---------------------------------------------
    Check("row_count_transactions", "drift",
          "SELECT abs(2595732 - count(*)) FROM fact_transactions", "zero",
          rationale="Reconciled against the source file at load."),

    Check("row_count_causal", "drift",
          "SELECT abs(36771279 - count(*)) FROM fact_causal", "zero",
          rationale="36,786,524 source rows less 15,245 recorded duplicates."),

    Check("row_count_households", "drift",
          "SELECT abs(2500 - count(*)) FROM dim_household", "zero"),

    # --- measured anomalies, from etl_data_quality (NUMERIC, not float32)
    Check("anomaly_quantity_le_zero", "anomalies",
          "SELECT count(*) FROM fact_transactions WHERE quantity <= 0",
          "recorded", recorded_key="quantity_le_zero",
          rationale="Returns. Recorded value is 14,466."),

    Check("anomaly_weighted_items", "anomalies",
          "SELECT count(*) FROM fact_transactions WHERE quantity > 1000",
          "recorded", recorded_key="weighted_items",
          rationale="Weighted goods in grams. Recorded value is 23,101."),

    Check("anomaly_sales_value_zero", "anomalies",
          "SELECT count(*) FROM fact_transactions WHERE sales_value = 0",
          "recorded", recorded_key="sales_value_zero",
          rationale="Free goods / full coupon coverage. NUMERIC value is "
                    "18,879; the float32 profile said 18,850."),

    Check("anomaly_negative_sales", "anomalies",
          "SELECT count(*) FROM fact_transactions WHERE sales_value < 0",
          "zero", rationale="Returns appear as quantity <= 0, never as negative "
                            "money. Zero such rows in the source."),

    # --- generated columns ----------------------------------------------
    Check("gross_value_consistent", "derived",
          """SELECT count(*) FROM fact_transactions
             WHERE gross_value <> sales_value - retail_disc""",
          "zero"),

    Check("is_return_consistent", "derived",
          "SELECT count(*) FROM fact_transactions WHERE is_return <> (quantity <= 0)",
          "zero"),

    Check("is_weighted_consistent", "derived",
          "SELECT count(*) FROM fact_transactions WHERE is_weighted_item <> (quantity > 1000)",
          "zero"),

    # --- domain ----------------------------------------------------------
    Check("causal_display_is_text_code", "domain",
          "SELECT count(*) FROM fact_causal WHERE display !~ '^[0-9A-Z]$' AND display IS NOT NULL",
          "zero", rationale="display/mailer are single-character categorical "
                            "codes. Integer inference corrupted them during "
                            "profiling; this catches a regression to that."),

    Check("store_promo_coverage_flag", "domain",
          """SELECT abs(115 - count(*)) FROM dim_store WHERE has_promo_coverage""",
          "zero", rationale="115 of 582 stores appear in fact_causal."),

    Check("demographics_coverage", "domain",
          """SELECT count(*) FROM dim_household WHERE has_demographics""",
          "min", threshold=801,
          rationale="801 households carry demographics -- a behavioural "
                    "selection effect (32% of households, 55% of transactions), "
                    "not a coverage gap to be fixed."),

    # --- freshness --------------------------------------------------------
    Check("fact_covers_full_panel", "freshness",
          """SELECT (SELECT max(day_number) FROM dim_date)
                  - (SELECT max(day_number) FROM fact_transactions)""",
          "max", threshold=0,
          rationale="The fact table must reach the end of the modelled calendar."),

    Check("load_control_all_done", "freshness",
          "SELECT count(*) FROM etl_load_control WHERE status <> 'done'",
          "zero"),
]
