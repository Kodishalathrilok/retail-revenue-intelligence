"""A second adjudicator, written from adjudication_rubric.md alone.

INDEPENDENCE, PRECISELY

This module imports NOTHING from rrip. It reads adjudication_worksheet.csv and
nothing else. It does not share the original classifier's number regex, its
allowed-set construction, its sentence splitter, its negation markers, its
direction word lists, or its notion of a "trap". Every rule here was written
from the rubric prose.

Two specific divergences are deliberate, so that agreement means something:

  * Truth is derived from the worksheet's `trusted_metrics` column, parsed here.
    The original classifier builds its truth set by calling rrip.ai.derive on the
    case definition. If derive() is wrong, the original inherits the error and
    this judge does not.
  * Direction and ranking are inferred from the metrics themselves, not from an
    `expected_direction` / `expected_top` field written by the benchmark author.
    So this judge can disagree with the ANSWER KEY, not just with the code.

WHAT IT CANNOT BE INDEPENDENT OF

The rubric's author had already seen how the original classifier scored seven
outputs. See the limits section of adjudication_rubric.md. Judge-vs-original
agreement is therefore a lower bound on shared assumptions, not a replication.
"""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent

PASS = "PASS"
UNSUPPORTED_NUMBER = "UNSUPPORTED_NUMBER"
NUMERICALLY_WRONG = "NUMERICALLY_WRONG"
SEMANTICALLY_WRONG = "SEMANTICALLY_WRONG"
VALID_REFUSAL = "VALID_REFUSAL"
UNSURE = "UNSURE"

# Own number grammar: optional sign, optional currency, digits with separators,
# optional decimals, optional trailing percent.
NUMBER = re.compile(r"[-+]?[$£€]?\d[\d,]*(?:\.\d+)?%?")

# Own inability vocabulary, phrased from the rubric's list.
DISCLAIMERS = (
    "not possible", "cannot", "can not", "can't", "does not", "do not",
    "is not", "are not", "no ", "not ", "without", "lacks", "lack ", "unable",
    "insufficient", "impossible", "unavailable", "undefined", "absent",
    "n't", "limited to", "only contains", "not available", "cannot be",
)

RISE = ("rose", "rise", "risen", "grew", "grow", "growth", "increase",
        "increased", "increasing", "higher", "gained", "climbed", "up by",
        "upward", "improvement", "improved")
FALL = ("fell", "fall", "fallen", "declined", "decline", "decrease",
        "decreased", "decreasing", "lower", "dropped", "drop", "shrank",
        "reduction", "reduced", "downward", "down by", "contracted")
FLAT = ("unchanged", "no change", "flat", "identical", "same", "stable",
        "steady", "held", "constant")

SUPERLATIVE = ("highest", "largest", "biggest", "greatest", "strongest",
               "leading", "top", "peak", "best", "most")

# Concepts the metrics in this project can never carry, per rubric category 3.
CONCEPTS = {
    "profit": ("profit", "profitable", "profitability"),
    "margin": ("margin", "margins"),
    "roi": ("roi", "return on investment"),
    "cost": ("cost of goods", "cogs", "cost data", "costs incurred"),
    "forecast": ("forecast", "predict", "projection", "will be", "expected to reach"),
    "annualisation": ("annualised", "annualized", "per year", "annual rate"),
    "causal": ("because of", "caused by", "driven by", "due to the", "led to"),
    "proof": ("proves", "proven", "no effect", "conclusively", "definitively"),
}

# Words that mark a number as bound to a supplied quantity, used to separate
# rubric category 2 (wrong value for a named metric) from category 1.
METRIC_WORDS = ("percent", "%", "change", "growth", "decline", "average",
                "mean", "total", "share", "rate", "ratio", "difference")


@dataclass
class Judgement:
    row_id: str
    label: str
    category: str
    note: str


