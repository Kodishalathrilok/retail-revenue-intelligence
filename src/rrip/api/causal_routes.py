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
from rrip.db.connection import connect

router = APIRouter(prefix="/api/v1/causal", tags=["causal"])

# Contamination measured in Phase 0, carried here so the endpoint can report it
# without recomputing the whole screen.
CONTAMINATION = {26: 6.6, 8: 59.9, 18: 90.8}

DEFAULT_CONFOUNDERS = ["pre_spend", "pre_baskets", "coupon_offers", "has_demographics"]


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


@router.get("/analysis/{campaign_id}")
async def analysis(campaign_id: int,
                   adjust: bool = Query(True),
                   propose: bool = Query(False)) -> dict:
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
        "interpretation": {
            "naive": ("Before/after change for the treated group alone. This is "
                      "what a dashboard reports when nobody asks about a control "
                      "group. It absorbs every seasonal and secular trend."),
            "did": ("Difference-in-differences: the treated change minus the "
                    "control change over the same period. Valid only if parallel "
                    "trends holds."),
            "adjusted": ("DiD with household covariates, including coupon_offers "
                         "-- coupons targeted at products a household already "
                         "bought. That is selection on past outcomes, the "
                         "specific confounder that breaks DiD."),
        },
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
