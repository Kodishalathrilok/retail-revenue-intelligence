"""6c -- Causal verification: difference-in-differences on a real campaign.

DIVISION OF LABOUR, ENFORCED BY STRUCTURE

  SQL          builds the treated and control panels
  statsmodels  estimates the effect
  the model    proposes candidate confounders from the schema, and nothing else

The LLM never sees an estimate, never produces a number, and its output is a
list of column names checked against the actual schema before use. A confounder
it proposes that does not exist in the schema is discarded, not trusted.

WHY CAMPAIGN 26

Phase 0 screened all 30 campaigns. Campaign 26 is the only one combining a
usable treatment group with low contamination: 310 uncontaminated treated
households against 2,165 control, 6.6% contaminated, 180-day pre-period.
Campaign 18 is retained as a STRESS CASE at 90.8% contamination -- reporting a
contaminated estimate beside a clean one shows what contamination does to an
answer, which a caveat cannot.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import statsmodels.formula.api as smf

from rrip.ai.provider import LLMProvider

# Columns a proposed confounder is allowed to name. A model proposal outside
# this set is discarded -- the schema is the authority, not the suggestion.
SCHEMA_COLUMNS = {
    "age_desc", "marital_status_code", "income_desc", "homeowner_desc",
    "hh_comp_desc", "household_size_desc", "kid_category_desc",
    "has_demographics", "pre_spend", "pre_baskets", "store_id",
    "has_promo_coverage", "coupon_offers", "department", "brand",
    "commodity_desc", "week_no", "day_number",
}

CONFOUNDER_PROMPT = """\
You are helping design a difference-in-differences study of a retail marketing
campaign. Your ONLY task is to propose candidate confounders.

You must NOT estimate any effect, produce any number, or draw any conclusion.

Available household and transaction attributes:
  age_desc, marital_status_code, income_desc, homeowner_desc, hh_comp_desc,
  household_size_desc, kid_category_desc, has_demographics,
  pre_spend (household spend in the pre-period),
  pre_baskets (household basket count in the pre-period),
  store_id, has_promo_coverage, coupon_offers (count of coupons targeted at
  products the household already bought), week_no, day_number

Context you must account for:
  - Campaign assignment was NOT randomised. dunnhumby targeted households.
  - Only 801 of 2,500 households have demographic attributes, and those
    households generate 55% of transactions -- so demographic availability is
    itself correlated with shopping behaviour.
  - Coupon targeting correlates with prior purchasing.

