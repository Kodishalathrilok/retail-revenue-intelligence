"""AI endpoints. Every stage of validation is returned, not just the outcome."""

from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from rrip.ai.narration import narrate
from rrip.ai.nl2sql import answer
from rrip.ai.provider import CallRefused, LLMError, LLMUnavailable, get_provider
from rrip.api import limits
from rrip.api.db import fetch
from rrip.api.models import NarrateRequest, NLQueryRequest, NLQueryResponse
from rrip.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/ai", tags=["ai"])

# Every route that can reach a model carries this: origin check, then the
# shared per-IP limit. The daily cap is enforced per model call, inside the
# provider that guarded_provider() returns.
PROTECTED = [Depends(limits.protect)]

_PROVIDER_DOWN = "The language model provider is unavailable. Try again shortly."


def _provider(name: str | None = None):
    return limits.guarded_provider(name)


async def _published_forecast_departments() -> list[str]:
    rows = await fetch("SELECT department FROM pub_forecast_departments ORDER BY department")
    return [r["department"] for r in rows]


def _ai_unavailable(departments: list[str]) -> JSONResponse:
    """The model was needed and could not be reached. Stated as such, with the
    one thing a visitor can do about it: name the department themselves, which
    the deterministic matcher handles without any model."""
    return JSONResponse(status_code=503, content={
        "error": "AI_UNAVAILABLE",
        "message": ("The language model is unavailable, and it was needed to work "
                    "out which department this question is about. Name one of the "
                    "departments below and the forecast works without it."),
        "retry_after": None,
        "available_departments": departments,
    })


def _public_reason(reason: str | None) -> str | None:
    """Provider failures are logged in full and reported generically.

    The raw text is an upstream error body, useful in a log and meaningless (or
    revealing about the stack) in a response. Every other failure reason --
    gate rejections, refusals -- is the product and is returned as is.
    """
    if reason and reason.startswith("provider error"):
        logger.warning("NL->SQL %s", reason)
        return _PROVIDER_DOWN
    return reason


@router.get("/status")
async def status() -> dict:
    """Which providers are configured. Surfaced so a missing key is visible."""
    out = []
    for n in ("gemini", "groq"):
        p = get_provider(n)
        out.append({"provider": n, "model": p.model, "configured": p.available,
                    "requests_per_minute": p.limiter.requests_per_minute})
    return {"providers": out,
            "active": os.getenv("RRIP_LLM_PROVIDER", "gemini"),
            "cache_enabled": settings.llm_cache,
            # on / off / misconfigured -- so a deployment smoke test can assert
            # the AI endpoints are protected without triggering a model call.
            "protection": limits.mode()}


@router.post("/query", response_model=NLQueryResponse, dependencies=PROTECTED)
async def nl_query(req: NLQueryRequest, provider: str | None = None) -> NLQueryResponse:
    """Natural language to SQL, with every validation stage reported.

    A rejected query returns 200 with `succeeded: false` and the full attempt
    trail. The rejection IS the product here -- returning an error status would
    hide the behaviour the endpoint exists to demonstrate.
    """
    try:
        p = _provider(provider)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    try:
        result = await answer(req.question, p, max_attempts=req.max_attempts)
    except LLMUnavailable as exc:
        logger.warning("NL->SQL provider unavailable: %s", exc)
        raise HTTPException(503, _PROVIDER_DOWN) from exc

    return NLQueryResponse(
        question=result.question,
        succeeded=result.succeeded,
        sql=result.sql,
        columns=result.columns,
        rows=result.rows[:200],
        row_count=len(result.rows),
        attempts=[{
            "attempt": a.attempt,
            "sql": a.sql,
            "stages": [{"stage": s.stage, "passed": s.passed,
                        "detail": s.detail, "duration_ms": s.duration_ms}
                       for s in a.stages],
            "rejected_reason": a.rejected_reason,
            "error_fed_back": a.error_fed_back,
        } for a in result.attempts],
        total_duration_ms=result.total_duration_ms,
        provider=result.provider,
        failure_reason=_public_reason(result.failure_reason),
    )


@router.post("/narrate", dependencies=PROTECTED)
async def narrate_endpoint(req: NarrateRequest, provider: str | None = None) -> dict:
    """Grounded narration over a computed result set.

    The caller supplies data that SQL already computed. If the model introduces
    any number absent from that data, the response is REJECTED rather than
    repaired, and the violations are returned so the guard is observable.
    """
    try:
        p = _provider(provider)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    result = await narrate(req.data, req.question, p)
    return {
        "ok": result.ok,
        "narrative": result.narrative if result.ok else None,
        "rejected_reason": _public_reason(result.rejected_reason),
        "violations": [{"value": v.value, "context": v.context, "reason": v.reason}
                       for v in result.violations],
        "provider": result.provider,
        "guard": ("Every number in the narrative must appear in the computed "
                  "input. Responses containing an ungrounded figure are rejected "
                  "wholesale, not repaired."),
    }


