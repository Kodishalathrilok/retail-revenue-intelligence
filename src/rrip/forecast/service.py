"""Deterministic forecast serving, and the rules for refusing to serve.

DEPENDENCY-FREE ON PURPOSE

This module imports json, csv, math and pathlib and nothing else. Not numpy,
not pandas, not scikit-learn. The reason is the same one that shapes the rest
of this repository: the deployed API runs under a 250 MB serverless limit
against a 332.9 MB scientific stack, and rrip.api.forecast_routes imports this
at module load.

It is also a correctness property rather than only a size one. The forecasts
were computed once at training time and stored; serving reads them. There is no
second implementation of the feature pipeline on the request path that could
drift away from the one that was evaluated, and the same request returns the
same number on every call and every process.

GOVERNANCE IS THE MAJORITY OF THIS FILE

Producing the number is a dictionary lookup. Deciding whether the number should
be produced at all is the work:

  MODEL_UNAVAILABLE      no artifact, or one built against a different contract
  UNSUPPORTED_HORIZON    anything other than one week ahead
  UNKNOWN_DEPARTMENT     not a modelled series, answered with the list
  INSUFFICIENT_HISTORY   trailing scale below the servable floor
  OUT_OF_RANGE           a week the artifact holds no forecast for

and, for requests that are served, a confidence flag that is derived from the
measured test error for that specific department rather than asserted. A system
that reports the same confidence for GROCERY (test WAPE 7.0%) and
COUP/STR & MFG (67.9%) is not reporting confidence.
"""

from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass
from pathlib import Path

# Import paths only. rrip.forecast.contract is a module of constants and
# dataclasses with no third-party imports, so it is safe on the serving path.
from rrip.forecast.contract import (
    CONTRACT_VERSION,
    FEATURE_VERSION,
    MIN_SERVABLE_SCALE_USD,
    PARTIAL_WEEKS,
    SUPPORTED_HORIZONS,
    TARGET_NAME,
    TARGET_UNIT,
)

MODEL_DIR = Path(__file__).resolve().parents[3] / "models" / "forecast"

# CONFIDENCE AND CAVEATS ARE SEPARATE, AND THE SEPARATION WAS EARNED
#
# The first version of this folded everything into one flag, and the result was
# that all 23 departments returned LIMITED -- because week 102 is a partial
# week, and that caveat applies to every forecast for it. A flag that is always
# the same value is not a flag, and it hid the thing worth surfacing: measured
# test error ranges from 7.0% WAPE for GROCERY to 67.9% for COUP/STR & MFG.
#
# So `confidence` now describes THIS DEPARTMENT'S measured reliability and
# nothing else, and week-level or model-level warnings go in `caveats`, which
# every forecast for that week carries equally.
CONFIDENCE_NORMAL = "NORMAL"      # within 1.5x the pooled test WAPE
CONFIDENCE_LIMITED = "LIMITED"    # 1.5x to 3x
CONFIDENCE_LOW = "LOW"            # worse than 3x

LIMITED_WAPE_MULTIPLE = 1.5
LOW_WAPE_MULTIPLE = 3.0

# Reported drift below this is not distinguishable from noise at 14 test weeks
# split into three blocks, so it is not surfaced as a warning.
DRIFT_WARN_PP = 2.0


class ForecastUnavailable(RuntimeError):
    """No servable artifact. Distinct from a bad request."""

    code = "MODEL_UNAVAILABLE"


class ForecastRequestError(ValueError):
    """The request cannot be served as asked. Carries a machine-readable code."""

    def __init__(self, code: str, message: str, **detail):
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail

    def to_dict(self) -> dict:
        return {"error": self.code, "message": self.message, **self.detail}


def _num(value: str) -> float:
    if value is None or value == "" or value.lower() in ("nan", "none"):
        return float("nan")
    return float(value)


def _bool(value: str) -> bool:
    return str(value).strip().lower() in ("true", "1", "yes")


