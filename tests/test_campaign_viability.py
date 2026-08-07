"""Validate the Phase 0 campaign screen against synthetic data with known answers.

The screen decides whether Phase 6c is buildable, so a wrong answer in either
direction is expensive: a false "viable" sends us into building a causal module
the data cannot support, and a false "not viable" wrongly kills the module.
Both directions are tested here.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from rrip.profile import campaigns


def make_tx(n_households: int = 500, max_day: int = 700, seed: int = 0) -> pd.DataFrame:
    """Synthetic transactions: every household shops every 7 days."""
    rng = np.random.default_rng(seed)
    days = np.arange(1, max_day + 1, 7)
    hh = np.arange(1, n_households + 1)
    grid_hh = np.repeat(hh, len(days))
    grid_day = np.tile(days, len(hh))
    return pd.DataFrame(
        {
            "household_key": grid_hh,
            "day": grid_day,
            "week_no": ((grid_day - 1) // 7) + 1,
            "sales_value": rng.uniform(5, 50, size=len(grid_hh)),
        }
    )


def desc(rows: list[tuple[int, str, int, int]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["campaign", "description", "start_day", "end_day"])


def members(mapping: dict[int, range | list[int]]) -> pd.DataFrame:
    out = []
    for cid, hhs in mapping.items():
        for h in hhs:
            out.append({"campaign": cid, "household_key": h, "description": f"Type{cid}"})
    return pd.DataFrame(out)


def only(res: pd.DataFrame, cid: int) -> pd.Series:
    return res[res["campaign"] == cid].iloc[0]


# --- the happy path ---------------------------------------------------------

def test_viable_campaign_passes() -> None:
    tx = make_tx()
    res = campaigns.analyse(tx, members({1: range(1, 201)}), desc([(1, "TypeA", 400, 500)]))
    row = res.iloc[0]

    assert bool(row["viable"])
    assert row["treated_n"] == 200
    assert row["control_n"] == 300
    assert row["pre_days"] == campaigns.PRE_WINDOW_DAYS
    assert row["window_start"] == 400 - campaigns.PRE_WINDOW_DAYS
    assert row["post_days"] == 101
    assert row["contaminated_n"] == 0
    assert row["reasons"] == "passes all screens"


def test_pre_window_is_bounded_not_all_history() -> None:
    """Pre-period is a bounded window, so it does not grow with campaign start day."""
    tx = make_tx(max_day=700)
    late = only(campaigns.analyse(tx, members({1: range(1, 201)}),
                                  desc([(1, "TypeA", 600, 650)])), 1)
    assert late["pre_days"] == campaigns.PRE_WINDOW_DAYS
    assert late["window_start"] == 420


def test_pre_window_truncated_near_start_of_panel() -> None:
    """A campaign starting before a full window has elapsed gets what exists.

    99 days still clears MIN_PRE_DAYS, so truncation alone is not disqualifying.
    """
    tx = make_tx()
    row = only(campaigns.analyse(tx, members({1: range(1, 201)}),
                                 desc([(1, "TypeA", 100, 200)])), 1)
    assert row["window_start"] == 1
    assert row["pre_days"] == 99
    assert bool(row["viable"])


def test_truncated_window_below_threshold_is_rejected() -> None:
    """Truncation becomes disqualifying once it drops under MIN_PRE_DAYS."""
    tx = make_tx()
    row = only(campaigns.analyse(tx, members({1: range(1, 201)}),
                                 desc([(1, "TypeA", 60, 200)])), 1)
    assert row["pre_days"] == 59
    assert not row["viable"]
    assert "pre-period" in row["reasons"]


# --- rejection paths --------------------------------------------------------

def test_short_pre_period_rejected() -> None:
    tx = make_tx()
    row = only(campaigns.analyse(tx, members({1: range(1, 201)}),
                                 desc([(1, "TypeA", 30, 200)])), 1)
    assert not row["viable"]
    assert "pre-period" in row["reasons"]


def test_short_post_period_rejected() -> None:
    tx = make_tx()
    row = only(campaigns.analyse(tx, members({1: range(1, 201)}),
                                 desc([(1, "TypeA", 400, 405)])), 1)
    assert not row["viable"]
    assert "post-period" in row["reasons"]


def test_blanket_campaign_has_no_control() -> None:
    """If everyone is treated there is no counterfactual, however large the n."""
    tx = make_tx()
    row = only(campaigns.analyse(tx, members({1: range(1, 501)}),
                                 desc([(1, "TypeB", 400, 500)])), 1)
    assert not row["viable"]
    assert row["control_n"] == 0
    assert "blanket" in row["reasons"]


def test_small_treatment_group_rejected() -> None:
    tx = make_tx()
    row = only(campaigns.analyse(tx, members({1: range(1, 20)}),
                                 desc([(1, "TypeA", 400, 500)])), 1)
    assert not row["viable"]
    assert "treated n" in row["reasons"]


# --- contamination and the control-group definition -------------------------

def test_overlapping_campaigns_flagged_as_contamination() -> None:
    tx = make_tx()
    d = desc([(1, "TypeA", 400, 500), (2, "TypeC", 450, 550)])
    m = members({1: range(1, 201), 2: range(150, 260)})

    c1 = only(campaigns.analyse(tx, m, d), 1)

    assert c1["contaminated_n"] == 51           # households 150..200 inclusive
    assert c1["contaminated_pct"] == pytest.approx(25.5, abs=0.1)
    assert c1["control_n"] == 241               # 500 - 259 touched in-window
    assert c1["overlapping_campaigns"] == "2"


def test_distant_campaign_does_not_shrink_control_group() -> None:
    """Regression: a blanket campaign outside the window must not zero the control.

    The original implementation defined control as 'never in any campaign', so a
    single blanket campaign anywhere in the panel's history made every campaign
    look unusable. This is the case that exposed it.
    """
    tx = make_tx()
    d = desc([
        (1, "TypeA", 500, 600),   # the campaign under test
        (3, "TypeB", 1, 100),     # blanket, but long finished before the window
    ])
    m = members({1: range(1, 201), 3: range(1, 501)})

    c1 = only(campaigns.analyse(tx, m, d), 1)

    assert c1["overlapping_campaigns"] == "-"
    assert c1["control_n"] == 300               # unaffected by the distant blanket
    assert c1["strict_control_n"] == 0          # but nobody is untouched overall
    assert bool(c1["viable"])


def test_overlapping_blanket_names_the_blocking_campaign() -> None:
    """When a blanket campaign does overlap, the reason should say which one."""
    tx = make_tx()
    d = desc([(1, "TypeA", 400, 500), (3, "TypeB", 350, 450)])
    m = members({1: range(1, 201), 3: range(1, 501)})

    c1 = only(campaigns.analyse(tx, m, d), 1)

    assert not c1["viable"]
    assert c1["control_n"] == 0
    assert "blanket overlap from campaign 3" in c1["reasons"]


def test_overlap_detected_against_pre_window_not_just_campaign_dates() -> None:
    """A campaign ending inside our pre-period still contaminates the baseline."""
    tx = make_tx()
    # Campaign 2 ends at day 350, inside campaign 1's window (start 400 - 180 = 220).
    d = desc([(1, "TypeA", 400, 500), (2, "TypeC", 300, 350)])
    m = members({1: range(1, 201), 2: range(300, 400)})

    c1 = only(campaigns.analyse(tx, m, d), 1)

    assert c1["overlapping_campaigns"] == "2"
    assert c1["control_n"] == 200  # 500 - 200 treated - 100 in campaign 2


def test_strict_control_reported_separately() -> None:
    tx = make_tx(n_households=300)
    d = desc([(1, "TypeA", 400, 500), (2, "TypeC", 600, 650)])
    m = members({1: range(1, 101), 2: range(101, 201)})

    c1 = only(campaigns.analyse(tx, m, d), 1)

    assert c1["treated_n"] == 100
    assert c1["control_n"] == 200        # campaign 2 does not overlap
    assert c1["strict_control_n"] == 100  # but only 100 are never touched at all


# --- misc -------------------------------------------------------------------

def test_pre_trend_slopes_are_computed() -> None:
    tx = make_tx()
    row = only(campaigns.analyse(tx, members({1: range(1, 201)}),
                                 desc([(1, "TypeA", 400, 500)])), 1)
    assert row["pre_slope_treated"] is not None
    assert row["pre_slope_control"] is not None
    assert row["slope_gap"] is not None
    assert row["pre_weeks"] > 0


def test_households_with_no_transactions_excluded_from_treated() -> None:
    """campaign_table lists households that may never appear in transactions."""
    tx = make_tx(n_households=300)
    m = members({1: list(range(1, 201)) + [9001, 9002, 9003]})
    row = only(campaigns.analyse(tx, m, desc([(1, "TypeA", 400, 500)])), 1)
    assert row["treated_n"] == 200  # ghosts dropped, not counted
