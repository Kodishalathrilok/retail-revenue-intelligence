"""Deterministic baselines. These are the bar, not a formality.

The measured autocorrelation structure decides which of these is the real
competitor, and on this data it is NOT the naive one. Post-ramp weekly revenue
has lag-1 autocorrelation near zero at panel level (+0.07), so last week's
figure is a poor predictor of next week's and an 8-week trailing mean beats it
by roughly a fifth. Reporting a model as "19% better than naive" would
therefore be measuring the weakness of the naive rule rather than the strength
of the model.

So every comparison in this project is against the BEST baseline, and the best
baseline is identified on validation data like any other choice.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pandas as pd

from rrip.forecast import contract as C


def clipped_scale(frame: pd.DataFrame) -> np.ndarray:
    """The divisor the features were built with.

    Ratio features were formed against scale_usd clipped at SCALE_FLOOR_USD, so
    recovering dollars requires the same clip. Multiplying by the unclipped
    scale would silently rescale the sparse departments.
    """
    return frame.scale_usd.clip(lower=C.SCALE_FLOOR_USD).to_numpy(dtype=float)


def _from_ratio(frame: pd.DataFrame, col: str) -> np.ndarray:
    return frame[col].to_numpy(dtype=float) * clipped_scale(frame)


def naive(frame: pd.DataFrame) -> np.ndarray:
    """Baseline 1 -- next week equals last week."""
    return _from_ratio(frame, "rev_lag1_ratio")


def seasonal_naive(frame: pd.DataFrame) -> np.ndarray:
    """Baseline 2 -- next week equals the same point in the previous cycle.

    The cycle is 4 weeks, taken from the measured autocorrelation rather than
    from convention: lag-4 is the strongest non-adjacent lag on this panel
    (+0.36 overall, +0.43 GROCERY, +0.52 MEAT-PCKGD). The conventional weekly
    choice, lag 52, was tested and is worse than having no seasonal term --
    see seasonal_naive_annual().
    """
    return _from_ratio(frame, "rev_lag4_ratio")


def trailing_mean(frame: pd.DataFrame) -> np.ndarray:
    """Baseline 3 -- the 8-week trailing mean.

    Identically the normalisation scale, so this baseline is the constant
    prediction ratio 1.0. That is the whole reason the model predicts a ratio:
    it begins level with this baseline and has to earn any departure from it.
    """
    return clipped_scale(frame)


def trailing_median(frame: pd.DataFrame) -> np.ndarray:
    """Trailing median -- the outlier-resistant version of the above."""
    return _from_ratio(frame, "rollmed8_ratio")


def trailing_mean_4(frame: pd.DataFrame) -> np.ndarray:
    """A shorter trailing mean, which trades stability for responsiveness."""
    return _from_ratio(frame, "roll4_ratio")


def drift(frame: pd.DataFrame) -> np.ndarray:
    """Last week plus the fitted slope of the last 8 weeks.

    Included because several departments are genuinely trending -- KIOSK-GAS
    carries lag-1 autocorrelation +0.72 -- and a flat trailing mean cannot
    follow them. Clipped at zero: revenue cannot be negative, and an
    unconstrained linear extrapolation of a falling series produces one.
    """
    scale = clipped_scale(frame)
    last = frame.rev_lag1_ratio.to_numpy(float) * scale
    slope = frame.trend_slope_ratio.to_numpy(float) * scale
    return np.maximum(last + slope, 0.0)


BASELINES: dict[str, Callable[[pd.DataFrame], np.ndarray]] = {
    "naive": naive,
    "seasonal_naive_4": seasonal_naive,
    "trailing_mean_8": trailing_mean,
    "trailing_median_8": trailing_median,
    "trailing_mean_4": trailing_mean_4,
    "drift": drift,
}

# The baseline every model is judged against.
#
# It is trailing_mean_4, not naive and not trailing_mean_8, because that is
# what won on rolling-origin validation: WAPE 7.16% against 7.33% for the
# 8-week mean, 8.23% for seasonal-naive-4 and 9.29% for naive. Quoting an
# improvement over naive would be quoting a 23% gain that mostly measures how
# badly last-week's-figure performs on a series with lag-1 autocorrelation
# near zero.
#
# train.py re-derives the winner from validation scores and raises if it is not
# this one, so the constant cannot drift away from the measurement it claims.
PRIMARY_BASELINE = "trailing_mean_4"


def predict_all(frame: pd.DataFrame) -> dict[str, np.ndarray]:
    return {name: fn(frame) for name, fn in BASELINES.items()}


def seasonal_naive_annual(frame: pd.DataFrame, panel: pd.DataFrame) -> np.ndarray:
    """Lag-52 seasonal naive, computed from the panel rather than the features.

    It is not in the feature set and cannot be: a 52-week lookback for the
    first target week would reach week -24. It IS computable for the test weeks
    (88-101 look back to 36-49, both above the history floor), so it is scored
    there and reported as a recorded negative rather than asserted away.

    Returns NaN where the lagged week is unavailable; the caller drops those.
    """
    lag = C.REJECTED_SEASONAL_LAG_WEEKS
    lookup = {(str(r.department), int(r.week_no)): float(r.revenue)
              for r in panel.itertuples()}
    return np.array([lookup.get((str(d), int(w) - lag), np.nan)
                     for d, w in zip(frame.department, frame.week_no, strict=True)])