@dataclass
class ForecastStore:
    """The artifact, loaded once and held read-only."""

    metadata: dict
    rows: dict[tuple[str, int], dict]
    departments: list[str]
    weeks: list[int]

    @property
    def version(self) -> str:
        sha = self.metadata.get("git_sha", "unknown")
        short = sha[:8] if sha != "unknown" else "nogit"
        return (f"{self.metadata['contract_version']}/"
                f"{self.metadata['model_type']}/{short}")

    @property
    def trained_until_week(self) -> int:
        return int(self.metadata["contract"]["windows"]["train"][1])

    @property
    def observed_until_week(self) -> int:
        """Last week with actual revenue, i.e. the forecast origin."""
        actual_weeks = [w for (_d, w), r in self.rows.items()
                        if not math.isnan(r["actual"])]
        return max(actual_weeks) if actual_weeks else self.trained_until_week

    @property
    def pooled_test_wape(self) -> float:
        return float(self.metadata["metrics"]["test"]["scores"]["wape"])

    def department_test_wape(self, department: str) -> float | None:
        per = self.metadata["metrics"]["test"].get("per_department", {})
        row = per.get(department)
        return float(row["wape"]) if row else None


_store: ForecastStore | None = None


def load_store(directory: Path | None = None, force: bool = False) -> ForecastStore:
    """Load and cache the artifact, refusing one built for a different contract."""
    global _store
    if _store is not None and not force:
        return _store

    directory = directory or MODEL_DIR
    meta_path = directory / "metadata.json"
    serving_path = directory / "serving.csv"

    if not meta_path.exists() or not serving_path.exists():
        raise ForecastUnavailable(
            f"no forecast artifact in {directory}. Build one with "
            "`rrip forecast-train`.")

    metadata = json.loads(meta_path.read_text(encoding="utf-8"))

    if metadata.get("contract_version") != CONTRACT_VERSION:
        raise ForecastUnavailable(
            f"artifact contract {metadata.get('contract_version')!r} does not "
            f"match the running contract {CONTRACT_VERSION!r}; retrain.")
    if metadata.get("feature_version") != FEATURE_VERSION:
        raise ForecastUnavailable(
            f"artifact feature version {metadata.get('feature_version')!r} "
            f"does not match the running code {FEATURE_VERSION!r}; retrain.")

    rows: dict[tuple[str, int], dict] = {}
    with serving_path.open(newline="", encoding="utf-8") as fh:
        for raw in csv.DictReader(fh):
            dept = raw["department"]
            week = int(raw["week_no"])
            rows[(dept, week)] = {
                "department": dept,
                "week_no": week,
                "split": raw.get("split", "none"),
                "actual": _num(raw.get("revenue", "")),
                "prediction": _num(raw["prediction"]),
                "lower": _num(raw["lower"]),
                "upper": _num(raw["upper"]),
                "baseline_prediction": _num(raw["baseline_prediction"]),
                "baseline_name": raw.get("baseline_name", ""),
                "model_prediction": _num(raw.get("model_prediction", "")),
                "scale_usd": _num(raw["scale_usd"]),
                "servable": _bool(raw.get("servable", "true")),
                "is_partial_week": _bool(raw.get("is_partial_week", "false")),
                "inputs": [_num(raw.get(f"input_week_lag{i}_usd", ""))
                           for i in (1, 2, 3, 4)],
            }

    _store = ForecastStore(
        metadata=metadata,
        rows=rows,
        departments=sorted({d for d, _ in rows}),
        weeks=sorted({w for _, w in rows}),
    )
    return _store


def _confidence(store: ForecastStore, row: dict) -> tuple[str, list[str]]:
    """How reliable forecasts for THIS department have measurably been.

    Derived from the department's own error on the held-out test weeks,
    expressed relative to the pooled figure. Not a heuristic about the data and
    not a guess -- if a department was forecast badly in the fourteen weeks
    nobody tuned against, that is the best available evidence about the next
    one.
    """
    reasons: list[str] = []
    dept_wape = store.department_test_wape(row["department"])
    pooled = store.pooled_test_wape

    if dept_wape is None:
        return CONFIDENCE_LIMITED, [
            "No per-department test error was recorded for "
            f"{row['department']}, so its reliability is unmeasured."]

    ratio = dept_wape / pooled if pooled else float("inf")
    if ratio > LOW_WAPE_MULTIPLE:
        level = CONFIDENCE_LOW
    elif ratio > LIMITED_WAPE_MULTIPLE:
        level = CONFIDENCE_LIMITED
    else:
        level = CONFIDENCE_NORMAL

    if level != CONFIDENCE_NORMAL:
        reasons.append(
            f"Measured test error for {row['department']} is {dept_wape:.1f}% "
            f"WAPE against {pooled:.1f}% pooled across all departments "
            f"({ratio:.1f}x). Treat this forecast as indicative only.")

    if row["scale_usd"] < 5 * MIN_SERVABLE_SCALE_USD:
        reasons.append(
            f"{row['department']} averaged only ${row['scale_usd']:.2f} a week "
            "over the trailing window. A percentage error on a series that "
            "small is close to meaningless.")

    return level, reasons


