"""Campaign viability analysis for the Phase 6c causal module.

The question this answers: does any dunnhumby campaign support an honest
difference-in-differences? That needs four things simultaneously, and a campaign
failing any one of them is unusable no matter how good it looks on the others:

  1. A treatment group of meaningful size.
  2. A control group that is genuinely untreated *during the analysis window*.
  3. Enough pre-period to *test* parallel trends, not just assert it.
  4. Enough post-period for an effect to appear.

On the control-group definition
-------------------------------
An earlier version defined the control group as households in *no* campaign
ever. That is too strict, and a smoke test showed why: a single blanket campaign
touching the whole panel empties the control group for every other campaign,
falsely reporting that no DiD is possible. Campaigns that ran at a completely
different time do not contaminate this campaign's comparison window.

So the control group here is: households not in this campaign, and not in any
campaign whose window overlaps this one's analysis window (bounded pre-period
through end of campaign). `strict_control_n` -- never treated by anything -- is
still reported, since it is the cleanest group where one exists.

We deliberately do not estimate any treatment effect here. This is a feasibility
screen only; estimation belongs in Phase 6c under DoWhy/statsmodels.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# Feasibility thresholds. Deliberately explicit so the report can state why a
# campaign was rejected rather than silently dropping it.
MIN_PRE_DAYS = 90       # ~13 weeks: enough to fit a pre-trend with any confidence
MIN_POST_DAYS = 14      # below this an effect has no room to show up
MIN_TREATED = 100       # households
MIN_CONTROL = 100       # households
BLANKET_RATIO = 0.90    # treated share of panel above which there is no control group

# Bounded pre-period. Using all history back to day 1 would both dilute the
# trend test and needlessly widen the contamination window.
PRE_WINDOW_DAYS = 180


def _pre_period_trend(
    tx: pd.DataFrame, households: set[int], start_day: int, window_start: int
) -> tuple[float, int]:
    """OLS slope of weekly spend per household over the bounded pre-period.

    Returns (slope, n_weeks). A cheap parallel-trends smell test: if the groups'
    pre-period slopes already diverge sharply, DiD's core assumption is in
    trouble before estimation starts.
    """
    if not households:
        return float("nan"), 0

    pre = tx[
        (tx["day"] >= window_start)
        & (tx["day"] < start_day)
        & (tx["household_key"].isin(households))
    ]
    if pre.empty:
        return float("nan"), 0

    weekly = pre.groupby("week_no")["sales_value"].sum() / len(households)
    if len(weekly) < 3:
        return float("nan"), len(weekly)

    slope = float(np.polyfit(weekly.index.to_numpy(dtype=float), weekly.to_numpy(), 1)[0])
    return slope, len(weekly)


def analyse(
    tx: pd.DataFrame,
    campaign_members: pd.DataFrame,
    campaign_desc: pd.DataFrame,
    pre_window_days: int = PRE_WINDOW_DAYS,
) -> pd.DataFrame:
    """One row per campaign, with a viability verdict and the reasons behind it."""
    panel = set(tx["household_key"].unique())
    ever_treated = set(campaign_members["household_key"].unique())
    never_treated = panel - ever_treated

    members_by_campaign = campaign_members.groupby("campaign")["household_key"].apply(set)
    spans = campaign_desc.set_index("campaign")[["start_day", "end_day"]].to_dict("index")

    rows = []
    for _, c in campaign_desc.iterrows():
        cid = c["campaign"]
        start, end = int(c["start_day"]), int(c["end_day"])

        treated = members_by_campaign.get(cid, set()) & panel

        # Analysis window: bounded pre-period through campaign end.
        window_start = max(1, start - pre_window_days)
        pre_days = start - window_start
        post_days = end - start + 1

        # Campaigns overlapping the analysis window contaminate both groups.
        overlapping = [
            other
            for other, s in spans.items()
            if other != cid and s["start_day"] <= end and s["end_day"] >= window_start
        ]
        overlap_households: set[int] = set()
        for other in overlapping:
            overlap_households |= members_by_campaign.get(other, set())

        contaminated = treated & overlap_households
        control = panel - treated - overlap_households
        strict_control = never_treated

        pre_tx_treated = int(
            tx[(tx["day"] >= window_start) & (tx["day"] < start)
               & (tx["household_key"].isin(treated))].shape[0]
        )
        pre_tx_control = int(
            tx[(tx["day"] >= window_start) & (tx["day"] < start)
               & (tx["household_key"].isin(control))].shape[0]
        )

        slope_t, weeks_t = _pre_period_trend(tx, treated, start, window_start)
        slope_c, weeks_c = _pre_period_trend(tx, control, start, window_start)

        # Reject reasons, accumulated so the report can explain itself.
        reasons = []
        if pre_days < MIN_PRE_DAYS:
            reasons.append(f"pre-period {pre_days}d < {MIN_PRE_DAYS}d")
        if post_days < MIN_POST_DAYS:
            reasons.append(f"post-period {post_days}d < {MIN_POST_DAYS}d")
        if len(treated) < MIN_TREATED:
            reasons.append(f"treated n={len(treated)} < {MIN_TREATED}")
        if len(control) < MIN_CONTROL:
            blockers = [
                str(o)
                for o in overlapping
                if len(members_by_campaign.get(o, set())) / max(len(panel), 1) > BLANKET_RATIO
            ]
            detail = f" (blanket overlap from campaign {', '.join(blockers)})" if blockers else ""
            reasons.append(f"control n={len(control)} < {MIN_CONTROL}{detail}")
        if panel and len(treated) / len(panel) > BLANKET_RATIO:
            reasons.append("blanket campaign - no control group")
        if pre_tx_treated == 0 or pre_tx_control == 0:
            reasons.append("no pre-period transactions in one group")

        rows.append(
            {
                "campaign": cid,
                "type": c.get("description", "?"),
                "start_day": start,
                "end_day": end,
                "window_start": window_start,
                "pre_days": pre_days,
                "post_days": post_days,
                "treated_n": len(treated),
                "control_n": len(control),
                "strict_control_n": len(strict_control),
                "overlapping_campaigns": ",".join(map(str, sorted(overlapping))) or "-",
                "contaminated_n": len(contaminated),
                "contaminated_pct": (
                    round(100 * len(contaminated) / len(treated), 1) if treated else 0.0
                ),
                "pre_tx_treated": pre_tx_treated,
                "pre_tx_control": pre_tx_control,
                "pre_weeks": min(weeks_t, weeks_c),
                "pre_slope_treated": round(slope_t, 4) if slope_t == slope_t else None,
                "pre_slope_control": round(slope_c, 4) if slope_c == slope_c else None,
                "slope_gap": (
                    round(abs(slope_t - slope_c), 4)
                    if slope_t == slope_t and slope_c == slope_c
                    else None
                ),
                "viable": not reasons,
                "reasons": "; ".join(reasons) if reasons else "passes all screens",
            }
        )

    out = pd.DataFrame(rows)
    return out.sort_values(
        ["viable", "pre_days", "treated_n"], ascending=[False, False, False]
    ).reset_index(drop=True)
