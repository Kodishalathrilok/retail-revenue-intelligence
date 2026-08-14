"""Error metrics, and the reason each one is here.

MAE   dollars, comparable within a department, meaningless across them.
RMSE  dollars, penalises the large misses that a category plan actually cares
      about. Reported alongside MAE because RMSE >> MAE is itself a finding --
      it means the error is concentrated in a few weeks.
WAPE  sum|error| / sum|actual|, as a percentage. The scale-free headline.

MAPE IS DELIBERATELY ABSENT. This panel contains genuine zero weeks -- FROZEN
GROCERY averages $7.90 a week across 23 departments and lands on zero
repeatedly -- and MAPE is undefined at zero and unbounded near it. A single
$0.84 week with a $3 forecast contributes 257% and moves the mean for the whole
department. WAPE is the pooled ratio of the same quantities and stays finite,
which is why it is the headline here.

sMAPE is computed as a secondary check because WAPE is dominated by whichever
series is largest -- GROCERY is half of all revenue -- and a metric that
weights every department equally is needed to see the small ones at all. Both
are reported; neither is reported alone.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Scores:
    n: int
    mae: float
    rmse: float
    wape: float
    smape: float
    bias: float          # mean signed error; positive means over-forecasting
    actual_total: float

    def to_dict(self) -> dict:
        return {"n": self.n, "mae": round(self.mae, 3),
                "rmse": round(self.rmse, 3), "wape": round(self.wape, 4),
                "smape": round(self.smape, 4), "bias": round(self.bias, 3),
                "actual_total": round(self.actual_total, 2)}


def score(actual, predicted) -> Scores:
    y = np.asarray(actual, dtype=float)
    p = np.asarray(predicted, dtype=float)
    if y.shape != p.shape:
        raise ValueError(f"shape mismatch: actual {y.shape} vs predicted {p.shape}")
    if len(y) == 0:
        raise ValueError("cannot score an empty set")
    if not np.isfinite(p).all():
        raise ValueError("predictions contain NaN or inf")

    err = p - y
    denom = np.abs(y).sum()

    # sMAPE with the standard symmetric denominator. Where actual and forecast
    # are both zero the term is 0/0; it is defined as zero because a correct
    # prediction of nothing is not an error.
    sd = (np.abs(y) + np.abs(p)) / 2.0
    smape_terms = np.where(sd > 0, np.abs(err) / np.where(sd > 0, sd, 1.0), 0.0)

    return Scores(
        n=len(y),
        mae=float(np.abs(err).mean()),
        rmse=float(np.sqrt((err ** 2).mean())),
        wape=float(100.0 * np.abs(err).sum() / denom) if denom > 0 else float("nan"),
        smape=float(100.0 * smape_terms.mean()),
        bias=float(err.mean()),
        actual_total=float(y.sum()),
    )


@dataclass
class Comparison:
    """One model's scores next to the baseline it has to beat."""

    name: str
    scores: Scores
    baseline_name: str
    baseline: Scores
    per_department: dict[str, Scores] = field(default_factory=dict)

    @property
    def wape_improvement_pct(self) -> float:
        """Percentage reduction in WAPE against the baseline. Negative = worse."""
        if not np.isfinite(self.baseline.wape) or self.baseline.wape == 0:
            return float("nan")
        return 100.0 * (self.baseline.wape - self.scores.wape) / self.baseline.wape

    @property
    def mae_improvement_pct(self) -> float:
        if self.baseline.mae == 0:
            return float("nan")
        return 100.0 * (self.baseline.mae - self.scores.mae) / self.baseline.mae

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "scores": self.scores.to_dict(),
            "baseline_name": self.baseline_name,
            "baseline_scores": self.baseline.to_dict(),
            "wape_improvement_pct": round(self.wape_improvement_pct, 2),
            "mae_improvement_pct": round(self.mae_improvement_pct, 2),
            "per_department": {k: v.to_dict() for k, v in self.per_department.items()},
        }


def score_by(frame: pd.DataFrame, actual_col: str, pred_col: str,
             by: str) -> dict[str, Scores]:
    """Scores split by a column, for the segment tables the report needs."""
    out: dict[str, Scores] = {}
    for key, g in frame.groupby(by):
        out[str(key)] = score(g[actual_col], g[pred_col])
    return out


def macro_wape(per_segment: dict[str, Scores]) -> float:
    """Unweighted mean of per-segment WAPE.

    The counterweight to the headline. Pooled WAPE is a dollar-weighted number
    and GROCERY is half the dollars, so a model can post a good pooled WAPE
    while being useless on twenty-two departments. This is the number that
    notices.
    """
    vals = [s.wape for s in per_segment.values() if np.isfinite(s.wape)]
    return float(np.mean(vals)) if vals else float("nan")


def diebold_mariano(actual, pred_a, pred_b, loss: str = "abs") -> dict:
    """Test whether two forecasts differ significantly, on absolute-error loss.

    Comparing two WAPEs and declaring the smaller one better ignores that both
    were measured on the same 322 observations with substantial shared error.
    This is the standard test for exactly that, and it is used here to decide
    whether a WAPE gap is a result or a rounding difference.

    Uses the Harvey-Leybourne-Newbold small-sample correction, which matters at
    this sample size, and a h=1 variance (no autocovariance term, because the
    forecast horizon is one step and the loss differential is not overlapping).
    Returns the statistic, a two-sided p-value from the t distribution, and the
    mean loss differential -- negative means A has lower loss.
    """
    from scipy import stats as sps

    y = np.asarray(actual, float)
    a = np.asarray(pred_a, float)
    b = np.asarray(pred_b, float)

    if loss == "abs":
        d = np.abs(a - y) - np.abs(b - y)
    elif loss == "sq":
        d = (a - y) ** 2 - (b - y) ** 2
    else:
        raise ValueError(f"unknown loss {loss!r}")

    n = len(d)
    dbar = float(d.mean())
    var = float(d.var(ddof=1))
    if n < 3 or var <= 0:
        return {"statistic": float("nan"), "p_value": float("nan"),
                "mean_loss_diff": dbar, "n": n,
                "note": "degenerate: zero variance in the loss differential"}

    stat = dbar / np.sqrt(var / n)
    # HLN correction for h = 1.
    stat *= np.sqrt((n + 1 - 2 * 1 + 1 * (1 - 1) / n) / n)
    p = float(2 * (1 - sps.t.cdf(abs(stat), df=n - 1)))
    return {"statistic": round(float(stat), 4), "p_value": round(p, 5),
            "mean_loss_diff": round(dbar, 4), "n": n}
