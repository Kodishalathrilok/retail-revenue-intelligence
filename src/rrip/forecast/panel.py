"""Build the department x week panel the forecaster trains on.

Four queries, one per source of signal, joined into a dense frame:

  * revenue          fact_transactions x dim_product
  * panel activity   fact_transactions, all departments together
  * promotions       fact_causal x dim_product  (36.8M rows -- see the note)
  * campaigns        dim_campaign x bridge_campaign_household

DENSITY IS THE POINT. Revenue is built as a CROSS JOIN of departments with
weeks, LEFT JOINed to observed sales and coalesced to zero, because a
department that sold nothing in a week earned zero -- that is an observation,
not a gap. Building the frame from observed rows only would silently shorten
the sparse series' lag windows and make a department look more regular than it
is, which is exactly the case the robustness suite needs to see clearly.
"""

from __future__ import annotations

import hashlib
import json
import logging

import pandas as pd

from rrip.config import PROJECT_ROOT
from rrip.forecast import contract as C

logger = logging.getLogger(__name__)

# The 36.8M-row scan takes minutes. Cached under the repo's cache directory,
# keyed by the SQL itself so a query edit invalidates it rather than serving a
# frame built by code that no longer exists.
#
# CSV rather than parquet: the panel is under 2,000 rows, and parquet would
# add pyarrow (~40 MB) to the pipeline extra to serialise a frame small enough
# to open in a text editor. Dtypes are restated on read so the round trip is
# exact rather than inferred.
CACHE_DIR = PROJECT_ROOT / ".cache" / "forecast"

CACHE_DTYPES = {
    "department": "string", "week_no": "int64", "revenue": "float64",
    "dept_baskets": "int64", "dept_households": "int64",
    "panel_revenue": "float64", "panel_baskets": "int64",
    "panel_households": "int64", "promo_rows": "int64", "on_display": "int64",
    "in_mailer": "int64", "campaigns_active": "int64",
    "campaign_households": "int64",
}

# Feature history begins at HISTORY_FLOOR_WEEK, so the panel is pulled from
# there. Nothing earlier is fetched at all -- the ramp weeks are not in memory
# to be accidentally used.
PANEL_FIRST_WEEK = C.HISTORY_FLOOR_WEEK
PANEL_LAST_WEEK = C.LAST_TARGET_WEEK


REVENUE_SQL = """
WITH depts AS (
    SELECT DISTINCT p.department
    FROM fact_transactions ft
    JOIN dim_product p ON p.product_id = ft.product_id
    WHERE p.department IS NOT NULL
),
weeks AS (
    SELECT week_no FROM dim_week
    WHERE week_no BETWEEN %(w0)s AND %(w1)s AND NOT is_partial_week
),
obs AS (
    SELECT p.department, ft.week_no,
           sum(ft.sales_value) AS revenue,
           count(DISTINCT ft.basket_id) AS baskets,
           count(DISTINCT ft.household_key) AS households
    FROM fact_transactions ft
    JOIN dim_product p ON p.product_id = ft.product_id
    WHERE ft.week_no BETWEEN %(w0)s AND %(w1)s AND p.department IS NOT NULL
    GROUP BY 1, 2
)
SELECT d.department,
       w.week_no,
       coalesce(o.revenue, 0)::float8   AS revenue,
       coalesce(o.baskets, 0)::int      AS dept_baskets,
       coalesce(o.households, 0)::int   AS dept_households
FROM depts d
CROSS JOIN weeks w
LEFT JOIN obs o ON o.department = d.department AND o.week_no = w.week_no
ORDER BY d.department, w.week_no
"""

PANEL_SQL = """
SELECT ft.week_no,
       sum(ft.sales_value)::float8                AS panel_revenue,
       count(DISTINCT ft.basket_id)::int          AS panel_baskets,
       count(DISTINCT ft.household_key)::int      AS panel_households
FROM fact_transactions ft
JOIN dim_week w ON w.week_no = ft.week_no
WHERE ft.week_no BETWEEN %(w0)s AND %(w1)s AND NOT w.is_partial_week
GROUP BY 1
ORDER BY 1
"""

