"""AI endpoints. Every stage of validation is returned, not just the outcome."""

from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException

from rrip.ai.narration import narrate
from rrip.ai.nl2sql import answer
from rrip.ai.provider import LLMUnavailable, get_provider
from rrip.api.db import fetch
from rrip.api.models import NLQueryRequest, NLQueryResponse

router = APIRouter(prefix="/api/v1/ai", tags=["ai"])


def _provider(name: str | None = None):
    return get_provider(name, use_cache=os.getenv("RRIP_LLM_CACHE", "1") != "0")


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
            "cache_enabled": os.getenv("RRIP_LLM_CACHE", "1") != "0"}


@router.post("/query", response_model=NLQueryResponse)
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
        raise HTTPException(503, str(exc)) from exc

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
        failure_reason=result.failure_reason,
    )


@router.post("/narrate")
async def narrate_endpoint(payload: dict, provider: str | None = None) -> dict:
    """Grounded narration over a computed result set.

    The caller supplies data that SQL already computed. If the model introduces
    any number absent from that data, the response is REJECTED rather than
    repaired, and the violations are returned so the guard is observable.
    """
    data = payload.get("data")
    question = payload.get("question", "Explain these results.")
    if data is None:
        raise HTTPException(400, "payload must include 'data'")

    try:
        p = _provider(provider)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    result = await narrate(data, question, p)
    return {
        "ok": result.ok,
        "narrative": result.narrative if result.ok else None,
        "rejected_reason": result.rejected_reason,
        "violations": [{"value": v.value, "context": v.context, "reason": v.reason}
                       for v in result.violations],
        "provider": result.provider,
        "guard": ("Every number in the narrative must appear in the computed "
                  "input. Responses containing an ungrounded figure are rejected "
                  "wholesale, not repaired."),
    }


@router.post("/ask")
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

    try:
        store = FS.load_store()
    except FS.ForecastUnavailable as exc:
        raise HTTPException(503, {"error": FS.ForecastUnavailable.code,
                                  "message": str(exc)}) from exc

    intent = FI.resolve(question, store.departments)
    if not intent.is_complete:
        try:
            p = _provider(provider)
            if p.available:
                intent = await FI.resolve_with_llm(question, store.departments, p)
        except (ValueError, LLMUnavailable):
            # The deterministic matcher already ran. A provider that is missing
            # or refusing costs a clarification prompt, not an answer.
            pass

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
            "available_departments": store.departments,
        }

    try:
        result = FS.forecast(intent.department, horizon=intent.horizon)
    except FS.ForecastRequestError as exc:
        return {"question": question, "route": "forecast", "succeeded": False,
                "routing": routing.to_dict(), "intent": intent.to_dict(),
                **exc.to_dict()}

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