def _caveats(store: ForecastStore, row: dict) -> list[str]:
    """Warnings about the request or the model that are not department-specific."""
    out: list[str] = []

    if row["is_partial_week"]:
        out.append(
            f"Week {row['week_no']} is a partial week in this dataset "
            "(6 days, not 7). The predictor was fitted on full weeks only, so "
            "this figure describes a 7-day week and will overstate a 6-day "
            "one by roughly a seventh.")

    drift = (store.metadata.get("robustness", {})
             .get("time_drift", {}).get("drift_pp"))
    if drift is not None and drift > DRIFT_WARN_PP:
        out.append(
            f"Error rose by {drift:.1f} percentage points of WAPE across the "
            "test period, which is consistent with the predictor going stale.")

    if row["split"] == "train":
        out.append(
            "This week was part of the training window, so the figure shown is "
            "in-sample and is not evidence of forecast accuracy.")

    return out


def _explain(store: ForecastStore, row: dict) -> dict:
    """Why this number, computed from the deployed predictor's own arithmetic.

    When the deployed predictor is the trailing mean, the explanation is not an
    approximation of anything -- it is the calculation. The four weekly figures
    listed sum and divide to exactly the forecast. That is a stronger
    explanation than any attribution method produces for a tree ensemble, and
    it is one of the things gained by the model not having earned deployment.
    """
    kind = store.metadata.get("deployment", {}).get("deployed_kind", "baseline")

    if kind == "baseline":
        origin = row["week_no"] - 1
        contributors = [
            {"label": f"Revenue in week {origin - i}",
             "value_usd": round(v, 2),
             "weight": 0.25}
            for i, v in enumerate(row["inputs"]) if not math.isnan(v)
        ]
        return {
            "method": "arithmetic",
            "additive": True,
            "description": (
                "The forecast is the mean of this department's revenue over "
                "the four most recently completed weeks."),
            "contributors": contributors,
        }

    importance = store.metadata.get("importance", [])[:5]
    return {
        "method": "permutation_importance",
        "additive": False,
        "description": (
            "Features the fitted model relies on most, measured by the "
            "increase in error when each is shuffled. These are global "
            "importances, not a decomposition of this particular forecast."),
        "contributors": [{"label": i["label"], "importance": i["importance"]}
                         for i in importance],
    }


