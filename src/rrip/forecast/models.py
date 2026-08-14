"""Candidate models, in increasing order of how much explaining they need.

All of them predict the RATIO to the trailing scale, never dollars. Three
consequences follow from that and they are the reason for the design:

  * A model that learns nothing converges on the constant 1.0, which IS the
    8-week trailing-mean baseline. There is no configuration in which the model
    silently underperforms the baselines by an order of magnitude because it
    spent its capacity on scale. (The best baseline turned out to be the 4-week
    mean rather than the 8-week one, so the model still has to earn something;
    the point is that the floor is a baseline rather than a catastrophe.)
  * Sample weights are the trailing scale in dollars. Weighted absolute error
    in ratio space equals absolute error in dollars, so the training objective
    is the reported metric rather than a proxy for it.
  * The linear model exports to a dict of floats. Serving it needs no numpy and
    no scikit-learn, which matters because the deployed API runs on a 250 MB
    serverless limit against a 332.9 MB scientific stack (see pyproject.toml).

WHY NOT DEEP LEARNING

1,058 training rows across 23 series. That is not a sample size where a neural
sequence model can be fitted or, more to the point, evaluated -- the test set
is 322 observations, and the confidence interval on a WAPE measured there is
wide enough to swallow most plausible gains. The benchmark decides between
these candidates on measured validation error, and if the simplest one wins
that is the result.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Protocol

import numpy as np

from rrip.forecast import contract as C

# Fixed everywhere a model can consume randomness. Recorded in the artifact.
RANDOM_SEED = 20260814


class Model(Protocol):
    name: str
    hyperparameters: dict

    def fit(self, x: np.ndarray, y: np.ndarray, w: np.ndarray) -> Model: ...
    def predict(self, x: np.ndarray) -> np.ndarray: ...


# ---------------------------------------------------------------------------
# Linear
# ---------------------------------------------------------------------------


@dataclass
class LinearSpec:
    """A fitted linear model as plain floats -- no numpy, no sklearn.

    This is what gets serialised and what the service scores with. Keeping the
    servable form dependency-free is not tidiness: rrip.api must import it on a
    deployment that cannot carry scikit-learn, and an artifact that can only be
    read by the library that wrote it is an artifact the API cannot use.

    `lower`/`upper` are winsorisation bounds fitted on the training rows. They
    are part of the model, not a data-cleaning step done beforehand, so they
    travel with it and inference reproduces training exactly.
    """

    feature_names: list[str]
    lower: list[float]
    upper: list[float]
    means: list[float]
    stds: list[float]
    coefficients: list[float]
    intercept: float

    def _terms(self, values: dict[str, float]) -> list[tuple[str, float]]:
        out: list[tuple[str, float]] = []
        for name, lo, hi, mean, std, coef in zip(
                self.feature_names, self.lower, self.upper, self.means,
                self.stds, self.coefficients, strict=True):
            if name not in values:
                raise KeyError(f"missing feature {name!r} at inference time")
            v = min(max(float(values[name]), lo), hi)
            out.append((name, coef * ((v - mean) / (std if std else 1.0))))
        return out

    def score_one(self, values: dict[str, float]) -> float:
        """Predict a single ratio from a name->value mapping.

        Keyed by name rather than position on purpose. A reordered feature
        vector produces a plausible wrong number, and this is the code path
        that runs in production.
        """
        return self.intercept + sum(t for _, t in self._terms(values))

    def contributions(self, values: dict[str, float]) -> list[tuple[str, float]]:
        """Per-feature signed contribution to the predicted ratio.

        Exact, not approximate: for a linear model on standardised inputs the
        prediction is the intercept plus these terms, and they sum to it. No
        surrogate, no sampling, no LLM.
        """
        return sorted(self._terms(values), key=lambda t: abs(t[1]), reverse=True)

    def to_dict(self) -> dict:
        return {"kind": "linear", "feature_names": self.feature_names,
                "lower": self.lower, "upper": self.upper,
                "means": self.means, "stds": self.stds,
                "coefficients": self.coefficients, "intercept": self.intercept}

    @classmethod
    def from_dict(cls, d: dict) -> LinearSpec:
        if d.get("kind") != "linear":
            raise ValueError(f"not a linear spec: {d.get('kind')!r}")
        return cls(feature_names=list(d["feature_names"]),
                   lower=[float(v) for v in d["lower"]],
                   upper=[float(v) for v in d["upper"]],
                   means=[float(v) for v in d["means"]],
                   stds=[float(v) for v in d["stds"]],
                   coefficients=[float(v) for v in d["coefficients"]],
                   intercept=float(d["intercept"]))


@dataclass
class RidgeRatio:
    """Ridge on winsorised, standardised features, predicting the ratio.

    Standardisation happens here rather than in a sklearn Pipeline so that the
    fitted constants are values this module owns and can write into a
    LinearSpec. A Pipeline would serialise only as a pickle.

    Regularisation is not optional at this sample size: several features are
    near-collinear by construction (roll4_ratio, momentum_ratio and
    rollmed8_ratio are three views of the same window), and unpenalised OLS
    splits large opposing coefficients between them.

    WINSORISATION, AND WHY IT IS PART OF THE MODEL

    Without it this estimator scored WAPE 8.32% on rolling-origin CV -- worse
    than every baseline including naive. The cause was measured rather than
    guessed: the ratio features are heavy-tailed (rev_lag1_ratio reaches 8.0
    against a median of 0.98, promo_rows_ratio reaches 8.0), and squared loss
    on unbounded inputs spends the fit on a handful of sparse-department weeks.
    Clipping each feature to its 1st-99th training percentile took it to 7.49%.

    That is disclosed here because it changes the reading of the benchmark: the
    linear model still loses to the best baseline, but it loses by a little
    rather than a lot, and the first number would have made the tree model look
    better than it is. The bounds are fitted on training rows only and stored
    in the spec, so validation and test are clipped with training bounds and
    never with their own.
    """

    alpha: float = 1.0
    clip_pct: tuple[float, float] = (1.0, 99.0)
    name: str = "ridge"
    _spec: LinearSpec | None = field(default=None, repr=False)

    @property
    def hyperparameters(self) -> dict:
        return {"alpha": self.alpha, "clip_percentiles": list(self.clip_pct)}

    def fit(self, x: np.ndarray, y: np.ndarray, w: np.ndarray) -> RidgeRatio:
        from sklearn.linear_model import Ridge

        lower = np.percentile(x, self.clip_pct[0], axis=0)
        upper = np.percentile(x, self.clip_pct[1], axis=0)
        xc = np.clip(x, lower, upper)

        means = xc.mean(axis=0)
        stds = xc.std(axis=0)
        stds = np.where(stds > 1e-12, stds, 1.0)

        model = Ridge(alpha=self.alpha, fit_intercept=True)
        model.fit((xc - means) / stds, y, sample_weight=w)

        self._spec = LinearSpec(
            feature_names=list(C.FEATURE_NAMES),
            lower=[float(v) for v in lower],
            upper=[float(v) for v in upper],
            means=[float(v) for v in means],
            stds=[float(v) for v in stds],
            coefficients=[float(v) for v in model.coef_],
            intercept=float(model.intercept_))
        return self

    def predict(self, x: np.ndarray) -> np.ndarray:
        s = self.spec
        xc = np.clip(x, np.asarray(s.lower), np.asarray(s.upper))
        return (((xc - np.asarray(s.means)) / np.asarray(s.stds))
                @ np.asarray(s.coefficients) + s.intercept)

    @property
    def spec(self) -> LinearSpec:
        if self._spec is None:
            raise RuntimeError("model is not fitted")
        return self._spec


# ---------------------------------------------------------------------------
# Trees
# ---------------------------------------------------------------------------


@dataclass
class GradientBoostRatio:
    """HistGradientBoostingRegressor on absolute-error loss.

    Absolute error rather than squared: the headline metrics are MAE and WAPE,
    and squared loss chases the few enormous ratio outliers that sparse
    departments produce. Depth and leaf count are held down hard because the
    training set is roughly a thousand rows -- the defaults will fit it
    perfectly and forecast nothing.
    """

    max_depth: int = 3
    max_leaf_nodes: int = 7
    learning_rate: float = 0.05
    max_iter: int = 300
    min_samples_leaf: int = 40
    l2_regularization: float = 1.0
    name: str = "hist_gradient_boosting"
    _model: Any = field(default=None, repr=False)

    @property
    def hyperparameters(self) -> dict:
        return {"max_depth": self.max_depth, "max_leaf_nodes": self.max_leaf_nodes,
                "learning_rate": self.learning_rate, "max_iter": self.max_iter,
                "min_samples_leaf": self.min_samples_leaf,
                "l2_regularization": self.l2_regularization,
                "loss": "absolute_error", "random_state": RANDOM_SEED}

    def fit(self, x: np.ndarray, y: np.ndarray, w: np.ndarray) -> GradientBoostRatio:
        from sklearn.ensemble import HistGradientBoostingRegressor

        self._model = HistGradientBoostingRegressor(
            loss="absolute_error",
            max_depth=self.max_depth,
            max_leaf_nodes=self.max_leaf_nodes,
            learning_rate=self.learning_rate,
            max_iter=self.max_iter,
            min_samples_leaf=self.min_samples_leaf,
            l2_regularization=self.l2_regularization,
            early_stopping=False,
            random_state=RANDOM_SEED)
        self._model.fit(x, y, sample_weight=w)
        return self

    def predict(self, x: np.ndarray) -> np.ndarray:
        if self._model is None:
            raise RuntimeError("model is not fitted")
        return self._model.predict(x)


@dataclass
class RandomForestRatio:
    """Random forest, as the bagged counterpart to the boosted trees.

    Included so the tree family is represented by more than one fitting
    procedure. If boosting wins only because of its loss function rather than
    because trees suit this problem, comparing against a forest on squared loss
    is what shows it.
    """

    n_estimators: int = 400
    max_depth: int = 6
    min_samples_leaf: int = 20
    max_features: float = 0.5
    name: str = "random_forest"
    _model: Any = field(default=None, repr=False)

    @property
    def hyperparameters(self) -> dict:
        return {"n_estimators": self.n_estimators, "max_depth": self.max_depth,
                "min_samples_leaf": self.min_samples_leaf,
                "max_features": self.max_features, "random_state": RANDOM_SEED}

    def fit(self, x: np.ndarray, y: np.ndarray, w: np.ndarray) -> RandomForestRatio:
        from sklearn.ensemble import RandomForestRegressor

        self._model = RandomForestRegressor(
            n_estimators=self.n_estimators, max_depth=self.max_depth,
            min_samples_leaf=self.min_samples_leaf, max_features=self.max_features,
            n_jobs=-1, random_state=RANDOM_SEED)
        self._model.fit(x, y, sample_weight=w)
        return self

    def predict(self, x: np.ndarray) -> np.ndarray:
        if self._model is None:
            raise RuntimeError("model is not fitted")
        return self._model.predict(x)


# ---------------------------------------------------------------------------
# Candidate grid
# ---------------------------------------------------------------------------
#
# Small and hand-chosen. A large random search over 1,058 rows evaluated on 14
# validation weeks selects noise, and the selection itself would then need a
# held-out set to be trusted -- which is the test set, which is locked.


def candidates() -> list[Model]:
    grid: list[Model] = []
    for alpha in (0.3, 1.0, 3.0, 10.0, 30.0):
        m = RidgeRatio(alpha=alpha)
        m.name = f"ridge(alpha={alpha})"
        grid.append(m)
    for lr, leaves, leaf_n in ((0.05, 7, 40), (0.05, 15, 20), (0.02, 7, 40)):
        m = GradientBoostRatio(learning_rate=lr, max_leaf_nodes=leaves,
                               min_samples_leaf=leaf_n)
        m.name = f"hgb(lr={lr},leaves={leaves},min_leaf={leaf_n})"
        grid.append(m)
    for depth, leaf_n in ((6, 20), (10, 10)):
        m = RandomForestRatio(max_depth=depth, min_samples_leaf=leaf_n)
        m.name = f"rf(depth={depth},min_leaf={leaf_n})"
        grid.append(m)
    return grid


def to_dollars(ratios: np.ndarray, scale: np.ndarray) -> np.ndarray:
    """Convert predicted ratios back to dollars, floored at zero.

    Revenue cannot be negative. The floor is applied here, once, rather than in
    each model, so that every candidate is treated identically and the
    constraint is visible in one place.
    """
    return np.maximum(np.asarray(ratios, float) * np.asarray(scale, float), 0.0)


def is_finite_spec(spec: LinearSpec) -> bool:
    vals = (spec.coefficients + spec.means + spec.stds + spec.lower
            + spec.upper + [spec.intercept])
    return all(math.isfinite(v) for v in vals)