Return ONLY JSON:
{
  "confounders": [
    {"variable": "<exact name from the list above>",
     "mechanism": "how it could affect both treatment assignment and outcome",
     "priority": "high" | "medium" | "low"}
  ],
  "assumption_risks": ["specific threats to the parallel trends assumption"]
}"""


# Columns that must be numeric before any model fit. Postgres NUMERIC arrives as
# Decimal, which pandas types as `object` -- indistinguishable by dtype from a
# genuine string column, and silently misread as categorical.
NUMERIC_COLUMNS = {
    "spend", "baskets", "pre_spend", "pre_baskets", "coupon_offers",
    "week_no", "day_number", "treated", "post",
}

# Above this many distinct values, a column claiming to be categorical is
# almost certainly a misclassified continuous variable.
MAX_CATEGORICAL_LEVELS = 50


class DtypeError(TypeError):
    """A column reached a model fit with a type that would be misinterpreted."""


def assert_model_dtypes(df: pd.DataFrame, columns: list[str]) -> None:
    """Fail loudly rather than let a type be inferred wrongly.

    This exists because inference already failed once, expensively: pre_spend
    arrived as Decimal, was typed `object`, was read as categorical, and
    produced a 2,430-column design matrix. The fit took 341 seconds and
    returned an estimate 5.5x the correct one. Nothing errored.

    See docs/methodology-notes.md, error 8.
    """
    problems: list[str] = []
    for c in columns:
        if c not in df.columns:
            continue
        s = df[c]
        if c in NUMERIC_COLUMNS:
            if not pd.api.types.is_numeric_dtype(s):
                problems.append(
                    f"{c!r} must be numeric before fitting but has dtype "
                    f"{s.dtype} (Decimal from a NUMERIC column types as object; "
                    "coerce with pd.to_numeric)")
        elif s.dtype == object and s.nunique() > MAX_CATEGORICAL_LEVELS:
            problems.append(
                f"{c!r} is object dtype with {s.nunique():,} distinct values; "
                f"above {MAX_CATEGORICAL_LEVELS} it is treated as continuous, "
                "because a categorical that wide is a misclassified number")
    if problems:
        bullets = "\n  - ".join(problems)
        raise DtypeError(f"dtype assertion failed before model fit:\n  - {bullets}")


@dataclass
class ParallelTrends:
    passed: bool
    treated_slope: float
    control_slope: float
    slope_difference: float
    interaction_pvalue: float
    pre_weeks: int
    verdict: str


@dataclass
class CausalResult:
    campaign_id: int
    treated_n: int
    control_n: int
    contaminated_pct: float
    naive_difference: float
    did_estimate: float
    did_stderr: float
    did_pvalue: float
    ci_low: float
    ci_high: float
    adjusted_estimate: float | None
    adjusted_stderr: float | None
    confounders_used: list[str]
    parallel_trends: ParallelTrends
    confidence: str
    warnings: list[str] = field(default_factory=list)
    proposed_confounders: list[dict] = field(default_factory=list)


def build_panel(conn, campaign_id: int, pre_window: int = 180,
                post_window: int | None = None) -> pd.DataFrame:
    """Household-week panel of spend, with treatment and period flags.

    The treated group excludes households enrolled in any campaign overlapping
    this one's analysis window -- the same definition Phase 0 used to screen
    campaigns. Control excludes them too.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT start_day, end_day FROM dim_campaign WHERE campaign_id = %s",
                    (campaign_id,))
        row = cur.fetchone()
        if not row:
            raise ValueError(f"campaign {campaign_id} not found")
        start_day, end_day = int(row[0]), int(row[1])

        window_start = max(1, start_day - pre_window)
        window_end = end_day if post_window is None else min(end_day, start_day + post_window)

        cur.execute("""
            WITH overlapping AS (
                SELECT DISTINCT b.household_key
                FROM dim_campaign c
                JOIN bridge_campaign_household b ON b.campaign_id = c.campaign_id
                WHERE c.campaign_id <> %(cid)s
                  AND c.start_day <= %(wend)s AND c.end_day >= %(wstart)s
            ),
            treated AS (
                SELECT b.household_key FROM bridge_campaign_household b
                WHERE b.campaign_id = %(cid)s
                  AND b.household_key NOT IN (SELECT household_key FROM overlapping)
            ),
            control AS (
                SELECT h.household_key FROM dim_household h
                WHERE h.household_key NOT IN (
                          SELECT household_key FROM bridge_campaign_household
                          WHERE campaign_id = %(cid)s)
                  AND h.household_key NOT IN (SELECT household_key FROM overlapping)
            ),
            panel AS (
                SELECT household_key, 1 AS treated FROM treated
                UNION ALL
                SELECT household_key, 0 FROM control
            )
            SELECT p.household_key,
                   p.treated,
                   ft.week_no,
                   (ft.day_number >= %(start)s)::int          AS post,
                   sum(ft.sales_value)                        AS spend,
                   count(DISTINCT ft.basket_id)               AS baskets
            FROM panel p
            JOIN fact_transactions ft ON ft.household_key = p.household_key
            WHERE ft.day_number BETWEEN %(wstart)s AND %(wend)s
            GROUP BY p.household_key, p.treated, ft.week_no, 4
        """, {"cid": campaign_id, "start": start_day,
              "wstart": window_start, "wend": window_end})
        rows = cur.fetchall()
        cols = [d.name for d in cur.description]

    df = pd.DataFrame(rows, columns=cols)
    df["spend"] = df["spend"].astype(float)
    return df


