"""Prediction intervals by split conformal calibration.

WHICH KIND OF INTERVAL THIS IS

A PREDICTION interval: a range for a single future observation of department
revenue. It is wider than a confidence interval, which would cover the expected
value, and the difference is not pedantic -- the confidence interval on a
fitted mean can be a few percent wide while the realised week still lands 20%
away. A planner ordering stock needs the range the actual number falls in.

The distinction the report keeps separate:

  confidence interval   where the model's expected value sits
  prediction interval   where next week's actual revenue sits   <- this
  uncertainty estimate  any informal spread; not what is served

WHY CONFORMAL AND NOT THE MODEL'S OWN

HistGradientBoostingRegressor on absolute-error loss produces a point estimate
and nothing else. The alternatives were a quantile-loss model per bound, which
triples the artifacts and can produce crossing quantiles, or a Gaussian
residual assumption, which this data does not support -- the residual
distribution is right-skewed because revenue is bounded below at zero and not
above.

Split conformal makes no distributional assumption at all. It takes the
residuals the model actually made on data it never trained on, and reads the
quantiles off them.

THE ASSUMPTION IT DOES MAKE, AND WHY COVERAGE IS MEASURED ANYWAY

Conformal guarantees marginal coverage under EXCHANGEABILITY of the calibration
and test residuals. Time series are not exchangeable: if the later weeks are
harder than the calibration weeks, the interval is too narrow and the guarantee
does not hold. So the guarantee is treated here as a construction principle,
not as a claim -- realised coverage is measured on the temporal test set and
reported next to the nominal level. If they disagree, the measured number is
the one that is true.

SCALED RESIDUALS

Nonconformity is the residual divided by the department's trailing scale, not
the raw dollar residual. A single global dollar quantile would be set almost
entirely by GROCERY and would produce an interval wider than the entire annual
revenue of SPIRITS. Dividing by scale first makes one department's residual
comparable to another's; the interval is then re-expressed in dollars per
department.

Quantiles are taken of the SIGNED scaled residual rather than its absolute
value, so the interval can be asymmetric. Revenue is bounded below by zero and
unbounded above, and the residuals are correspondingly skewed.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class ConformalIntervals:
    """Calibrated residual quantiles, in units of the trailing scale."""

    level: float                 # nominal coverage, e.g. 0.80
    lower_q: float               # quantile of signed scaled residual
    upper_q: float
    n_calibration: int
    calibration_weeks: tuple[int, int]

    def apply(self, point: np.ndarray, scale: np.ndarray
              ) -> tuple[np.ndarray, np.ndarray]:
        """Turn point forecasts into dollar bounds.

        The lower bound is floored at zero because revenue cannot be negative.
        That floor makes realised coverage slightly HIGHER than nominal for the
        sparse departments rather than lower, which is the safe direction, and
        it is measured either way.
        """
        p = np.asarray(point, float)
        s = np.asarray(scale, float)
        return np.maximum(p + self.lower_q * s, 0.0), p + self.upper_q * s

    def apply_one(self, point: float, scale: float) -> tuple[float, float]:
        return (max(point + self.lower_q * scale, 0.0),
                point + self.upper_q * scale)

    def to_dict(self) -> dict:
        return {"level": self.level, "lower_q": round(self.lower_q, 6),
                "upper_q": round(self.upper_q, 6),
                "n_calibration": self.n_calibration,
                "calibration_weeks": list(self.calibration_weeks),
                "method": "split conformal on signed scale-normalised residuals",
                "kind": "prediction interval"}

    @classmethod
    def from_dict(cls, d: dict) -> ConformalIntervals:
        return cls(level=float(d["level"]), lower_q=float(d["lower_q"]),
                   upper_q=float(d["upper_q"]),
                   n_calibration=int(d["n_calibration"]),
                   calibration_weeks=tuple(d["calibration_weeks"]))  # type: ignore[arg-type]


def calibrate(actual: np.ndarray, point: np.ndarray, scale: np.ndarray,
              level: float, weeks: tuple[int, int]) -> ConformalIntervals:
    """Fit residual quantiles on a calibration set the model did not train on.

    The finite-sample correction matters here. With n calibration points the
    conformal quantile is taken at ceil((n+1)(1-alpha/2))/n rather than at
    (1-alpha/2), which is what makes the coverage guarantee hold at finite n
    instead of asymptotically. At n=322 it is the difference between reading
    the 90th percentile and the 90.3rd; small, but it is the entire reason the
    method has a guarantee, so it is not dropped for tidiness.
    """
    a = np.asarray(actual, float)
    p = np.asarray(point, float)
    s = np.asarray(scale, float)
    if not (len(a) == len(p) == len(s)):
        raise ValueError("actual, point and scale must be the same length")
    n = len(a)
    if n < 20:
        raise ValueError(
            f"conformal calibration needs a usable sample; got {n}. Below "
            "roughly 20 points the quantile is the extreme order statistic and "
            "the interval is noise.")
    if not 0 < level < 1:
        raise ValueError(f"level must be in (0, 1); got {level}")

    resid = (a - p) / np.where(s > 0, s, 1.0)

    alpha = 1.0 - level
    # Two-sided, so alpha/2 in each tail, each with the finite-sample bump.
    k = min(np.ceil((n + 1) * (1 - alpha / 2)) / n, 1.0)
    upper_q = float(np.quantile(resid, k))
    lower_q = float(np.quantile(resid, 1.0 - k))

    return ConformalIntervals(level=level, lower_q=lower_q, upper_q=upper_q,
                              n_calibration=n, calibration_weeks=weeks)


def coverage(actual: np.ndarray, lower: np.ndarray,
             upper: np.ndarray) -> dict:
    """Realised coverage and mean width. The number that decides the claim."""
    a = np.asarray(actual, float)
    lo = np.asarray(lower, float)
    hi = np.asarray(upper, float)
    inside = (a >= lo) & (a <= hi)
    denom = np.where(np.abs(a) > 0, np.abs(a), np.nan)
    return {
        "n": int(len(a)),
        "coverage": round(float(inside.mean()), 4),
        "mean_width": round(float((hi - lo).mean()), 2),
        "median_width": round(float(np.median(hi - lo)), 2),
        "mean_relative_width": round(float(np.nanmean((hi - lo) / denom)), 4),
        "below_lower": int((a < lo).sum()),
        "above_upper": int((a > hi).sum()),
    }