# fact_causal covers weeks 9..101 and holds 36.8M rows. display and mailer are
# TEXT, not integers -- the codes mix digits and letters and integer inference
# corrupted them during profiling (docs/schema.md). '0' is the no-promotion
# code for both, so "promoted" is <> '0' and never > 0.
PROMO_SQL = """
SELECT fc.week_no,
       p.department,
       count(*)::bigint                                        AS promo_rows,
       count(*) FILTER (WHERE fc.display <> '0')::bigint       AS on_display,
       count(*) FILTER (WHERE fc.mailer  <> '0')::bigint       AS in_mailer
FROM fact_causal fc
JOIN dim_product p ON p.product_id = fc.product_id
WHERE fc.week_no BETWEEN %(w0)s AND %(w1)s AND p.department IS NOT NULL
GROUP BY 1, 2
ORDER BY 1, 2
"""

# A campaign is active in a week if its day range overlaps that week. dim_week
# carries the day bounds, so the overlap is expressed on days and never
# recomputes the (day+8)/7 rule -- docs/schema.md is emphatic that a second
# spelling of that rule is how the time model goes quietly wrong.
CAMPAIGN_SQL = """
WITH enrol AS (
    SELECT campaign_id, count(DISTINCT household_key)::int AS households
    FROM bridge_campaign_household GROUP BY 1
)
SELECT w.week_no,
       count(DISTINCT c.campaign_id)::int                  AS campaigns_active,
       coalesce(sum(e.households), 0)::int                  AS campaign_households
FROM dim_week w
LEFT JOIN dim_campaign c
       ON c.start_day <= w.end_day AND c.end_day >= w.start_day
LEFT JOIN enrol e ON e.campaign_id = c.campaign_id
WHERE w.week_no BETWEEN %(w0)s AND %(w1)s AND NOT w.is_partial_week
GROUP BY 1
ORDER BY 1
"""

_QUERIES = {
    "revenue": REVENUE_SQL,
    "panel": PANEL_SQL,
    "promo": PROMO_SQL,
    "campaign": CAMPAIGN_SQL,
}