def check_parallel_trends(df: pd.DataFrame, alpha: float = 0.05) -> ParallelTrends:
    """Test whether treated and control trends differ BEFORE treatment.

    The formal test is an interaction between group and time on pre-period data
    only. A significant interaction means the groups were already diverging, and
    difference-in-differences attributes that divergence to the campaign.
    """
    pre = df[df["post"] == 0]
    weekly = (pre.groupby(["treated", "week_no"])["spend"].mean().reset_index())
    pre_weeks = weekly["week_no"].nunique()

    if pre_weeks < 4:
        return ParallelTrends(False, float("nan"), float("nan"), float("nan"),
                              float("nan"), pre_weeks,
                              "INSUFFICIENT pre-period to test parallel trends")

    model = smf.ols("spend ~ week_no * treated", data=weekly).fit()
    interaction = model.params.get("week_no:treated", float("nan"))
    pval = model.pvalues.get("week_no:treated", float("nan"))

    t_slope = float(np.polyfit(
        weekly.loc[weekly.treated == 1, "week_no"],
        weekly.loc[weekly.treated == 1, "spend"], 1)[0])
    c_slope = float(np.polyfit(
        weekly.loc[weekly.treated == 0, "week_no"],
        weekly.loc[weekly.treated == 0, "spend"], 1)[0])

    passed = bool(pval > alpha)
    verdict = ("Pre-period trends are statistically indistinguishable "
               f"(interaction p = {pval:.3f}). Parallel trends is supported."
               if passed else
               f"Pre-period trends DIVERGE (interaction p = {pval:.4f}). "
               "Parallel trends is VIOLATED and the DiD estimate is not "
               "attributable to the campaign.")
    return ParallelTrends(passed, t_slope, c_slope, float(interaction),
                          float(pval), pre_weeks, verdict)


def estimate_did(df: pd.DataFrame, confounders: list[str] | None = None,
                 household_attrs: pd.DataFrame | None = None) -> dict:
    """Two-way fixed effects DiD. Returns the estimate, never a verdict."""
    assert_model_dtypes(df, ["spend", "treated", "post"])
    base = smf.ols("spend ~ treated * post", data=df).fit(
        cov_type="cluster", cov_kwds={"groups": df["household_key"]})
    term = "treated:post"
    out = {
        "did_estimate": float(base.params[term]),
        "did_stderr": float(base.bse[term]),
        "did_pvalue": float(base.pvalues[term]),
        "ci_low": float(base.conf_int().loc[term, 0]),
        "ci_high": float(base.conf_int().loc[term, 1]),
        "adjusted_estimate": None,
        "adjusted_stderr": None,
        "confounders_used": [],
    }

    if confounders and household_attrs is not None:
        merged = df.merge(household_attrs, on="household_key", how="left")
        usable = [c for c in confounders
                  if c in merged.columns and merged[c].notna().sum() > 0]

        # Postgres NUMERIC arrives as Decimal, which pandas types as `object`.
        # A dtype==object test therefore misreads a continuous column as
        # categorical: pre_spend has 2,430 distinct values, so C(pre_spend)
        # built a 2,430-column design matrix and the fit took 341 seconds.
        # Coerce anything numeric-convertible to float BEFORE deciding.
        for c in usable:
            if merged[c].dtype == object:
                coerced = pd.to_numeric(merged[c], errors="coerce")
                if coerced.notna().sum() == merged[c].notna().sum():
                    merged[c] = coerced.astype(float)

        if usable:
            # Types are ASSERTED here, not inferred. Coercion above may have
            # failed silently for a column that is genuinely non-numeric.
            assert_model_dtypes(merged, usable)

            # Only genuine strings become categorical, and only within a sane
            # level count.
            terms = []
            for c in usable:
                is_cat = (merged[c].dtype == object
                          and merged[c].nunique() <= MAX_CATEGORICAL_LEVELS)
                terms.append(f"C({c})" if is_cat else c)
            terms = " + ".join(terms)
            adj = smf.ols(f"spend ~ treated * post + {terms}", data=merged).fit(
                cov_type="cluster", cov_kwds={"groups": merged["household_key"]})
            out["adjusted_estimate"] = float(adj.params[term])
            out["adjusted_stderr"] = float(adj.bse[term])
            out["confounders_used"] = usable
    return out


