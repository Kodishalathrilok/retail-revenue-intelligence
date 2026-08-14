"""Forecast endpoints (Phase 9).

WHAT THE LLM MAY AND MAY NOT DO HERE

Nothing on this path calls a language model. The NL layer can route a question
to these endpoints and translate it into `department` and `horizon` -- that is
in rrip.ai.forecast_intent, and it emits parameters, never figures. Every
number below was computed by rrip.forecast at training time, scored on a
temporal test set, and stored. The same request returns the same number.

WHY THIS IMPORTS ALMOST NOTHING

rrip.forecast.service depends on the standard library alone, deliberately, so
that this router loads on the published tier where the scientific stack is not
installed. Contrast rrip.api.causal_routes, which defers its statsmodels import
into the handler for the same reason. Here it is not even needed: serving is a
lookup, because the forecasts were precomputed.

ERROR CODES ARE PART OF THE CONTRACT

Each refusal carries a machine-readable `error` field, because the caller most
likely to hit one is the NL layer, and "insufficient history" has to be
distinguishable from "unknown department" by something more reliable than
matching on prose.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query

from rrip.config import settings
from rrip.forecast import service as FS

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/forecast", tags=["forecast"])

# Refusal code -> HTTP status.
#
# INSUFFICIENT_HISTORY is 422 rather than 404: the department exists and the
# request is well-formed, but the data cannot support the answer. Collapsing it
# to 404 would tell a caller the department is unknown, which is false and
# would send someone looking for a spelling mistake.
STATUS = {
    "UNKNOWN_DEPARTMENT": 404,
    "OUT_OF_RANGE": 422,
    "UNSUPPORTED_HORIZON": 422,
    "INSUFFICIENT_HISTORY": 422,
}


def _handle(exc: FS.ForecastRequestError) -> HTTPException:
    return HTTPException(STATUS.get(exc.code, 400), exc.to_dict())


def _unavailable(exc: FS.ForecastUnavailable) -> HTTPException:
    """503, not 500. The service is fine; it has no model to serve.

    A 500 says the server broke and invites a retry. A 503 with this body says
    an artifact is missing and names the command that builds it.
    """
    return HTTPException(503, {
        "error": FS.ForecastUnavailable.code,
        "message": str(exc),
        "remedy": "Run `rrip forecast-train` to build the artifact.",
    })


@router.get("")
async def get_forecast(
    department: str = Query(..., description="Department name, e.g. GROCERY"),
    week: int | None = Query(None, description=(
        "Week being predicted. Omit for the next week after the last observed "
        "one.")),
    horizon: int = Query(1, description="Weeks ahead. Only 1 is supported."),
) -> dict:
    """One department's forecast for one week, with its prediction interval."""
    if settings.is_published:
        return await _published_forecast(department, week, horizon)
    try:
        return FS.forecast(department, week, horizon)
    except FS.ForecastUnavailable as exc:
        raise _unavailable(exc) from exc
    except FS.ForecastRequestError as exc:
        raise _handle(exc) from exc


@router.get("/departments")
async def departments() -> dict:
    """Modelled departments, with the measured test error for each.

    The error travels with the list on purpose. A caller picking a department
    from a dropdown should be able to see that GARDEN CENTER's forecasts carry
    a 146% WAPE before choosing it, not after acting on one.
    """
    if settings.is_published:
        from rrip.api.db import fetch
        rows = await fetch(
            """SELECT department, test_wape, servable
               FROM pub_forecast_departments ORDER BY department""")
        return {"items": [{"department": r["department"],
                           "test_wape": (float(r["test_wape"])
                                         if r["test_wape"] is not None else None),
                           "servable": r["servable"]} for r in rows]}
    try:
        store = FS.load_store()
    except FS.ForecastUnavailable as exc:
        raise _unavailable(exc) from exc

    per = store.metadata["metrics"]["test"].get("per_department", {})

    # Servability is evaluated at the week that would actually be forecast, not
    # across the whole history. A department that traded two years ago and has
    # sold nothing for eight weeks is not servable now, and `any()` over every
    # stored week reported it as though it were.
    target = store.observed_until_week + 1

    items = []
    for d in store.departments:
        row = store.rows.get((d, target))
        items.append({
            "department": d,
            "test_wape": (round(float(per[d]["wape"]), 2) if d in per else None),
            "servable": bool(row["servable"]) if row else False,
            "trailing_scale_usd": (round(row["scale_usd"], 2) if row else None),
        })
    return {"items": items, "forecast_week": target}