def _cache_key() -> str:
    payload = json.dumps(
        {"sql": _QUERIES, "w0": PANEL_FIRST_WEEK, "w1": PANEL_LAST_WEEK},
        sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _read(conn, sql: str) -> pd.DataFrame:
    with conn.cursor() as cur:
        cur.execute(sql, {"w0": PANEL_FIRST_WEEK, "w1": PANEL_LAST_WEEK})
        cols = [d.name for d in cur.description]
        return pd.DataFrame(cur.fetchall(), columns=cols)


def build_panel(conn, use_cache: bool = True) -> pd.DataFrame:
    """Return the dense department x week frame, weeks 20..101.

    Columns: department, week_no, revenue, dept_baskets, dept_households,
    panel_revenue, panel_baskets, panel_households, promo_rows, on_display,
    in_mailer, campaigns_active, campaign_households.

    No feature engineering happens here and no rows are dropped for
    qualification -- that is rrip.forecast.features, so that the raw panel can
    be inspected and the inclusion rule can be applied against training weeks
    only.
    """
    cache = CACHE_DIR / f"panel-{_cache_key()}.csv"
    if use_cache and cache.exists():
        logger.info("forecast panel: reading cache %s", cache.name)
        return pd.read_csv(cache, dtype=CACHE_DTYPES)

    logger.info("forecast panel: querying weeks %d..%d (the fact_causal "
                "aggregate scans 36.8M rows)", PANEL_FIRST_WEEK, PANEL_LAST_WEEK)

    rev = _read(conn, REVENUE_SQL)
    panel = _read(conn, PANEL_SQL)
    promo = _read(conn, PROMO_SQL)
    camp = _read(conn, CAMPAIGN_SQL)

    df = rev.merge(panel, on="week_no", how="left")
    df = df.merge(promo, on=["week_no", "department"], how="left")
    df = df.merge(camp, on="week_no", how="left")

    # fact_causal starts at week 9 and stops at 101, so within weeks 20..101
    # a missing promo row means the department had no promoted products that
    # week -- zero exposure, not unknown exposure.
    for col in ("promo_rows", "on_display", "in_mailer"):
        df[col] = df[col].fillna(0).astype("int64")
    for col in ("campaigns_active", "campaign_households"):
        df[col] = df[col].fillna(0).astype("int64")

    df = df.sort_values(["department", "week_no"]).reset_index(drop=True)

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(cache, index=False)
    logger.info("forecast panel: %d rows, %d departments, weeks %d..%d",
                len(df), df.department.nunique(),
                int(df.week_no.min()), int(df.week_no.max()))
    return df


def extend_for_forecast(panel: pd.DataFrame, week: int) -> pd.DataFrame:
    """Append a target-only row per department for a week with no actuals yet.

    Forecasting week W requires a row keyed on W whose features are built from
    weeks W-8..W-1. Those exist; W's own revenue does not, and it must not be
    invented. The appended row therefore carries NaN revenue -- NaN rather than
    zero, because zero is a real observation in this panel and would be
    indistinguishable from a department that genuinely sold nothing.

    Nothing on the appended row feeds a feature of any row that is served: its
    own values would only ever be read by a target at week W+1, which is not
    built. build_features still asserts no feature is NaN, so if that ever
    stopped being true the build would fail rather than serve.
    """
    last = int(panel.week_no.max())
    if week != last + 1:
        raise ValueError(
            f"can only extend by one week: panel ends at {last}, asked for "
            f"{week}. Multi-step forecasting is not supported -- see "
            "contract.SUPPORTED_HORIZONS.")

    depts = sorted(panel.department.unique())
    rows = pd.DataFrame({
        "department": depts,
        "week_no": week,
        "revenue": float("nan"),
        "dept_baskets": 0,
        "dept_households": 0,
        "panel_revenue": float("nan"),
        "panel_baskets": 0,
        "panel_households": 0,
        "promo_rows": 0,
        "on_display": 0,
        "in_mailer": 0,
        "campaigns_active": 0,
        "campaign_households": 0,
    })
    out = pd.concat([panel, rows], ignore_index=True)
    return out.sort_values(["department", "week_no"]).reset_index(drop=True)


def qualifying_departments(panel: pd.DataFrame) -> list[str]:
    """Departments the contract admits, decided on TRAINING weeks alone.

    Using the whole panel here would let test-period activity choose which
    series are modellable. That is selection on the outcome and it inflates
    test scores without any feature ever touching the future.
    """
    lo, hi = C.TRAIN_WEEKS
    train = panel[(panel.week_no >= lo) & (panel.week_no <= hi)]
    stats = train.groupby("department").revenue.agg(
        mean_revenue="mean", nonzero_frac=lambda s: float((s > 0).mean()))
    keep = stats[(stats.mean_revenue >= C.MIN_TRAIN_MEAN_REVENUE)
                 & (stats.nonzero_frac >= C.MIN_TRAIN_NONZERO_FRAC)]
    return sorted(keep.index.tolist())


def panel_summary(panel: pd.DataFrame) -> dict:
    """Facts about the panel, for the artifact's provenance block."""
    quals = qualifying_departments(panel)
    lo, hi = C.TRAIN_WEEKS
    train = panel[(panel.week_no >= lo) & (panel.week_no <= hi)
                  & panel.department.isin(quals)]
    return {
        "weeks": [int(panel.week_no.min()), int(panel.week_no.max())],
        "departments_in_source": int(panel.department.nunique()),
        "departments_qualifying": len(quals),
        "qualifying_departments": quals,
        "rows": int(len(panel)),
        "train_mean_weekly_revenue": round(float(train.revenue.mean()), 2),
        "inclusion_rule": (
            f"mean training-week revenue >= ${C.MIN_TRAIN_MEAN_REVENUE:.2f} "
            f"and >= {C.MIN_TRAIN_NONZERO_FRAC:.0%} of training weeks non-zero, "
            f"evaluated on weeks {lo}-{hi} only"),
    }
