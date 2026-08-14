"""Where the model fails, measured rather than avoided.

A single test-set WAPE hides everything a planner needs to know before trusting
a forecast: whether it degrades over time, whether the headline is one large
department's score wearing a suit, whether it collapses on the small ones, and
what it does when the week is unusual or the data is late.

Every function here returns numbers that go into the release report as they
come out, including the ones that look bad. The promotion split and the sparse
tier are expected to look bad, and reporting them is the point -- a robustness
section where everything passes has not tested anything.

USE OF TARGET-WEEK DATA

Several splits here condition on what actually happened in the target week --
whether it was a promotion-heavy week, whether revenue was an outlier. That is
legitimate at ANALYSIS time and forbidden at PREDICTION time, and the
distinction is real: the model never saw these; they are used to sort its
errors into buckets after the fact. Nothing in this module feeds a feature.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from rrip.forecast import contract as C
from rrip.forecast import metrics as M


def by_time(frame: pd.DataFrame, actual: str, pred: str,
            block: int = 5) -> dict:
    """Does accuracy decay as the forecast origin moves away from training?

    Blocks of `block` weeks rather than per-week, because 23 observations in a
    single week is too few for a stable WAPE and a noisy per-week series
    invites reading a trend into it that is not there.
    """
    f = frame.copy()
    first = int(f.week_no.min())
    f["block"] = ((f.week_no - first) // block).astype(int)
    out = []
    for _block, g in f.groupby("block"):
        s = M.score(g[actual], g[pred])
        out.append({"weeks": [int(g.week_no.min()), int(g.week_no.max())],
                    **s.to_dict()})
    wapes = [o["wape"] for o in out]
    return {
        "blocks": out,
        "first_block_wape": wapes[0] if wapes else None,
        "last_block_wape": wapes[-1] if wapes else None,
        "drift_pp": round(wapes[-1] - wapes[0], 3) if len(wapes) > 1 else None,
        "note": ("Positive drift means later weeks are forecast less "
                 "accurately, which is what a model going stale looks like. "
                 "At 14 test weeks this is a direction, not a trend estimate."),
    }


def by_department(frame: pd.DataFrame, actual: str, pred: str) -> dict:
    """Per-department scores, plus how concentrated the headline is.

    `error_share` is the fraction of total absolute dollar error a department
    contributes. If one department carries most of it, the pooled WAPE is that
    department's WAPE and the other twenty-two are decoration.
    """
    per = M.score_by(frame, actual, pred, "department")
    total_err = sum(s.mae * s.n for s in per.values())
    total_act = sum(s.actual_total for s in per.values())

    rows = []
    for dept, s in per.items():
        rows.append({
            "department": dept,
            **s.to_dict(),
            "error_share": round((s.mae * s.n) / total_err, 4) if total_err else 0.0,
            "revenue_share": round(s.actual_total / total_act, 4) if total_act else 0.0,
        })
    rows.sort(key=lambda r: r["error_share"], reverse=True)

    return {
        "departments": rows,
        "pooled_wape": round(M.score(frame[actual], frame[pred]).wape, 4),
        "macro_wape": round(M.macro_wape(per), 4),
        "top_error_share": rows[0]["error_share"] if rows else None,
        "top_department": rows[0]["department"] if rows else None,
        "note": ("pooled_wape is dollar-weighted and macro_wape weights every "
                 "department equally. A large gap between them means the "
                 "headline describes the biggest department and not the "
                 "system."),
    }


def by_volume_tier(frame: pd.DataFrame, actual: str, pred: str,
                   train_frame: pd.DataFrame) -> dict:
    """Scores split by department size, with tiers cut on TRAINING revenue.

    Cutting the tiers on test revenue would define "small" using the same data
    the scores are computed on. The boundaries are $1,000 and $100 of mean
    weekly training revenue -- round numbers chosen before looking at the
    results, so the tiers are not drawn around where the model happens to work.
    """
    means = train_frame.groupby("department").revenue.mean()

    def tier(dept: str) -> str:
        m = float(means.get(dept, 0.0))
        if m >= 1000:
            return "large (>= $1k/wk)"
        if m >= 100:
            return "medium ($100-$1k/wk)"
        return "sparse (< $100/wk)"

    f = frame.copy()
    f["tier"] = f.department.map(tier)
    out = {}
    for t, g in f.groupby("tier"):
        out[t] = {"departments": sorted(g.department.unique().tolist()),
                  **M.score(g[actual], g[pred]).to_dict()}
    return {"tiers": out,
            "note": ("The sparse tier is expected to score badly: those series "
                     "hit zero repeatedly and a percentage error on a $7 week "
                     "is close to meaningless. They are reported rather than "
                     "excluded, and the API refuses to serve a department "
                     f"whose trailing scale is below "
                     f"${C.MIN_SERVABLE_SCALE_USD:.0f}.")}


def by_promotion(frame: pd.DataFrame, panel: pd.DataFrame,
                 actual: str, pred: str) -> dict:
    """Does accuracy change in weeks that were heavily promoted?

    The split uses the TARGET week's actual display share, which the model
    never saw. If errors are larger in promoted weeks, the strict cutoff on
    promotion features is costing accuracy -- which is the trade the contract
    makes deliberately, and this is the measurement of its price.
    """
    p = panel.copy()
    p["display_pct"] = p.on_display / p.promo_rows.clip(lower=1)
    key = p.set_index(["department", "week_no"]).display_pct

    f = frame.copy()
    f["display_pct"] = [float(key.get((d, w), 0.0))
                        for d, w in zip(f.department, f.week_no, strict=True)]
    cut = float(f.display_pct.median())
    f["promo_band"] = np.where(f.display_pct > cut, "high promotion",
                               "low promotion")

    out = {}
    for band, g in f.groupby("promo_band"):
        out[band] = {"median_display_pct_cut": round(cut, 4),
                     **M.score(g[actual], g[pred]).to_dict()}
    return {"bands": out,
            "note": ("Split on the target week's realised display share, which "
                     "is an analysis-time quantity. The model had only the "
                     "previous week's.")}


def by_outlier(frame: pd.DataFrame, train_frame: pd.DataFrame,
               actual: str, pred: str, z: float = 2.0) -> dict:
    """Accuracy on weeks that were unusual for that department.

    The department's mean and standard deviation come from TRAINING weeks, so
    "unusual" is defined by history rather than by the test period's own
    spread.
    """
    stats = train_frame.groupby("department").revenue.agg(["mean", "std"])

    f = frame.copy()
    mu = f.department.map(stats["mean"])
    sd = f.department.map(stats["std"]).replace(0, np.nan)
    f["z"] = (f[actual] - mu) / sd
    f["band"] = np.where(f.z.abs() >= z, f"outlier (|z| >= {z})",
                         f"normal (|z| < {z})")

    out = {}
    for band, g in f.groupby("band"):
        out[band] = M.score(g[actual], g[pred]).to_dict()
    return {"bands": out, "z_threshold": z,
            "note": ("Spikes are where a trailing-mean forecast is guaranteed "
                     "to be wrong, and no model without the cause of the spike "
                     "in its features can fix that. Reported so the failure is "
                     "sized rather than implied.")}


def stale_data(frame: pd.DataFrame, predict, actual: str) -> dict:
    """What happens when the most recent week has not landed yet.

    Simulated exactly, not approximated: the forecast for week w is taken from
    the feature row built for week w-1, which was constructed from data through
    week w-2. That is precisely the situation where last week's sales have not
    finished loading and the planner runs the model anyway.

    `predict` takes a feature frame and returns dollar predictions.
    """
    f = frame.sort_values(["department", "week_no"]).copy()
    f["fresh_pred"] = predict(f)

    stale = f[["department", "week_no", "fresh_pred"]].copy()
    stale["week_no"] = stale.week_no + 1
    stale = stale.rename(columns={"fresh_pred": "stale_pred"})

    merged = f.merge(stale, on=["department", "week_no"], how="inner")
    if merged.empty:
        return {"note": "not enough consecutive weeks to simulate stale data"}

    fresh = M.score(merged[actual], merged.fresh_pred)
    old = M.score(merged[actual], merged.stale_pred)
    return {
        "fresh": fresh.to_dict(),
        "one_week_stale": old.to_dict(),
        "wape_penalty_pp": round(old.wape - fresh.wape, 4),
        "note": ("The stale run reuses the previous week's forecast, built "
                 "from data one week older. The penalty is what running "
                 "without the latest week costs."),
    }


def run_suite(frame: pd.DataFrame, train_frame: pd.DataFrame,
              panel: pd.DataFrame, predict, actual: str = "revenue",
              pred: str = "prediction") -> dict:
    return {
        "time_drift": by_time(frame, actual, pred),
        "department_variation": by_department(frame, actual, pred),
        "volume_tiers": by_volume_tier(frame, actual, pred, train_frame),
        "promotion_periods": by_promotion(frame, panel, actual, pred),
        "outliers": by_outlier(frame, train_frame, actual, pred),
        "stale_data": stale_data(frame, predict, actual),
    }