def naive_difference(df: pd.DataFrame) -> float:
    """Before/after difference for the treated group ALONE.

    This is the number a dashboard reports when nobody asks about a control
    group. It is included precisely so it can be compared with the DiD estimate.
    """
    t = df[df["treated"] == 1]
    return float(t[t.post == 1]["spend"].mean() - t[t.post == 0]["spend"].mean())


def household_attributes(conn, campaign_id: int, pre_window: int = 180) -> pd.DataFrame:
    with conn.cursor() as cur:
        cur.execute("SELECT start_day FROM dim_campaign WHERE campaign_id = %s",
                    (campaign_id,))
        start_day = int(cur.fetchone()[0])
        cur.execute("""
            SELECT h.household_key, h.age_desc, h.income_desc, h.homeowner_desc,
                   h.household_size_desc, h.has_demographics,
                   coalesce(pre.pre_spend, 0)   AS pre_spend,
                   coalesce(pre.pre_baskets, 0) AS pre_baskets,
                   coalesce(cp.coupon_offers, 0) AS coupon_offers
            FROM dim_household h
            LEFT JOIN (
                SELECT household_key, sum(sales_value) AS pre_spend,
                       count(DISTINCT basket_id) AS pre_baskets
                FROM fact_transactions
                WHERE day_number BETWEEN %(wstart)s AND %(start)s - 1
                GROUP BY household_key) pre ON pre.household_key = h.household_key
            LEFT JOIN (
                -- Coupon offers on products the household ALREADY bought before
                -- the campaign. This is selection on past outcomes, the specific
                -- confounder that breaks DiD.
                SELECT ft.household_key, count(DISTINCT bcp.coupon_upc) AS coupon_offers
                FROM fact_transactions ft
                JOIN bridge_coupon_product bcp ON bcp.product_id = ft.product_id
                JOIN bridge_coupon_campaign bcc ON bcc.coupon_upc = bcp.coupon_upc
                WHERE bcc.campaign_id = %(cid)s
                  AND ft.day_number < %(start)s
                GROUP BY ft.household_key) cp ON cp.household_key = h.household_key
        """, {"cid": campaign_id, "start": start_day,
              "wstart": max(1, start_day - pre_window)})
        rows = cur.fetchall()
        cols = [d.name for d in cur.description]
    return pd.DataFrame(rows, columns=cols)


async def propose_confounders(provider: LLMProvider, campaign_id: int,
                              context: str = "") -> list[dict]:
    """Model proposes; schema decides. Proposals outside the schema are dropped."""
    prompt = (f"Campaign {campaign_id} in a household panel of 2,500 households. "
              f"{context}\nPropose candidate confounders.")
    try:
        resp = await provider.complete(prompt, system=CONFOUNDER_PROMPT)
    except Exception:
        return []

    import re
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", resp.text.strip(),
                  flags=re.IGNORECASE)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return []

    out: list[dict] = []
    for c in parsed.get("confounders", []):
        if not isinstance(c, dict):
            continue
        var = str(c.get("variable", "")).strip()
        # The schema is the authority. A hallucinated column name is discarded.
        c["in_schema"] = var in SCHEMA_COLUMNS
        out.append(c)
    return out


def confidence_verdict(pt: ParallelTrends, contaminated_pct: float,
                       treated_n: int, res: dict) -> tuple[str, list[str]]:
    warnings: list[str] = []
    if not pt.passed:
        warnings.append(
            "PARALLEL TRENDS VIOLATED -- the groups were already diverging before "
            "the campaign, so the estimate below cannot be attributed to it.")
    if contaminated_pct > 25:
        warnings.append(
            f"{contaminated_pct:.1f}% of enrolled households were simultaneously "
            "in an overlapping campaign; this measures a bundle, not this campaign.")
    if treated_n < 100:
        warnings.append(f"treated n = {treated_n} is small; the interval is wide.")
    if res["did_pvalue"] > 0.05:
        warnings.append(
            f"the DiD estimate is not statistically distinguishable from zero "
            f"(p = {res['did_pvalue']:.3f}).")

    if not pt.passed or contaminated_pct > 50:
        return "NOT CREDIBLE", warnings
    if warnings:
        return "WEAK", warnings
    return "CREDIBLE", warnings
