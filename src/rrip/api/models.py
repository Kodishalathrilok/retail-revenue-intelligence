"""Pydantic models for API requests and responses."""

from __future__ import annotations

import base64
import json
from datetime import date
from typing import Any

from pydantic import BaseModel, Field, field_validator

# The panel runs day 1 (2015-01-07, a Wednesday) to day 711 (2016-12-17).
PANEL_START = date(2015, 1, 7)
PANEL_END = date(2016, 12, 17)


class DateWindow(BaseModel):
    date_from: date = Field(default=PANEL_START)
    date_to: date = Field(default=PANEL_END)

    @field_validator("date_to")
    @classmethod
    def _ordered(cls, v: date, info) -> date:
        lo = info.data.get("date_from")
        if lo and v < lo:
            raise ValueError("date_to must not precede date_from")
        return v


class Cursor(BaseModel):
    """Opaque keyset cursor.

    Keyset rather than OFFSET: OFFSET re-scans and discards every skipped row,
    so deep pages get progressively slower on tables this size, and a concurrent
    insert shifts every subsequent page.
    """

    last_values: list[Any]

    def encode(self) -> str:
        return base64.urlsafe_b64encode(
            json.dumps(self.last_values, default=str).encode()).decode()

    @classmethod
    def decode(cls, raw: str | None) -> Cursor | None:
        if not raw:
            return None
        try:
            return cls(last_values=json.loads(base64.urlsafe_b64decode(raw)))
        except Exception as exc:
            raise ValueError("malformed cursor") from exc


class Page[T](BaseModel):
    items: list[T]
    next_cursor: str | None = None
    has_more: bool = False


class Meta(BaseModel):
    """Context that must travel with any number this API returns.

    These are not decoration. The weekday convention and the panel structure
    both constrain what the figures mean, and a consumer that renders them
    without the caveat will make claims the data does not support.
    """

    calendar_basis: str = (
        "DAY 1 anchored to Wednesday 2015-01-07 so that day 6 is a Monday, "
        "matching the source WEEK_NO rule (day + 8) / 7. Elapsed intervals, "
        "month boundaries and YoY comparisons are real; weekday LABELS are a "
        "modelling convention and carry no meaning.")
    panel_basis: str = (
        "Household panel, not an acquisition funnel: 99.8% of households make "
        "their first purchase within 180 days of a 711-day window. Retention is "
        "reported on relative tenure, not calendar cohorts.")
    revenue_basis: str = (
        "sales_value is the NET amount charged. gross_value = sales_value - "
        "retail_disc, where retail_disc is stored negative.")


class ExecutiveOverview(BaseModel):
    total_revenue: float
    total_baskets: int
    total_households: int
    avg_basket_value: float
    total_units: int
    weeks_covered: int
    # Set only on the published tier, where the date filter cannot be applied.
    window_basis: str | None = None
    meta: Meta = Meta()


class NLQueryRequest(BaseModel):
    question: str = Field(min_length=3, max_length=500)
    max_attempts: int = Field(default=2, ge=1, le=3)


class ValidationStage(BaseModel):
    stage: str
    passed: bool
    detail: str = ""
    duration_ms: float | None = None


class NLQueryAttempt(BaseModel):
    attempt: int
    sql: str
    stages: list[ValidationStage]
    rejected_reason: str | None = None
    error_fed_back: str | None = None


class NLQueryResponse(BaseModel):
    question: str
    succeeded: bool
    sql: str | None = None
    columns: list[str] = []
    rows: list[dict] = []
    row_count: int = 0
    attempts: list[NLQueryAttempt] = []
    total_duration_ms: float = 0.0
    provider: str | None = None
    failure_reason: str | None = None
