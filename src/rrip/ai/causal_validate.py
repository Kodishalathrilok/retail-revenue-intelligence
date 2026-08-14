"""Validate the DiD estimator against synthetic data with a KNOWN true effect.

An estimator that returns a number is not evidence it returns the right number.
These generators plant an effect of known size and report how closely it is
recovered — including the cases the estimator is supposed to fail on.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from rrip.ai.causal import check_parallel_trends, estimate_did


@dataclass
class RecoveryResult:
    scenario: str
    true_effect: float
    estimated: float
    stderr: float
    abs_error: float
    pct_error: float
    covered: bool           # does the 95% CI contain the true effect?
    parallel_trends_passed: bool


def synthetic_panel(
    n_treated: int = 310,
    n_control: int = 2165,
    pre_weeks: int = 26,
    post_weeks: int = 8,
    true_effect: float = 5.0,
    base_spend: float = 30.0,
    treated_offset: float = 8.0,          # level difference DiD should absorb
    common_trend: float = 0.15,
    differential_trend: float = 0.0,      # non-zero breaks parallel trends
    noise: float = 6.0,
    seed: int = 7,
) -> pd.DataFrame:
    """Household-week panel with a planted treatment effect.

    `treated_offset` gives the treated group a permanently higher level, which a
    naive before/after would confuse with an effect and DiD must difference out.
    `differential_trend` deliberately violates parallel trends when non-zero.
    """
    rng = np.random.default_rng(seed)
    rows = []
    for hh in range(n_treated + n_control):
        treated = 1 if hh < n_treated else 0
        hh_effect = rng.normal(0, 4)
        for w in range(pre_weeks + post_weeks):
            post = 1 if w >= pre_weeks else 0
            spend = (base_spend
                     + hh_effect
                     + treated * treated_offset
                     + common_trend * w
                     + treated * differential_trend * w
                     + treated * post * true_effect
                     + rng.normal(0, noise))
            rows.append({"household_key": hh, "treated": treated,
                         "week_no": w, "post": post, "spend": spend})
    return pd.DataFrame(rows)


def recover(scenario: str, **kwargs) -> RecoveryResult:
    true_effect = kwargs.get("true_effect", 5.0)
    df = synthetic_panel(**kwargs)
    res = estimate_did(df)
    pt = check_parallel_trends(df)
    est = res["did_estimate"]
    return RecoveryResult(
        scenario=scenario,
        true_effect=true_effect,
        estimated=est,
        stderr=res["did_stderr"],
        abs_error=abs(est - true_effect),
        pct_error=abs(est - true_effect) / abs(true_effect) * 100 if true_effect else 0.0,
        covered=bool(res["ci_low"] <= true_effect <= res["ci_high"]),
        parallel_trends_passed=pt.passed,
    )


def coverage_study(n_sims: int = 100, true_effect: float = 5.0,
                   base_seed: int = 1000, **panel_kwargs) -> dict:
    """Repeat the experiment across seeds and measure BIAS and CI COVERAGE.

    A single recovery is an anecdote. If the estimator is unbiased and the
    standard errors are honest, then across many synthetic datasets the mean
    estimate lands on the true effect and the 95% interval contains it about 95%
    of the time. Coverage far below 95% means the intervals are too narrow --
    the estimator would be reporting more certainty than it has, which is the
    specific failure that makes a causal number dangerous rather than merely
    wrong.

    Deliberately smaller panels than the real campaign: this runs a hundred
    model fits, and the question here is the sampling behaviour of the
    estimator, not its performance at production scale.
    """
    defaults = dict(n_treated=120, n_control=400, pre_weeks=12, post_weeks=6)
    defaults.update(panel_kwargs)

    estimates, covered, widths = [], 0, []
    for i in range(n_sims):
        df = synthetic_panel(true_effect=true_effect, seed=base_seed + i, **defaults)
        res = estimate_did(df)
        estimates.append(res["did_estimate"])
        widths.append(res["ci_high"] - res["ci_low"])
        if res["ci_low"] <= true_effect <= res["ci_high"]:
            covered += 1

    arr = np.array(estimates)
    return {
        "n_simulations": n_sims,
        "true_effect": true_effect,
        "mean_estimate": float(arr.mean()),
        "bias": float(arr.mean() - true_effect),
        "sd_of_estimates": float(arr.std(ddof=1)),
        "ci_coverage_pct": round(100 * covered / n_sims, 1),
        "nominal_coverage_pct": 95.0,
        "mean_ci_width": float(np.mean(widths)),
        "panel": defaults,
        "base_seed": base_seed,
    }


def run_suite() -> list[RecoveryResult]:
    """Scenarios chosen so that some MUST fail -- an estimator that passes
    everything is not being tested."""
    return [
        recover("clean, effect = 5.0", true_effect=5.0),
        recover("clean, effect = 0.0 (no effect)", true_effect=0.0),
        recover("clean, small effect = 1.0", true_effect=1.0),
        recover("clean, large effect = 20.0", true_effect=20.0),
        recover("high noise", true_effect=5.0, noise=15.0),
        recover("small treated group", true_effect=5.0, n_treated=60),
        recover("large level offset", true_effect=5.0, treated_offset=25.0),
        recover("VIOLATED parallel trends", true_effect=5.0, differential_trend=0.4),
        recover("short pre-period", true_effect=5.0, pre_weeks=5),
    ]
