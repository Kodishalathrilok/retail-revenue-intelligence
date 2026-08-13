"""Causal analysis endpoints (6c)."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Query

from rrip.ai.causal import (
    build_panel,
    check_parallel_trends,
    confidence_verdict,
    estimate_did,
    household_attributes,
    naive_difference,
    propose_confounders,
)
from rrip.ai.provider import get_provider
from rrip.config import settings
from rrip.db.connection import connect

router = APIRouter(prefix="/api/v1/causal", tags=["causal"])

# Contamination measured in Phase 0, carried here so the endpoint can report it
# without recomputing the whole screen.
CONTAMINATION = {26: 6.6, 8: 59.9, 18: 90.8}

DEFAULT_CONFOUNDERS = ["pre_spend", "pre_baskets", "coupon_offers", "has_demographics"]

INTERPRETATION = {
    "naive": ("Before/after change for the treated group alone. This is what a "
              "dashboard reports when nobody asks about a control group. It "
              "absorbs every seasonal and secular trend."),
    "did": ("Difference-in-differences: the treated change minus the control "
            "change over the same period. Valid only if parallel trends holds."),
    "adjusted": ("DiD with household covariates, including coupon_offers -- "
                 "coupons targeted at products a household already bought. That "
                 "is selection on past outcomes, the specific confounder that "
                 "breaks DiD."),
}


@router.get("/campaigns")
async def campaigns() -> dict:
    """Campaigns viable for difference-in-differences, per the Phase 0 screen."""
    return {"items": [
        {"campaign_id": 26, "label": "Campaign 26 (recommended)",
         "contaminated_pct": 6.6, "treated_uncontaminated": 310, "control": 2165,
         "note": "Cleanest campaign in the dataset."},
        {"campaign_id": 8, "label": "Campaign 8 (large sample)",
         "contaminated_pct": 59.9, "treated_uncontaminated": 432, "control": 1202,
         "note": "Largest usable treatment group, moderate contamination."},
        {"campaign_id": 18, "label": "Campaign 18 (stress case)",
         "contaminated_pct": 90.8, "treated_uncontaminated": 104, "control": 987,
         "note": "Retained deliberately as a contaminated counter-example."},
    ]}


async def _published_analysis(campaign_id: int) -> dict:
    """Read a precomputed causal result from the published tier.

    The hosted tier has no fact table, so it cannot re-estimate. It serves the
    estimate, the verdict and the pre-trend series that local computed at
    publish time -- and says so, rather than presenting a stored number as a
    live one.
    """
    import json

    from rrip.api.db import fetch, fetch_one

    row = await fetch_one(
        "SELECT * FROM pub_causal_results WHERE campaign_id = %(cid)s",
        {"cid": campaign_id})
    if not row:
        raise HTTPException(404, f"no published causal result for campaign {campaign_id}")

    series = await fetch(
        """SELECT arm, week_no, mean_spend FROM pub_causal_pretrend
           WHERE campaign_id = %(cid)s ORDER BY arm, week_no""", {"cid": campaign_id})

    def arm(name: str) -> list[dict]:
        return [{"week_no": int(r["week_no"]), "mean_spend": float(r["mean_spend"])}
                for r in series if r["arm"] == name]

    try:
        warnings = json.loads(row["warnings"] or "[]")
    except (TypeError, ValueError):
        warnings = []

    return {
        "campaign_id": row["campaign_id"],
        "treated_n": row["treated_n"], "control_n": row["control_n"],
        "contaminated_pct": float(row["contaminated_pct"]),
        "naive_difference": float(row["naive_difference"]),
        "did_estimate": float(row["did_estimate"]),
        "did_stderr": float(row["did_stderr"]),
        "did_pvalue": float(row["did_pvalue"]),
        "ci_low": float(row["ci_low"]), "ci_high": float(row["ci_high"]),
        "adjusted_estimate": (float(row["adjusted_estimate"])
                              if row["adjusted_estimate"] is not None else None),
        "adjusted_stderr": (float(row["adjusted_stderr"])
                            if row["adjusted_stderr"] is not None else None),
        "confounders_used": [c for c in (row["confounders_used"] or "").split(",") if c],
        "parallel_trends": {
            "passed": row["pt_passed"],
            "treated_slope": float(row["pt_treated_slope"]),
            "control_slope": float(row["pt_control_slope"]),
            "interaction_pvalue": float(row["pt_interaction_pvalue"]),
            "pre_weeks": row["pt_pre_weeks"],
            "verdict": row["pt_verdict"],
        },
        "pre_period_series": {"treated": arm("treated"), "control": arm("control")},
        "confidence": row["confidence"],
        "warnings": warnings,
        "proposed_confounders": [],
        "precomputed": True,
        "interpretation": INTERPRETATION,
    }


@router.get("/analysis/{campaign_id}")
async def analysis(campaign_id: int,
                   adjust: bool = Query(True),
                   propose: bool = Query(False)) -> dict:
    if settings.is_published:
        return await _published_analysis(campaign_id)

    def _compute():
        """Blocking work: psycopg sync + statsmodels OLS on a 36k-row panel.

        Run in a worker thread. Left on the event loop it blocks every other
        request for the duration, which is how this endpoint first timed out.
        """
        with connect() as conn:
            df = build_panel(conn, campaign_id)
            if df.empty:
                return None
            attrs = household_attributes(conn, campaign_id)
        pt = check_parallel_trends(df)
        res = estimate_did(df)
        adj = estimate_did(df, DEFAULT_CONFOUNDERS, attrs) if adjust else res
        return df, pt, naive_difference(df), res, adj

    try:
        computed = await asyncio.to_thread(_compute)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    if computed is None:
        raise HTTPException(404, f"no panel data for campaign {campaign_id}")

    df, pt, naive, res, adj = computed
    treated_n = int(df[df.treated == 1].household_key.nunique())
    control_n = int(df[df.treated == 0].household_key.nunique())

    contaminated = CONTAMINATION.get(campaign_id, 0.0)
    verdict, warnings = confidence_verdict(pt, contaminated, treated_n, res)

    proposed = []
    if propose:
        p = get_provider()
        if p.available:
            proposed = await propose_confounders(
                p, campaign_id,
                context=f"{treated_n} treated, {control_n} control, "
                        f"{contaminated}% contaminated.")

    # Pre-period series for the parallel-trends plot, computed in SQL/pandas.
    pre = df[df.post == 0].groupby(["treated", "week_no"])["spend"].mean().reset_index()
    series = {
        "treated": [{"week_no": int(r.week_no), "mean_spend": round(float(r.spend), 3)}
                    for r in pre[pre.treated == 1].itertuples()],
        "control": [{"week_no": int(r.week_no), "mean_spend": round(float(r.spend), 3)}
                    for r in pre[pre.treated == 0].itertuples()],
    }

    return {
        "campaign_id": campaign_id,
        "treated_n": treated_n,
        "control_n": control_n,
        "contaminated_pct": contaminated,
        "naive_difference": round(naive, 4),
        "did_estimate": round(res["did_estimate"], 4),
        "did_stderr": round(res["did_stderr"], 4),
        "did_pvalue": round(res["did_pvalue"], 4),
        "ci_low": round(res["ci_low"], 4),
        "ci_high": round(res["ci_high"], 4),
        "adjusted_estimate": (round(adj["adjusted_estimate"], 4)
                              if adj.get("adjusted_estimate") is not None else None),
        "adjusted_stderr": (round(adj["adjusted_stderr"], 4)
                            if adj.get("adjusted_stderr") is not None else None),
        "confounders_used": adj.get("confounders_used", []),
        "parallel_trends": {
            "passed": pt.passed,
            "treated_slope": round(pt.treated_slope, 4),
            "control_slope": round(pt.control_slope, 4),
            "interaction_pvalue": round(pt.interaction_pvalue, 4),
            "pre_weeks": pt.pre_weeks,
            "verdict": pt.verdict,
        },
        "pre_period_series": series,
        "confidence": verdict,
        "warnings": warnings,
        "proposed_confounders": proposed,
        "precomputed": False,
        "interpretation": INTERPRETATION,
    }


@router.get("/validation")
async def validation() -> dict:
    """Recovery accuracy against synthetic data with a known true effect."""
    from rrip.ai.causal_validate import run_suite
    results = await asyncio.to_thread(run_suite)
    return {"items": [{
        "scenario": r.scenario,
        "true_effect": r.true_effect,
        "estimated": round(r.estimated, 4),
        "abs_error": round(r.abs_error, 4),
        "pct_error": round(r.pct_error, 2),
        "ci_covers_truth": r.covered,
        "parallel_trends_passed": r.parallel_trends_passed,
    } for r in results],
        "note": ("Scenarios are chosen so that some MUST fail. An estimator that "
                 "passes every scenario is not being tested.")}