def numbers_in(text: str) -> list[tuple[float, str]]:
    """Every numeric literal with the 30 characters that follow it."""
    out = []
    for m in NUMBER.finditer(text):
        raw = m.group(0).strip("$£€").rstrip("%").replace(",", "")
        if raw in ("", "-", "+", "."):
            continue
        try:
            out.append((float(raw), text[m.start():m.end() + 30]))
        except ValueError:
            continue
    return out


def supported_values(metrics_json: str, question: str) -> set[float]:
    """Everything the narration is entitled to say, built from the payload."""
    allowed: set[float] = set()

    def add(v: float) -> None:
        allowed.add(v)
        for places in (0, 1, 2, 3):
            allowed.add(round(v, places))
        if float(v).is_integer():
            allowed.add(float(int(v)))
        if v < 0:                      # rubric: magnitude when sign is in words
            add_positive = -v
            allowed.add(add_positive)
            for places in (0, 1, 2, 3):
                allowed.add(round(add_positive, places))

    def walk(node) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                for n, _ in numbers_in(str(k)):
                    add(n)
                walk(v)
        elif isinstance(node, (list, tuple)):
            for v in node:
                walk(v)
        elif isinstance(node, bool) or node is None:
            return
        elif isinstance(node, (int, float)):
            add(float(node))
        elif isinstance(node, str):
            for n, _ in numbers_in(node):
                add(n)

    try:
        walk(json.loads(metrics_json))
    except json.JSONDecodeError:
        for n, _ in numbers_in(metrics_json):
            add(n)

    # Rubric: numbers echoed from the question are supported.
    for n, _ in numbers_in(question):
        add(n)
    return allowed


def is_structural(value: float, context: str) -> bool:
    """Small counting words used to organise prose, per rubric category 1."""
    if not float(value).is_integer() or not 0 <= value <= 10:
        return False
    tail = context[len(str(int(value))):].lstrip()
    return not tail.startswith("%")


def claims(text: str) -> list[str]:
    """One claim per line -- the worksheet is built that way, so no splitting."""
    return [ln.strip() for ln in text.splitlines() if ln.strip()]


def disclaims(line: str) -> bool:
    return any(d in line.lower() for d in DISCLAIMERS)


def metrics_rows(metrics_json: str) -> list[dict]:
    try:
        obj = json.loads(metrics_json)
    except json.JSONDecodeError:
        return []
    rows = obj.get("rows") if isinstance(obj, dict) else None
    return rows if isinstance(rows, list) else []


def _num(v) -> float | None:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.replace(",", ""))
        except ValueError:
            return None
    return None


def infer_direction(metrics_json: str) -> str | None:
    """Read the movement out of the rows, without consulting the answer key."""
    rows = metrics_rows(metrics_json)
    if len(rows) < 2:
        return None
    keys = list(rows[0].keys())
    numeric = [k for k in keys
               if all(_num(r.get(k)) is not None for r in rows)]
    if not numeric:
        return None
    time_like = [k for k in numeric
                 if any(t in k.lower() for t in ("week", "day", "month", "period",
                                                 "year", "tenure"))]
    measures = [k for k in numeric if k not in time_like]
    if not measures:
        return None
    first, last = _num(rows[0][measures[0]]), _num(rows[-1][measures[0]])
    if first is None or last is None:
        return None
    if last > first:
        return "increase"
    if last < first:
        return "decrease"
    return "no_change"


def infer_top_label(metrics_json: str) -> tuple[str, float] | None:
    """Which named row is the largest, inferred from the rows themselves."""
    rows = metrics_rows(metrics_json)
    if len(rows) < 2:
        return None
    keys = list(rows[0].keys())
    labels = [k for k in keys if all(isinstance(r.get(k), str) for r in rows)]
    numeric = [k for k in keys if all(_num(r.get(k)) is not None for r in rows)]
    numeric = [k for k in numeric
               if not any(t in k.lower() for t in ("week", "day", "month",
                                                   "period", "year"))]
    if not labels or not numeric:
        return None
    lab, meas = labels[0], numeric[0]
    best = max(rows, key=lambda r: _num(r[meas]) or float("-inf"))
    return str(best[lab]), float(_num(best[meas]) or 0.0)