def forecast(department: str, week: int | None = None, horizon: int = 1,
             directory: Path | None = None) -> dict:
    """One forecast, or a structured refusal.

    `week` is the week being predicted. Omitted means the next week after the
    last observed one, which is the ordinary request.
    """
    store = load_store(directory)

    if horizon not in SUPPORTED_HORIZONS:
        raise ForecastRequestError(
            "UNSUPPORTED_HORIZON",
            f"This model forecasts {'/'.join(str(h) for h in SUPPORTED_HORIZONS)} "
            f"week ahead only; {horizon} was requested. Every feature is a lag "
            "or a rolling window over observed revenue, and at two weeks ahead "
            "the most informative of them does not exist. Iterating the model "
            "over its own output would produce a number whose error has not "
            "been measured.",
            requested_horizon=horizon,
            supported_horizons=list(SUPPORTED_HORIZONS))

    if department not in store.departments:
        raise ForecastRequestError(
            "UNKNOWN_DEPARTMENT",
            f"{department!r} is not a modelled department.",
            requested=department,
            available_departments=store.departments)

    target_week = week if week is not None else store.observed_until_week + horizon

    row = store.rows.get((department, target_week))
    if row is None:
        raise ForecastRequestError(
            "OUT_OF_RANGE",
            f"No forecast is stored for week {target_week}. This dataset is a "
            f"fixed panel ending at week {store.observed_until_week}; the "
            "model can forecast one week past it and no further.",
            requested_week=target_week,
            available_weeks=[min(store.weeks), max(store.weeks)])

    if not row["servable"] or row["scale_usd"] < MIN_SERVABLE_SCALE_USD:
        raise ForecastRequestError(
            "INSUFFICIENT_HISTORY",
            f"Insufficient history for a reliable forecast. {department} "
            f"averaged ${row['scale_usd']:.2f} a week over the trailing "
            f"window, below the ${MIN_SERVABLE_SCALE_USD:.2f} floor this "
            "service will forecast from. Extrapolating from a series that is "
            "mostly zeros would produce a number with no meaning.",
            department=department, week=target_week,
            trailing_scale_usd=round(row["scale_usd"], 2),
            minimum_scale_usd=MIN_SERVABLE_SCALE_USD)

    confidence, reasons = _confidence(store, row)
    caveats = _caveats(store, row)
    interval = store.metadata["conformal"]["calibration"]
    level = interval[next(iter(interval))]["level"]

    return {
        "target": TARGET_NAME,
        "unit": TARGET_UNIT,
        "department": department,
        "forecast_week": target_week,
        "forecast_origin_week": target_week - horizon,
        "horizon_weeks": horizon,
        "prediction": round(row["prediction"], 2),
        "lower_bound": round(row["lower"], 2),
        "upper_bound": round(row["upper"], 2),
        "interval": {
            "level": level,
            "kind": "prediction interval",
            "method": "split conformal on scale-normalised residuals",
            "measured_coverage": store.metadata["conformal"]
                .get("test_coverage", {}).get(f"{level:.2f}", {}).get("coverage"),
        },
        "baseline": {
            "name": row["baseline_name"],
            "prediction": round(row["baseline_prediction"], 2),
        },
        "challenger_model": {
            "name": store.metadata.get("challenger", {}).get("name"),
            "prediction": (round(row["model_prediction"], 2)
                           if not math.isnan(row["model_prediction"]) else None),
            "deployed": store.metadata.get("deployment", {})
                .get("deployed_kind") == "model",
        },
        "actual": (round(row["actual"], 2)
                   if not math.isnan(row["actual"]) else None),
        "in_sample": row["split"] == "train",
        "confidence": confidence,
        "confidence_reasons": reasons,
        "caveats": caveats,
        "explanation": _explain(store, row),
        "model_version": store.version,
        "model_type": store.metadata["model_type"],
        "trained_until_week": store.trained_until_week,
        "observed_until_week": store.observed_until_week,
        "accuracy": {
            "test_weeks": store.metadata["contract"]["windows"]["test"],
            "pooled_wape": store.pooled_test_wape,
            "department_wape": store.department_test_wape(department),
        },
    }


def history(department: str, weeks: int = 26,
            directory: Path | None = None) -> dict:
    """Actuals and out-of-sample forecasts for one department, for charting."""
    store = load_store(directory)
    if department not in store.departments:
        raise ForecastRequestError(
            "UNKNOWN_DEPARTMENT", f"{department!r} is not a modelled department.",
            requested=department, available_departments=store.departments)

    keys = sorted(w for (d, w) in store.rows if d == department)[-weeks:]
    series = []
    for w in keys:
        r = store.rows[(department, w)]
        series.append({
            "week_no": w,
            "split": r["split"],
            "actual": None if math.isnan(r["actual"]) else round(r["actual"], 2),
            "prediction": round(r["prediction"], 2),
            "lower_bound": round(r["lower"], 2),
            "upper_bound": round(r["upper"], 2),
            "baseline_prediction": round(r["baseline_prediction"], 2),
            "is_forecast": math.isnan(r["actual"]),
        })
    return {"department": department, "series": series,
            "model_version": store.version,
            "note": ("Rows in the 'train' split are in-sample and are shown "
                     "for continuity only; they are not evidence of accuracy.")}


def summary(directory: Path | None = None) -> dict:
    """What the service knows about itself, for the UI and the docs."""
    store = load_store(directory)
    dep = store.metadata.get("deployment", {})
    return {
        "target": TARGET_NAME,
        "grain": store.metadata["contract"]["target"]["grain"],
        "unit": TARGET_UNIT,
        "horizon_weeks": list(SUPPORTED_HORIZONS),
        "model_version": store.version,
        "model_type": store.metadata["model_type"],
        "deployed_kind": dep.get("deployed_kind"),
        "deployment_rationale": dep.get("rationale"),
        "challenger": store.metadata.get("challenger", {}),
        "departments": store.departments,
        "weeks": [min(store.weeks), max(store.weeks)],
        "observed_until_week": store.observed_until_week,
        "windows": store.metadata["contract"]["windows"],
        "metrics": store.metadata["metrics"],
        "conformal": store.metadata["conformal"],
        "leakage_audit_passed": store.metadata.get("leakage_audit", {}).get("passed"),
        "partial_weeks": list(PARTIAL_WEEKS),
    }
