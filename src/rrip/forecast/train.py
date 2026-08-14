"""The training pipeline, in the order the decisions have to be made.

    panel -> qualify -> features -> LEAKAGE AUDIT -> baselines -> model
    selection (rolling origin) -> fit -> conformal calibration -> UNLOCK TEST
    -> evaluate -> robustness -> artifact

Two orderings in that chain are load-bearing rather than stylistic:

  * The leakage audit runs BEFORE any model is fitted. A pipeline that trains
    first and audits afterwards has already produced a number somebody will
    remember, and the audit then has to argue against it.
  * The test set is unlocked exactly once, after the model and its
    hyperparameters are chosen, and everything downstream of that point is
    reporting rather than deciding. rrip.forecast.split enforces the lock.

WHAT IS SHIPPED IS WHAT WAS MEASURED

The artifact is the model fitted on the TRAINING weeks -- not a refit on
train+validation+test. Refitting on everything is the usual final step and it
would very likely forecast slightly better, but its test performance would be
unknown, and the API returns its own accuracy alongside every forecast. Shipping
the measured model keeps that claim true.

THE PROMOTION RULE

Whether the fitted model or the baseline becomes the served predictor is
decided by decide_deployment() below, against a threshold written down before
the test set was unlocked. A model has to beat the selected baseline AND the
difference has to be distinguishable from zero on a Diebold-Mariano test. Two
conditions rather than one, because at 322 test observations a WAPE gap of a
few hundredths of a point is well inside the noise, and "lower number wins"
would ship whichever model happened to land on the right side of it.

This is written as executable policy rather than prose so that the outcome is
not a judgement call made after seeing a result one might prefer.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from rrip.forecast import baselines as B
from rrip.forecast import contract as C
from rrip.forecast import explain as E
from rrip.forecast import features as F
from rrip.forecast import intervals as I
from rrip.forecast import leakage as L
from rrip.forecast import metrics as M
from rrip.forecast import models as MD
from rrip.forecast import panel as P
from rrip.forecast import registry as R
from rrip.forecast import robustness as RB
from rrip.forecast import split as S

logger = logging.getLogger(__name__)

# Nominal coverage for the served interval. 80% rather than 95%: at this error
# level a 95% band on GROCERY spans roughly a third of weekly revenue, which is
# technically correct and operationally useless. Both are measured and both go
# in the report; 80% is what the API returns by default.
DEFAULT_INTERVAL_LEVEL = 0.80
SECONDARY_INTERVAL_LEVEL = 0.95

# The bar the fitted model must clear on the held-out test weeks to become the
# served predictor. Fixed before the test set was unlocked.
PROMOTION_ALPHA = 0.05
PROMOTION_RULE = (
    "The fitted model is deployed only if BOTH hold on the untouched test "
    "weeks: (1) its WAPE is lower than the baseline selected on validation, "
    "and (2) a Diebold-Mariano test on absolute-error loss rejects equal "
    f"accuracy at p < {PROMOTION_ALPHA} in the model's favour. Otherwise the "
    "baseline is deployed and the model is retained as a measured challenger. "
    "Condition (2) exists because at n=322 a WAPE difference of a few "
    "hundredths of a point is not a result.")


def decide_deployment(actual, model_pred, baseline_pred, baseline_name: str,
                      model_name: str, alpha: float = PROMOTION_ALPHA) -> dict:
    """Apply the promotion rule. Returns the decision and the evidence for it."""
    model_s = M.score(actual, model_pred)
    base_s = M.score(actual, baseline_pred)
    dm = M.diebold_mariano(actual, model_pred, baseline_pred)

    lower = model_s.wape < base_s.wape
    significant = (np.isfinite(dm["p_value"]) and dm["p_value"] < alpha
                   and dm["mean_loss_diff"] < 0)
    deploy_model = bool(lower and significant)

    if deploy_model:
        rationale = (
            f"{model_name} reduced WAPE from {base_s.wape:.2f}% to "
            f"{model_s.wape:.2f}% and the difference is distinguishable from "
            f"zero (Diebold-Mariano p = {dm['p_value']}). Deployed.")
    elif lower:
        rationale = (
            f"{model_name} scored WAPE {model_s.wape:.2f}% against "
            f"{base_s.wape:.2f}% for {baseline_name}, but the difference is "
            f"not distinguishable from zero (Diebold-Mariano p = "
            f"{dm['p_value']}). The baseline is deployed: an unmeasurable "
            "gain does not justify a fitted artifact, a scikit-learn "
            "dependency on the serving path, and a model that can go stale.")
    else:
        rationale = (
            f"{model_name} did not beat {baseline_name} on the test weeks "
            f"(WAPE {model_s.wape:.2f}% against {base_s.wape:.2f}%; "
            f"Diebold-Mariano p = {dm['p_value']}). The baseline is deployed. "
            "This is a valid outcome and it is reported as measured rather "
            "than worked around -- a simpler predictor that performs as well "
            "is the better system.")

    return {
        "rule": PROMOTION_RULE,
        "alpha": alpha,
        "model": model_name,
        "baseline": baseline_name,
        "model_scores": model_s.to_dict(),
        "baseline_scores": base_s.to_dict(),
        "diebold_mariano": dm,
        "model_wape_lower": lower,
        "difference_significant": significant,
        "deployed_predictor": model_name if deploy_model else baseline_name,
        "deployed_kind": "model" if deploy_model else "baseline",
        "rationale": rationale,
    }


def _cv_score(frame: pd.DataFrame, make_model, folds) -> tuple[M.Scores, np.ndarray]:
    acts, preds = [], []
    for _, trf, vaf in S.iter_folds(frame, folds):
        model = make_model()
        model.fit(F.feature_matrix(trf), trf.target_ratio.to_numpy(float),
                  B.clipped_scale(trf))
        preds.append(MD.to_dollars(model.predict(F.feature_matrix(vaf)),
                                   B.clipped_scale(vaf)))
        acts.append(vaf.revenue.to_numpy(float))
    a, p = np.concatenate(acts), np.concatenate(preds)
    return M.score(a, p), p


def select_model(frame: pd.DataFrame) -> dict:
    """Choose model and hyperparameters on rolling-origin CV. Test is untouched.

    The grid is small on purpose (models.candidates). Ten candidates evaluated
    on six folds is already ten chances to pick a lucky one; a few hundred would
    make the winner's validation score a biased estimate of anything, and the
    only clean way to correct for that would be a second held-out set, which is
    the test set, which is locked.
    """
    folds = S.rolling_origin_folds()
    rows = []

    for name, fn in B.BASELINES.items():
        acts, preds = [], []
        for _, _tr, va in S.iter_folds(frame, folds):
            acts.append(va.revenue.to_numpy(float))
            preds.append(fn(va))
        rows.append({"candidate": name, "family": "baseline",
                     **M.score(np.concatenate(acts),
                               np.concatenate(preds)).to_dict()})

    for proto in MD.candidates():
        scores, _ = _cv_score(frame, lambda p=proto: _clone(p), folds)
        rows.append({"candidate": proto.name,
                     "family": type(proto).__name__,
                     "hyperparameters": proto.hyperparameters,
                     **scores.to_dict()})

    baseline_rows = [r for r in rows if r["family"] == "baseline"]
    model_rows = [r for r in rows if r["family"] != "baseline"]

    best_baseline = min(baseline_rows, key=lambda r: r["wape"])
    best_model = min(model_rows, key=lambda r: r["wape"])

    if best_baseline["candidate"] != B.PRIMARY_BASELINE:
        declared = next(r["wape"] for r in baseline_rows
                        if r["candidate"] == B.PRIMARY_BASELINE)
        raise AssertionError(
            f"baselines.PRIMARY_BASELINE is {B.PRIMARY_BASELINE!r} but "
            f"{best_baseline['candidate']!r} won on rolling-origin validation "
            f"(WAPE {best_baseline['wape']:.2f}% vs {declared:.2f}%). "
            "Update the constant and the comment that justifies it rather than "
            "reporting an improvement over a baseline that is not the best one.")

    return {"method": "rolling-origin cross-validation on train+validation",
            "folds": [f.describe() for f in S.rolling_origin_folds()],
            "candidates": rows,
            "best_baseline": best_baseline["candidate"],
            "best_baseline_wape": best_baseline["wape"],
            "selected": best_model["candidate"],
            "selected_wape": best_model["wape"]}


def _clone(proto):
    """A fresh unfitted copy of a candidate, preserving its display name."""
    import copy
    m = copy.deepcopy(proto)
    for attr in ("_spec", "_model"):
        if hasattr(m, attr):
            setattr(m, attr, None)
    return m


def _build_by_name(name: str):
    for proto in MD.candidates():
        if proto.name == name:
            return _clone(proto)
    raise KeyError(f"no candidate named {name!r}")


def run(conn, model_dir=None, use_cache: bool = True) -> dict:
    """Train, evaluate and write the artifact. Returns the full result block."""
    model_dir = model_dir or R.MODEL_DIR

    logger.info("forecast: building panel")
    raw = P.build_panel(conn, use_cache=use_cache)
    quals = P.qualifying_departments(raw)
    stats = F.fit_department_stats(raw, quals)
    summary = P.panel_summary(raw)
    logger.info("forecast: %d qualifying departments", len(quals))

    frame = F.build_features(raw, stats)

    logger.info("forecast: leakage audit")
    audit = L.run_audit(raw, stats, strict=True)

    logger.info("forecast: model selection on rolling-origin CV")
    selection = select_model(frame)
    logger.info("forecast: selected %s (WAPE %.2f%%), best baseline %s "
                "(WAPE %.2f%%)", selection["selected"], selection["selected_wape"],
                selection["best_baseline"], selection["best_baseline_wape"])

    # ---- fit on TRAIN, calibrate on VALIDATION -----------------------------
    tr = S.train_frame(frame)
    va = S.validation_frame(frame)

    model = _build_by_name(selection["selected"])
    model.fit(F.feature_matrix(tr), tr.target_ratio.to_numpy(float),
              B.clipped_scale(tr))

    va_pred = MD.to_dollars(model.predict(F.feature_matrix(va)),
                            B.clipped_scale(va))

    logger.info("forecast: permutation importance on validation")
    importance = E.permutation_importance(model, va)

    logger.info("forecast: oracle leakage comparison")
    oracle = L.oracle_comparison(
        raw, stats, lambda: _build_by_name(selection["selected"]))
    audit["oracle_comparison"] = oracle

    # ---- TEST. Selection is frozen above this line. ------------------------
    te = S.test_frame(frame, S.TEST_UNLOCK)
    te = te.copy()
    te["model_prediction"] = MD.to_dollars(model.predict(F.feature_matrix(te)),
                                           B.clipped_scale(te))
    te["baseline_prediction"] = B.BASELINES[B.PRIMARY_BASELINE](te)

    decision = decide_deployment(
        te.revenue.to_numpy(float), te.model_prediction.to_numpy(float),
        te.baseline_prediction.to_numpy(float), B.PRIMARY_BASELINE,
        selection["selected"])
    logger.info("forecast: deployment decision -- %s",
                decision["deployed_predictor"])

    deployed_is_model = decision["deployed_kind"] == "model"

    def predict_dollars(f: pd.DataFrame) -> np.ndarray:
        """The DEPLOYED predictor. Everything downstream scores this."""
        if deployed_is_model:
            return MD.to_dollars(model.predict(F.feature_matrix(f)),
                                 B.clipped_scale(f))
        return B.BASELINES[B.PRIMARY_BASELINE](f)

    te["prediction"] = predict_dollars(te)

    # Conformal calibration must use the DEPLOYED predictor's residuals. A band
    # fitted to the model's errors and applied to the baseline's forecasts
    # would be an interval for a prediction nobody serves.
    deployed_va_pred = predict_dollars(va)
    conformal = {}
    for level in (DEFAULT_INTERVAL_LEVEL, SECONDARY_INTERVAL_LEVEL):
        ci = I.calibrate(va.revenue.to_numpy(float), deployed_va_pred,
                         B.clipped_scale(va), level, C.VALIDATION_WEEKS)
        conformal[f"{level:.2f}"] = ci.to_dict()

    test_scores = M.score(te.revenue, te.prediction)
    baseline_test: dict[str, dict] = {}
    for name, fn in B.BASELINES.items():
        baseline_test[name] = M.score(te.revenue, fn(te)).to_dict()
    baseline_test["__challenger_model__"] = {
        **M.score(te.revenue, te.model_prediction).to_dict(),
        "note": f"the fitted challenger, {selection['selected']}",
    }

    annual = B.seasonal_naive_annual(te, raw)
    ok = np.isfinite(annual)
    baseline_test["seasonal_naive_52"] = {
        **M.score(te.revenue.to_numpy(float)[ok], annual[ok]).to_dict(),
        "note": ("Lag 52. Cannot be a feature -- a 52-week lookback for the "
                 "first target week would reach week -24 -- but it is "
                 "computable on the test weeks, so it is scored here rather "
                 "than dismissed."),
    }

    # Every model-vs-baseline pairing on the test weeks, so the claim "the
    # model beats naive but not the trailing mean" is a table rather than an
    # assertion.
    pairwise = {}
    for name, fn in B.BASELINES.items():
        pairwise[f"model_vs_{name}"] = M.diebold_mariano(
            te.revenue.to_numpy(float), te.model_prediction.to_numpy(float),
            fn(te))

    per_dept = M.score_by(te, "revenue", "prediction", "department")
    comparison = M.Comparison(
        name=decision["deployed_predictor"], scores=test_scores,
        baseline_name=B.PRIMARY_BASELINE,
        baseline=M.score(te.revenue, te.baseline_prediction),
        per_department=per_dept)

    # ---- intervals on test --------------------------------------------------
    interval_results = {}
    for level_key, spec in conformal.items():
        ci = I.ConformalIntervals.from_dict(spec)
        lo, hi = ci.apply(te.prediction.to_numpy(float), B.clipped_scale(te))
        interval_results[level_key] = {
            "nominal": ci.level,
            **I.coverage(te.revenue.to_numpy(float), lo, hi),
        }

    ci_default = I.ConformalIntervals.from_dict(
        conformal[f"{DEFAULT_INTERVAL_LEVEL:.2f}"])
    te["lower"], te["upper"] = ci_default.apply(
        te.prediction.to_numpy(float), B.clipped_scale(te))

    # ---- robustness ---------------------------------------------------------
    logger.info("forecast: robustness suite")
    robust = RB.run_suite(te, tr, raw, predict_dollars)
    robust["challenger_model"] = {
        "note": ("The same slices scored for the fitted challenger, so the "
                 "decision not to deploy it can be checked segment by segment "
                 "rather than on the pooled number alone."),
        "volume_tiers": RB.by_volume_tier(
            te.assign(prediction=te.model_prediction), "revenue", "prediction",
            tr)["tiers"],
        "department_variation": RB.by_department(
            te.assign(prediction=te.model_prediction), "revenue",
            "prediction")["departments"],
    }

    # ---- serving frame ------------------------------------------------------
    logger.info("forecast: building serving frame")
    serving = _serving_frame(conn, stats, model, ci_default, use_cache,
                             deployed_is_model)

    # ---- artifact -----------------------------------------------------------
    reference = E.reference_values(tr)
    metadata = R.build_metadata(
        model_type=decision["deployed_predictor"],
        hyperparameters=(getattr(model, "hyperparameters", {})
                         if deployed_is_model
                         else {"window_weeks": 4, "statistic": "mean",
                               "form": "mean of revenue in weeks t-3..t"}),
        seed=MD.RANDOM_SEED,
        dataset=summary,
        splits=S.describe_splits(frame),
        department_stats=stats.to_dict(),
        selection=selection,
        deployment=decision,
        challenger={"name": selection["selected"],
                    "hyperparameters": getattr(model, "hyperparameters", {}),
                    "test_scores": M.score(te.revenue,
                                           te.model_prediction).to_dict()},
        metrics={
            "test": comparison.to_dict(),
            "test_baselines": baseline_test,
            "validation_point_model": M.score(va.revenue, va_pred).to_dict(),
            "validation_point_deployed": M.score(va.revenue,
                                                 deployed_va_pred).to_dict(),
            "diebold_mariano_pairwise": pairwise,
            "macro_wape_test": round(M.macro_wape(per_dept), 4),
        },
        conformal={"calibration": conformal, "test_coverage": interval_results,
                   "calibrated_for": decision["deployed_predictor"]},
        importance=[i.to_dict() for i in importance],
        reference_values=reference,
        leakage_audit=audit,
        robustness=robust,
        servable_weeks=sorted(int(w) for w in serving.week_no.unique()),
        departments=quals,
        notes=[
            "The deployed predictor is chosen by the promotion rule in "
            "rrip.forecast.train, applied to the untouched test weeks.",
            "The fitted challenger is trained on TRAIN weeks only and is not "
            "refitted on train+validation+test, so its test metrics describe "
            "the exact artifact stored here.",
            f"Prediction intervals are split-conformal at "
            f"{DEFAULT_INTERVAL_LEVEL:.0%}, calibrated on the DEPLOYED "
            f"predictor's residuals over validation weeks "
            f"{C.VALIDATION_WEEKS[0]}-{C.VALIDATION_WEEKS[1]}.",
            "Permutation importances describe the challenger, not the "
            "deployed predictor. When the baseline is deployed its "
            "explanation is the weeks it averaged, which the service returns "
            "directly.",
        ])

    linear_spec = (model.spec.to_dict()
                   if isinstance(model, MD.RidgeRatio) else None)
    R.save(model_dir, model, metadata, serving, linear_spec)

    return {"metadata": metadata.to_dict(), "comparison": comparison.to_dict(),
            "audit": audit, "robustness": robust, "decision": decision,
            "interval_coverage": interval_results}


def _serving_frame(conn, stats: F.FittedStats, model,
                   ci: I.ConformalIntervals, use_cache: bool,
                   deployed_is_model: bool) -> pd.DataFrame:
    """Precompute every forecast the API can serve, including the future week.

    The API must not run the 36.8M-row causal aggregate on a request, and it
    must return the same number every time it is asked the same question. Both
    follow from computing the forecasts once, here, and storing them.

    The frame extends one week past the panel: week 102 is the genuine
    next-week forecast. It is also a PARTIAL week -- six days, not seven -- and
    the service flags that rather than presenting a six-day forecast as a
    weekly one.
    """
    raw = P.build_panel(conn, use_cache=use_cache)
    forecast_week = int(raw.week_no.max()) + 1
    extended = P.extend_for_forecast(raw, forecast_week)

    frame = F.build_features(extended, stats).copy()

    frame["model_prediction"] = MD.to_dollars(
        model.predict(F.feature_matrix(frame)), B.clipped_scale(frame))
    frame["baseline_prediction"] = B.BASELINES[B.PRIMARY_BASELINE](frame)
    frame["baseline_name"] = B.PRIMARY_BASELINE
    frame["prediction"] = (frame.model_prediction if deployed_is_model
                           else frame.baseline_prediction)

    lo, hi = ci.apply(frame.prediction.to_numpy(float), B.clipped_scale(frame))
    frame["lower"], frame["upper"] = lo, hi
    frame["is_partial_week"] = frame.week_no.isin(C.PARTIAL_WEEKS)
    frame["servable"] = frame.scale_usd >= C.MIN_SERVABLE_SCALE_USD

    # The four weeks the deployed trailing mean averaged, in dollars, so the
    # service can show its arithmetic rather than describing it.
    scale = B.clipped_scale(frame)
    for i in (1, 2, 3, 4):
        frame[f"input_week_lag{i}_usd"] = frame[f"rev_lag{i}_ratio"] * scale
    return frame
