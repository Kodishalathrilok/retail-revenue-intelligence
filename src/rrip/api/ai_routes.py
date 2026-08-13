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
