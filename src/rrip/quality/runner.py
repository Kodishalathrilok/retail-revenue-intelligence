"""Run the data quality suite, write a report, exit non-zero on failure."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from rich.console import Console
from rich.table import Table

from rrip.config import PROJECT_ROOT
from rrip.db.connection import connect
from rrip.quality.checks import CHECKS, Check

console = Console()


def _recorded(conn, key: str) -> float | None:
    with conn.cursor() as cur:
        cur.execute("SELECT value FROM etl_data_quality WHERE check_name = %s", (key,))
        row = cur.fetchone()
    return float(row[0]) if row else None


def evaluate(conn, chk: Check) -> dict:
    with conn.cursor() as cur:
        cur.execute(chk.sql)  # type: ignore[arg-type]
        row = cur.fetchone()
    observed = float(row[0]) if row and row[0] is not None else 0.0

    expected: float | None = None
    if chk.rule == "zero":
        expected, passed = 0.0, observed == 0
    elif chk.rule == "max":
        expected = float(chk.threshold or 0)
        passed = observed <= expected
    elif chk.rule == "min":
        expected = float(chk.threshold or 0)
        passed = observed >= expected
    elif chk.rule == "recorded":
        expected = _recorded(conn, chk.recorded_key or "")
        if expected is None:
            return {"name": chk.name, "category": chk.category, "severity": "error",
                    "observed": observed, "expected": None, "passed": False,
                    "detail": f"no recorded value for '{chk.recorded_key}' in "
                              "etl_data_quality -- run the load so it is measured",
                    "rationale": chk.rationale}
        passed = observed == expected
    else:
        raise ValueError(f"unknown rule {chk.rule!r}")

    return {"name": chk.name, "category": chk.category, "severity": chk.severity,
            "observed": observed, "expected": expected, "passed": passed,
            "detail": "" if passed else f"expected {chk.rule} {expected:,.4g}, got {observed:,.4g}",
            "rationale": chk.rationale}


def run(category: str | None = None, report_dir: Path | None = None) -> tuple[int, int, Path]:
    """Returns (failures, warnings, report_path)."""
    selected = [c for c in CHECKS if not category or c.category == category]
    results: list[dict] = []

    with connect() as conn:
        for chk in selected:
            try:
                results.append(evaluate(conn, chk))
            except Exception as exc:
                results.append({
                    "name": chk.name, "category": chk.category, "severity": "error",
                    "observed": None, "expected": None, "passed": False,
                    "detail": f"check raised {type(exc).__name__}: {exc}",
                    "rationale": chk.rationale})

    failures = sum(1 for r in results if not r["passed"] and r["severity"] == "error")
    warnings = sum(1 for r in results if not r["passed"] and r["severity"] == "warn")

    t = Table(title="Data quality suite")
    for c in ("check", "category", "observed", "expected", "result"):
        t.add_column(c, justify="right" if c in ("observed", "expected") else "left")
    for r in results:
        if r["passed"]:
            verdict = "[green]pass[/green]"
        elif r["severity"] == "warn":
            verdict = "[yellow]warn[/yellow]"
        else:
            verdict = "[red]FAIL[/red]"
        t.add_row(r["name"], r["category"],
                  "—" if r["observed"] is None else f"{r['observed']:,.4g}",
                  "—" if r["expected"] is None else f"{r['expected']:,.4g}",
                  verdict)
    console.print(t)

    for r in results:
        if not r["passed"]:
            tag = "FAIL" if r["severity"] == "error" else "WARN"
            colour = "red" if r["severity"] == "error" else "yellow"
            console.print(f"[{colour}]{tag}[/{colour}] {r['name']}: {r['detail']}")
            if r["rationale"]:
                console.print(f"       [dim]{r['rationale']}[/dim]")

    # Report artifact per run.
    out_dir = report_dir or (PROJECT_ROOT / "reports")
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = out_dir / f"quality-{stamp}.json"
    path.write_text(json.dumps({
        "generated_at": datetime.now(UTC).isoformat(),
        "checks_run": len(results),
        "failures": failures,
        "warnings": warnings,
        "results": results,
    }, indent=2, default=str), encoding="utf-8")

    console.print(f"\n{len(results)} checks | "
                  f"[{'red' if failures else 'green'}]{failures} failures[/] | "
                  f"[yellow]{warnings} warnings[/yellow]")
    console.print(f"report: {path}")
    return failures, warnings, path