def judge(row: dict) -> Judgement:
    text = row["narration"]
    metrics, question = row["trusted_metrics"], row["question"]
    allowed = supported_values(metrics, question)
    lines = claims(text)

    # --- category 1 and 2: numbers ----------------------------------------
    bad: list[tuple[float, str, bool]] = []
    for line in lines:
        for value, ctx in numbers_in(line):
            if value in allowed or is_structural(value, ctx):
                continue
            bound = any(w in line.lower() for w in METRIC_WORDS)
            bad.append((value, line, bound))
    if bad:
        bound_any = any(b for _v, _l, b in bad)
        values = sorted({v for v, _l, _b in bad})
        return Judgement(
            row["row_id"],
            NUMERICALLY_WRONG if bound_any else UNSUPPORTED_NUMBER,
            "bound_to_metric" if bound_any else "free_floating",
            f"unsupported value(s) {values}")

    # --- category 3: relationships ----------------------------------------
    live = [ln for ln in lines if not disclaims(ln)]
    live_text = " ".join(live).lower()

    expected = infer_direction(metrics)
    if expected:
        says_up = any(w in live_text for w in RISE)
        says_down = any(w in live_text for w in FALL)
        says_flat = any(w in live_text for w in FLAT)
        if expected == "increase" and says_down and not says_up:
            return Judgement(row["row_id"], SEMANTICALLY_WRONG, "direction",
                             "describes a fall where the metrics rise")
        if expected == "decrease" and says_up and not says_down:
            return Judgement(row["row_id"], SEMANTICALLY_WRONG, "direction",
                             "describes a rise where the metrics fall")
        if expected == "no_change" and (says_up or says_down) and not says_flat:
            return Judgement(row["row_id"], SEMANTICALLY_WRONG, "direction",
                             "describes movement between identical values")

    top = infer_top_label(metrics)
    if top:
        winner = top[0].lower()
        others = {str(r[k]).lower()
                  for r in metrics_rows(metrics) for k in r
                  if isinstance(r[k], str) and str(r[k]).lower() != winner}
        for line in live:
            low = line.lower()
            if not any(s in low for s in SUPERLATIVE) or winner in low:
                continue
            for other in others:
                if other and re.search(rf"\b{re.escape(other)}\b", low):
                    return Judgement(
                        row["row_id"], SEMANTICALLY_WRONG, "ranking",
                        f"superlative attached to {other!r}; largest is {winner!r}")

    for name, terms in CONCEPTS.items():
        for line in live:
            if any(t in line.lower() for t in terms):
                return Judgement(row["row_id"], SEMANTICALLY_WRONG, f"concept:{name}",
                                 f"asserts {name} without supporting metrics")

    # --- category 4: valid refusal ----------------------------------------
    undefined_marker = ("null" in metrics.lower()
                        or "_unavailable" in metrics.lower()
                        or "undefined" in metrics.lower())
    if undefined_marker and any(disclaims(ln) for ln in lines):
        return Judgement(row["row_id"], VALID_REFUSAL, "declined_undefined",
                         "declined a quantity the metrics mark unavailable")

    return Judgement(row["row_id"], PASS, "", "")


def main() -> int:
    src = HERE / "adjudication_worksheet.csv"
    with src.open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))

    out = [judge(r) for r in rows]
    dest = HERE / "adjudication_judge.csv"
    with dest.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["row_id", "label", "category", "note"])
        w.writeheader()
        w.writerows([j.__dict__ for j in out])

    from collections import Counter
    print(f"judged {len(out)} rows -> {dest.name}")
    for label, n in Counter(j.label for j in out).most_common():
        print(f"  {label:20} {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