@router.get("/history/{department}")
async def get_history(department: str,
                      weeks: int = Query(26, ge=4, le=80)) -> dict:
    """Actuals alongside forecasts, for the chart."""
    if settings.is_published:
        return await _published_history(department, weeks)
    try:
        return FS.history(department, weeks)
    except FS.ForecastUnavailable as exc:
        raise _unavailable(exc) from exc
    except FS.ForecastRequestError as exc:
        raise _handle(exc) from exc


@router.get("/summary")
async def get_summary() -> dict:
    """The model card: what is deployed, why, and how well it was measured."""
    if settings.is_published:
        from rrip.api.db import fetch_one
        row = await fetch_one("SELECT payload FROM pub_forecast_summary LIMIT 1")
        if not row:
            raise HTTPException(503, {"error": "MODEL_UNAVAILABLE",
                                      "message": "no published forecast summary"})
        import json
        return json.loads(row["payload"])
    try:
        return FS.summary()
    except FS.ForecastUnavailable as exc:
        raise _unavailable(exc) from exc


# ---------------------------------------------------------------------------
# Published tier
# ---------------------------------------------------------------------------
#
# The hosted deployment has no models/ directory and no filesystem to keep one
# in, so the same precomputed rows are published to Postgres by `rrip publish`.
# The payload shape is identical; only the storage differs.


async def _published_forecast(department: str, week: int | None,
                              horizon: int) -> dict:
    import json

    from rrip.api.db import fetch_one
    from rrip.forecast.contract import SUPPORTED_HORIZONS

    if horizon not in SUPPORTED_HORIZONS:
        raise HTTPException(422, {
            "error": "UNSUPPORTED_HORIZON",
            "message": f"only {list(SUPPORTED_HORIZONS)} week ahead is supported",
            "requested_horizon": horizon})

    if week is None:
        row = await fetch_one(
            """SELECT payload FROM pub_forecast
               WHERE department = %(d)s AND is_next_week
               LIMIT 1""", {"d": department})
    else:
        row = await fetch_one(
            """SELECT payload FROM pub_forecast
               WHERE department = %(d)s AND week_no = %(w)s""",
            {"d": department, "w": week})

    if not row:
        known = await fetch_one(
            "SELECT count(*) AS n FROM pub_forecast WHERE department = %(d)s",
            {"d": department})
        if not known or not known["n"]:
            raise HTTPException(404, {
                "error": "UNKNOWN_DEPARTMENT",
                "message": f"{department!r} is not a modelled department",
                "requested": department})
        raise HTTPException(422, {
            "error": "OUT_OF_RANGE",
            "message": f"no forecast stored for week {week}",
            "requested_week": week})

    payload = json.loads(row["payload"])
    if payload.get("error"):
        raise HTTPException(STATUS.get(payload["error"], 422), payload)
    return payload


async def _published_history(department: str, weeks: int) -> dict:
    from rrip.api.db import fetch

    rows = await fetch(
        """SELECT week_no, split, actual, prediction, lower_bound, upper_bound,
                  baseline_prediction, model_version
           FROM pub_forecast_history
           WHERE department = %(d)s
           ORDER BY week_no DESC LIMIT %(n)s""",
        {"d": department, "n": weeks})
    if not rows:
        raise HTTPException(404, {
            "error": "UNKNOWN_DEPARTMENT",
            "message": f"{department!r} is not a modelled department",
            "requested": department})

    series = [{
        "week_no": int(r["week_no"]), "split": r["split"],
        "actual": float(r["actual"]) if r["actual"] is not None else None,
        "prediction": float(r["prediction"]),
        "lower_bound": float(r["lower_bound"]),
        "upper_bound": float(r["upper_bound"]),
        "baseline_prediction": float(r["baseline_prediction"]),
        "is_forecast": r["actual"] is None,
    } for r in reversed(rows)]
    return {"department": department, "series": series,
            "model_version": rows[0]["model_version"]}