@router.post("/ask", dependencies=PROTECTED)
async def ask(payload: dict, provider: str | None = None) -> dict:
    """Route a question to SQL analytics or to the forecasting model.

    The descriptive/predictive split, made structural. The router classifies
    first and deterministically -- no model call -- and a FORECAST verdict never
    reaches SQL generation.

    That ordering is the point. Asked "what will Grocery revenue be next week?"
    with only a SQL tool available, a language model does not refuse; it writes
    a valid aggregate over historical rows and returns a number that passes
    every gate this project has and is not a forecast. The routing decision is
    what prevents it, and it is a table lookup rather than a judgement.

    The model's total involvement on the forecast path is picking a department
    name from a closed list, and only when the deterministic matcher finds
    none. Every figure comes from rrip.forecast.
    """
    from rrip.ai import forecast_intent as FI
    from rrip.ai.router import FORECAST, classify
    from rrip.forecast import service as FS

    question = (payload.get("question") or "").strip()
    if not question:
        raise HTTPException(400, "payload must include 'question'")

    routing = classify(question)
    if routing.verdict != FORECAST:
        return {"question": question, "route": "sql",
                "routing": routing.to_dict(),
                "note": ("Not a predictive question. Send it to "
                         "POST /api/v1/ai/query for the NL->SQL path.")}

    # Tier-aware, like /api/v1/forecast. The published tier has no model
    # artifact on disk -- the Vercel function installs the package, not the
    # repo's models/ directory -- so this read the file store there and every
    # forecast question returned 503. It reads the same pub_forecast* rows the
    # forecast routes serve.
    if settings.is_published:
        departments = await _published_forecast_departments()
    else:
        try:
            departments = FS.load_store().departments
        except FS.ForecastUnavailable as exc:
            raise HTTPException(503, {"error": FS.ForecastUnavailable.code,
                                      "message": str(exc)}) from exc

    intent = FI.resolve(question, departments)
    if not intent.is_complete and not intent.unknown_department:
        # The deterministic matcher found no department; only now is the model
        # asked. If it cannot be asked, say THAT. Replying "Which department?"
        # would present a provider outage as a flaw in the question.
        try:
            p = _provider(provider)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if not p.available:
            return _ai_unavailable(departments)
        try:
            intent = await FI.resolve_with_llm(question, departments, p)
        except CallRefused:
            raise                   # rate limit or daily cap: say so
        except LLMError as exc:
            # The whole fallback chain failed (Gemini already falls through
            # its models on 404/429/503 inside the call).
            logger.warning("/ask provider unavailable: %s", exc)
            return _ai_unavailable(departments)

    if not intent.is_complete:
        return {
            "question": question, "route": "forecast", "succeeded": False,
            "routing": routing.to_dict(), "intent": intent.to_dict(),
            "error": ("UNMODELLED_DEPARTMENT" if intent.unknown_department
                      else "DEPARTMENT_NOT_IDENTIFIED"),
            "message": (
                f"{intent.unknown_department!r} is a department in this dataset "
                "but was not modelled -- it did not meet the inclusion rule on "
                "the training weeks."
                if intent.unknown_department else
                "Which department? The forecast is produced per department."),
            "available_departments": departments,
        }

    try:
        if settings.is_published:
            from rrip.api.forecast_routes import _published_forecast
            result = await _published_forecast(intent.department, None, intent.horizon)
        else:
            result = FS.forecast(intent.department, horizon=intent.horizon)
    except FS.ForecastRequestError as exc:
        return {"question": question, "route": "forecast", "succeeded": False,
                "routing": routing.to_dict(), "intent": intent.to_dict(),
                **exc.to_dict()}
    except HTTPException as exc:
        # The published helper reports the same refusals (unsupported horizon,
        # unknown department) as HTTP errors; keep /ask's one response shape.
        if not isinstance(exc.detail, dict):
            raise
        return {"question": question, "route": "forecast", "succeeded": False,
                "routing": routing.to_dict(), "intent": intent.to_dict(),
                **exc.detail}

    return {
        "question": question, "route": "forecast", "succeeded": True,
        "routing": routing.to_dict(), "intent": intent.to_dict(),
        "forecast": result,
        "guard": ("The language model selected a department name from a fixed "
                  "list and nothing else. The prediction, the interval and the "
                  "confidence flag were computed by rrip.forecast and scored on "
                  "a held-out temporal test set."),
    }


@router.get("/anomalies")
async def anomalies(z_threshold: float = 2.5) -> dict:
    """Anomalies computed in SQL, ready to be narrated.

    The model does not detect these and does not score them. It receives the
    finished list.
    """
    from rrip.api import queries as Q

    rows = await fetch(
        Q.pick(Q.ANOMALIES_LOCAL, Q.ANOMALIES_PUBLISHED), {"z": z_threshold}, timeout_ms=30_000)
    return {"items": rows, "z_threshold": z_threshold,
            "note": ("Partial weeks 1 and 102 are excluded from the mean and "
                     "standard deviation but flagged if they appear, since a "
                     "five-day week is not comparable to a seven-day one.")}
