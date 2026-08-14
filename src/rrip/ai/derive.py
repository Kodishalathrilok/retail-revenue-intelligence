"""Deterministic derivation of the quantities a narrative wants to mention.

THE PROBLEM THIS SOLVES

The grounding guard rejects any number in a narrative that is not present in the
input. That is the right rule and it stays. But it made a legitimate sentence
impossible: given revenue 1000 and previous 800, "revenue grew 25%" contains a
number that is nowhere in the data, so the guard rejected a true statement. The
system prompt worked around it by telling the model to describe relationships in
words -- which is a worse explanation, produced by a constraint rather than
chosen.

The fix is not to loosen the guard. It is to stop asking the model for arithmetic
at all: compute the change, the percentage and the share HERE, put them in the
payload as named fields, and let the model do the only job it is trusted with,
which is turning named quantities into a sentence. The guard then passes on a
correct narrative for the right reason -- the number really is in the data.

WHAT IS DELIBERATELY NOT DONE HERE

No statistics. No trend detection, no significance, no outlier scoring. Those
belong to SQL and statsmodels, which already do them and are already tested.
This module does arithmetic on a result set that has already been computed:
differences, percentages, shares, totals.

EVERY DIVISION IS GUARDED

A zero denominator produces None and a stated reason, never inf, never NaN, and
never a silently omitted field. A missing percentage that says why it is missing
is a fact the narrator can report; a NaN is a number that will be printed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, DivisionByZero, InvalidOperation

# How many decimal places derived figures carry. Two is what currency and
# percentages are read at; the guard's allowed set includes coarser roundings of
# whatever appears here, so this sets the finest granularity a narrative may use.
PLACES = 2

# Column names that mean "this is the time axis". Used to decide which end of a
# result set is 'first' and which is 'latest' when describing a change.
TIME_HINTS = ("week", "week_no", "date", "date_key", "day", "day_number", "month",
              "month_number", "month_start", "quarter", "year", "year_number",
              "period", "tenure_month", "start_date")


@dataclass
class Derivation:
    """The derived payload, plus what could not be derived and why."""

    data: dict
    notes: list[str]


def _is_number(v: object) -> bool:
    return isinstance(v, (int, float, Decimal)) and not isinstance(v, bool)


def _num(v: object) -> Decimal | None:
    if not _is_number(v):
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None


def _round(v: Decimal | None) -> float | None:
    if v is None:
        return None
    try:
        return float(round(v, PLACES))
    except (InvalidOperation, OverflowError):
        return None


def _pct_change(current: Decimal, previous: Decimal) -> tuple[float | None, str | None]:
    """Percent change, or None with the reason it is undefined.

    A zero baseline is the case that matters. "Revenue grew from 0 to 500" has no
    percentage -- growth from nothing is not a percentage, it is a start -- and
    returning inf here would put 'inf%' in front of a reader.
    """
    if previous == 0:
        return None, ("percent change is undefined from a zero baseline; report "
                      "the absolute change instead")
    try:
        return _round((current - previous) / previous * 100), None
    except (DivisionByZero, InvalidOperation):
        return None, "percent change could not be computed"


def _numeric_columns(rows: list[dict], columns: list[str]) -> list[str]:
    """Columns where every non-null value is a number.

    Requiring ALL non-null values to be numeric matters: a column that is mostly
    numbers with one string in it is not a measure, and averaging it would
    produce a figure describing a coincidence.
    """
    out = []
    for c in columns:
        seen = [r.get(c) for r in rows if r.get(c) is not None]
        if seen and all(_is_number(v) for v in seen):
            out.append(c)
    return out


def _label_column(rows: list[dict], columns: list[str], numeric: list[str]) -> str | None:
    """The column that names each row, if there is one."""
    for c in columns:
        if c in numeric:
            continue
        seen = [r.get(c) for r in rows if r.get(c) is not None]
        if seen and all(isinstance(v, (str, date, datetime)) for v in seen):
            return c
    return None


def _time_column(columns: list[str]) -> str | None:
    for c in columns:
        if c.lower() in TIME_HINTS:
            return c
    for c in columns:
        if any(h in c.lower() for h in ("week", "date", "month", "year", "day")):
            return c
    return None


def derive(rows: list[dict], columns: list[str], question: str = "") -> Derivation:
    """Compute every quantity a narrator might reasonably want to state.

    The output is what gets sent to the model. It contains the original rows, so
    nothing is hidden from it, plus named derived fields so it never has to
    calculate one.
    """
    notes: list[str] = []
    data: dict = {
        "question": question,
        "row_count": len(rows),
        "columns": list(columns),
    }

    if not rows:
        data["rows"] = []
        notes.append("the query returned no rows; there is nothing to describe")
        data["notes"] = notes
        return Derivation(data, notes)

    # Rows are capped because the payload has a size limit upstream, and a
    # truncated list silently changes what 'the largest' means.
    cap = 200
    data["rows"] = rows[:cap]
    if len(rows) > cap:
        data["rows_truncated_to"] = cap
        notes.append(f"only the first {cap} of {len(rows)} rows are shown; "
                     "totals below are computed over ALL rows")

    numeric = _numeric_columns(rows, columns)
    label = _label_column(rows, columns, numeric)
    tcol = _time_column(columns)

    nulls = {c: sum(1 for r in rows if r.get(c) is None) for c in columns}
    if any(nulls.values()):
        data["null_counts"] = {c: n for c, n in nulls.items() if n}
        notes.append("some values are NULL; they are excluded from totals and "
                     "averages rather than treated as zero")

    # --- per-measure totals -------------------------------------------------
    totals: dict[str, dict] = {}
    for c in numeric:
        vals = [v for v in (_num(r.get(c)) for r in rows) if v is not None]
        if not vals:
            continue
        total = sum(vals, Decimal(0))
        entry = {
            "total": _round(total),
            "minimum": _round(min(vals)),
            "maximum": _round(max(vals)),
            "mean": _round(total / len(vals)),
            "n_values": len(vals),
        }
        if label:
            hi = max(rows, key=lambda r: _num(r.get(c)) or Decimal("-Infinity"))
            lo = min(rows, key=lambda r: _num(r.get(c)) or Decimal("Infinity"))
            entry["highest_row"] = {label: hi.get(label), c: _round(_num(hi.get(c)))}
            entry["lowest_row"] = {label: lo.get(label), c: _round(_num(lo.get(c)))}
        totals[c] = entry
    if totals:
        data["totals"] = totals

    # --- change across the time axis ---------------------------------------
    if tcol and len(rows) >= 2:
        ordered = sorted(
            [r for r in rows if r.get(tcol) is not None],
            key=lambda r: (_num(r.get(tcol)) if _is_number(r.get(tcol))
                           else str(r.get(tcol))))
        if len(ordered) >= 2:
            changes: dict[str, dict] = {}
            for c in numeric:
                if c == tcol:
                    continue
                first, last = _num(ordered[0].get(c)), _num(ordered[-1].get(c))
                if first is None or last is None:
                    continue
                pct, why = _pct_change(last, first)
                changes[c] = {
                    "from_period": ordered[0].get(tcol),
                    "to_period": ordered[-1].get(tcol),
                    "from_value": _round(first),
                    "to_value": _round(last),
                    "absolute_change": _round(last - first),
                    "percent_change": pct,
                    "direction": ("increase" if last > first
                                  else "decrease" if last < first else "no change"),
                }
                if why:
                    changes[c]["percent_change_unavailable"] = why
                    notes.append(f"{c}: {why}")
            if changes:
                data["change_over_period"] = changes
                data["period_column"] = tcol

    # --- share of total -----------------------------------------------------
    #
    # Only when rows are named and there is exactly one measure. With two
    # measures "share" is ambiguous, and guessing which one the reader meant is
    # the class of decision this system does not make.
    if label and len(numeric) == 1 and len(rows) <= 50:
        c = numeric[0]
        vals = [(r.get(label), _num(r.get(c))) for r in rows]
        vals = [(k, v) for k, v in vals if v is not None]
        total = sum((v for _, v in vals), Decimal(0))
        if total > 0:
            data["share_of_total"] = {
                "measure": c,
                "total": _round(total),
                "shares": [{label: k, c: _round(v),
                            "pct_of_total": _round(v / total * 100)}
                           for k, v in vals],
            }
        elif vals:
            notes.append(f"shares of {c} are undefined because the total is "
                         f"{_round(total)}")

    if notes:
        data["notes"] = notes
    return Derivation(data, notes)
