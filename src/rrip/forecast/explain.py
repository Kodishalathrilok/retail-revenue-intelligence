"""Why the model produced this number. Deterministic, and computed from the model.

Two layers, because they answer different questions:

  GLOBAL   permutation importance -- which features the model relies on across
           the whole validation set. Computed once at training time and stored
           in the artifact.
  LOCAL    per-forecast contributions -- what pushed THIS week's number away
           from the department's trailing mean. Computed at request time.

NO LLM IS INVOLVED IN EITHER. When the narration layer describes a forecast it
receives these fields as trusted input, exactly as rrip.ai.derive supplies
computed figures to narration elsewhere in this project. The model is told what
the drivers were; it is not asked to work them out.

THE HONESTY PROBLEM WITH LOCAL ATTRIBUTION

For a linear model the contributions are exact: prediction = intercept + sum of
terms, and the terms ARE the explanation. For a tree ensemble there is no such
decomposition without SHAP, and SHAP is not in this dependency set. What is
implemented instead is reference ablation: hold every other feature and move
one feature to its training median, then report how far the prediction moves.

That is honest and deterministic, but it is NOT additive -- the parts do not
sum to the whole, because the model has interactions and one-at-a-time changes
cannot see them. The API says so in the payload (`additive: false`) rather than
presenting the numbers as a decomposition they are not. Labelling an
approximate attribution as exact is the same category of error as the invented
figures the rest of this project is built to prevent.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from rrip.forecast import contract as C

PERMUTATION_SEED = 20260814
PERMUTATION_REPEATS = 20

# Shown to users instead of the raw column name. The forecast page and the
# narration payload both read this, so a feature is described in one place.
FEATURE_LABELS: dict[str, str] = {
    "rev_lag1_ratio": "Last week's revenue",
    "rev_lag2_ratio": "Revenue two weeks ago",
    "rev_lag3_ratio": "Revenue three weeks ago",
    "rev_lag4_ratio": "Revenue four weeks ago (seasonal lag)",
    "roll4_ratio": "4-week average",
    "rollmed8_ratio": "8-week median",
    "rollstd8_ratio": "8-week volatility",
    "momentum_ratio": "Short-term momentum (4-week vs 8-week)",
    "trend_slope_ratio": "8-week trend",
    "zeros_in_window": "Weeks with no sales in the last 8",
    "panel_rev_lag1_ratio": "Store-wide revenue last week",
    "panel_hh_lag1_ratio": "Store-wide active households last week",
    "panel_baskets_lag1_ratio": "Store-wide baskets last week",
    "dept_share_lag1": "Department share of revenue last week",
    "dept_share_delta": "Change in department share",
    "promo_display_pct_lag1": "Products on in-store display last week",
    "promo_mailer_pct_lag1": "Products in the mailer last week",
    "promo_display_delta": "Change in display promotion",
    "promo_rows_ratio": "Promoted product count vs its 8-week norm",
    "campaigns_active_lag1": "Marketing campaigns running last week",
    "campaign_households_lag1": "Households enrolled in a live campaign",
    "dept_volatility": "How volatile this department normally is",
    "dept_persistence": "How much this department follows last week",
    "dept_log_scale": "Department size",
}


def label(name: str) -> str:
    return FEATURE_LABELS.get(name, name)


@dataclass(frozen=True)
class Importance:
    feature: str
    label: str
    importance: float     # mean increase in weighted MAE when permuted
    std: float

    def to_dict(self) -> dict:
        return {"feature": self.feature, "label": self.label,
                "importance": round(self.importance, 6),
                "std": round(self.std, 6)}


def permutation_importance(model, frame: pd.DataFrame,
                           repeats: int = PERMUTATION_REPEATS,
                           seed: int = PERMUTATION_SEED) -> list[Importance]:
    """Increase in dollar MAE when one feature is shuffled.

    Measured on data the model did not train on -- shuffling a feature on the
    training set reports how much the model memorised, not how much it depends
    on that feature to forecast.

    The permutation is drawn from a seeded Generator, so the same artifact
    produces the same importances on every run. An unseeded importance table
    that changes between runs is not evidence about the model.
    """
    from rrip.forecast import baselines as B
    from rrip.forecast import features as F
    from rrip.forecast import models as MD

    x = F.feature_matrix(frame)
    y = frame.revenue.to_numpy(float)
    scale = B.clipped_scale(frame)

    def mae(mat: np.ndarray) -> float:
        return float(np.abs(MD.to_dollars(model.predict(mat), scale) - y).mean())

    base = mae(x)
    rng = np.random.default_rng(seed)
    out: list[Importance] = []
    for j, name in enumerate(C.FEATURE_NAMES):
        deltas = []
        for _ in range(repeats):
            perm = x.copy()
            perm[:, j] = rng.permutation(perm[:, j])
            deltas.append(mae(perm) - base)
        arr = np.asarray(deltas)
        out.append(Importance(name, label(name), float(arr.mean()),
                              float(arr.std(ddof=1))))
    out.sort(key=lambda i: i.importance, reverse=True)
    return out


@dataclass(frozen=True)
class LocalContribution:
    feature: str
    label: str
    value: float
    effect_usd: float
    direction: str        # "raises" | "lowers" | "neutral"

    def to_dict(self) -> dict:
        return {"feature": self.feature, "label": self.label,
                "value": round(self.value, 6),
                "effect_usd": round(self.effect_usd, 2),
                "direction": self.direction}


def local_contributions(model, values: dict[str, float], scale: float,
                        reference: dict[str, float],
                        top_n: int = 5) -> tuple[list[LocalContribution], bool]:
    """What moved this forecast, in dollars.

    Returns (contributions, additive). `additive` is True only for the linear
    model, where the terms provably sum to the prediction. For everything else
    the numbers are reference-ablation effects and the caller must not present
    them as a decomposition.
    """
    from rrip.forecast.models import RidgeRatio

    if isinstance(model, RidgeRatio):
        terms = model.spec.contributions(values)
        out = [LocalContribution(n, label(n), float(values[n]), t * scale,
                                 _direction(t)) for n, t in terms[:top_n]]
        return out, True

    x = np.array([[values[n] for n in C.FEATURE_NAMES]], dtype=float)
    base = float(model.predict(x)[0])

    effects: list[tuple[str, float]] = []
    for j, name in enumerate(C.FEATURE_NAMES):
        alt = x.copy()
        alt[0, j] = reference[name]
        effects.append((name, base - float(model.predict(alt)[0])))

    effects.sort(key=lambda t: abs(t[1]), reverse=True)
    out = [LocalContribution(n, label(n), float(values[n]), e * scale,
                             _direction(e)) for n, e in effects[:top_n]]
    return out, False


def _direction(x: float) -> str:
    if x > 1e-9:
        return "raises"
    if x < -1e-9:
        return "lowers"
    return "neutral"


def reference_values(frame: pd.DataFrame) -> dict[str, float]:
    """The neutral point each feature is ablated toward.

    The median of the TRAINING rows. The mean would be dragged by the same
    heavy tails that broke the linear model, so an "average" reference would
    sit somewhere no department actually is.
    """
    return {n: float(frame[n].median()) for n in C.FEATURE_NAMES}
