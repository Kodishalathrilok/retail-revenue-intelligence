"""The predictive benchmark: does the forecast layer earn its place, and behave?

Two things are measured, and they are different questions:

  ACCURACY   temporal test-set error, against every baseline, by segment, with
             interval coverage and stability over time. Read from the artifact
             the training run produced, because re-deriving it here would be a
             second implementation that could disagree with the one that
             actually ships.

  BEHAVIOUR  the cases in eval/datasets/forecast_v1.jsonl. Each is a question
             or a request with a mechanically checkable expectation: the
             routing verdict, the extracted parameters, the refusal code, or a
             property the returned payload must hold. No LLM judge, no
             subjective grading -- the same discipline the NL->SQL and
             narration benchmarks use.

WHY BEHAVIOUR CASES AT ALL

An accurate forecaster that answers "predict next week's units sold" with a
revenue figure is not a working system, and no accuracy metric would notice.
Most of the ways this component can fail a user are governance failures, so
most of the cases test governance.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from rrip.config import PROJECT_ROOT

DATASET = PROJECT_ROOT / "eval" / "datasets" / "forecast_v1.jsonl"
REPORT_DIR = PROJECT_ROOT / "reports" / "eval"


@dataclass
class Case:
    id: str
    category: str
    question: str
    expect: dict
    note: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> Case:
        return cls(id=d["id"], category=d["category"], question=d["question"],
                   expect=d["expect"], note=d.get("note", ""))


@dataclass
class CaseResult:
    case: Case
    passed: bool
    failures: list[str] = field(default_factory=list)
    observed: dict = field(default_factory=dict)
    duration_ms: float = 0.0

    def to_dict(self) -> dict:
        return {"id": self.case.id, "category": self.case.category,
                "question": self.case.question, "passed": self.passed,
                "failures": self.failures, "observed": self.observed,
                "duration_ms": round(self.duration_ms, 3),
                "note": self.case.note}


def load_cases(path: Path = DATASET) -> list[Case]:
    if not path.exists():
        raise FileNotFoundError(f"forecast benchmark dataset missing: {path}")
    cases = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("//"):
            cases.append(Case.from_dict(json.loads(line)))
    ids = [c.id for c in cases]
    if len(set(ids)) != len(ids):
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        raise ValueError(f"duplicate case ids in {path}: {dupes}")
    return cases


def _check(expect: dict, observed: dict) -> list[str]:
    """Mechanical grading. Every assertion is an exact or numeric comparison."""
    failures: list[str] = []

    for key, want in expect.items():
        if key == "prediction_within":
            lo, hi = want
            got = observed.get("prediction")
            if got is None or not (lo <= got <= hi):
                failures.append(
                    f"prediction {got} outside the expected range [{lo}, {hi}]")
            continue

        if key == "interval_contains_prediction":
            p = observed.get("prediction")
            lo = observed.get("lower_bound")
            hi = observed.get("upper_bound")
            if p is None or lo is None or hi is None or not (lo <= p <= hi):
                failures.append(
                    f"prediction {p} not inside its own interval [{lo}, {hi}]")
            continue

        if key == "explanation_sums_to_prediction":
            exp = observed.get("explanation") or {}
            contrib = exp.get("contributors") or []
            if not exp.get("additive"):
                failures.append("explanation is not marked additive")
            elif contrib:
                total = sum(c.get("value_usd", 0) * c.get("weight", 0)
                            for c in contrib)
                if abs(total - (observed.get("prediction") or 0)) > 0.02:
                    failures.append(
                        f"contributors sum to {total:.2f} but the prediction "
                        f"is {observed.get('prediction')}")
            continue

        if key == "confidence_reasons_nonempty":
            if not observed.get("confidence_reasons"):
                failures.append("expected at least one confidence reason")
            continue

        if key == "caveats_nonempty":
            if not observed.get("caveats"):
                failures.append("expected at least one caveat")
            continue

        got = observed.get(key)
        if got != want:
            failures.append(f"{key}: expected {want!r}, got {got!r}")

    return failures


def run_case(case: Case) -> CaseResult:
    from rrip.ai import forecast_intent as FI
    from rrip.ai.router import classify
    from rrip.forecast import service as FS

    t0 = time.perf_counter()
    observed: dict = {}

    routing = classify(case.question)
    observed["verdict"] = routing.verdict
    observed["rule"] = routing.rule

    if routing.verdict == "FORECAST":
        store = FS.load_store()
        intent = FI.resolve(case.question, store.departments)
        observed["department"] = intent.department
        observed["horizon"] = intent.horizon
        observed["unknown_department"] = intent.unknown_department

        if intent.is_complete:
            try:
                payload = FS.forecast(intent.department, horizon=intent.horizon)
                observed.update({
                    "error": None,
                    "prediction": payload["prediction"],
                    "lower_bound": payload["lower_bound"],
                    "upper_bound": payload["upper_bound"],
                    "confidence": payload["confidence"],
                    "confidence_reasons": payload["confidence_reasons"],
                    "caveats": payload["caveats"],
                    "explanation": payload["explanation"],
                    "forecast_week": payload["forecast_week"],
                    "model_type": payload["model_type"],
                })
            except FS.ForecastRequestError as exc:
                observed["error"] = exc.code
                observed["message"] = exc.message

    failures = _check(case.expect, observed)
    return CaseResult(case=case, passed=not failures, failures=failures,
                      observed=observed,
                      duration_ms=(time.perf_counter() - t0) * 1000)


def latency_probe(n: int = 200) -> dict:
    """Inference latency, measured rather than claimed.

    Serving is a dictionary lookup over precomputed rows, so this should be
    microseconds. It is measured anyway: if it ever stops being a lookup, this
    is the number that says so.
    """
    from rrip.forecast import service as FS

    store = FS.load_store()
    dept = store.departments[0]
    FS.forecast(dept)  # warm

    samples = []
    for _ in range(n):
        t0 = time.perf_counter()
        FS.forecast(dept)
        samples.append((time.perf_counter() - t0) * 1000)
    samples.sort()
    return {"n": n, "mean_ms": round(sum(samples) / n, 4),
            "p50_ms": round(samples[n // 2], 4),
            "p95_ms": round(samples[int(n * 0.95)], 4),
            "max_ms": round(samples[-1], 4)}


def run(path: Path = DATASET) -> dict:
    """Run the behaviour cases and assemble them with the measured accuracy."""
    from rrip.forecast import service as FS

    cases = load_cases(path)
    results = [run_case(c) for c in cases]

    by_category: dict[str, dict] = {}
    for r in results:
        b = by_category.setdefault(r.case.category, {"total": 0, "passed": 0})
        b["total"] += 1
        b["passed"] += int(r.passed)
    for b in by_category.values():
        b["pass_rate"] = round(100.0 * b["passed"] / b["total"], 1)

    summary = FS.summary()
    passed = sum(r.passed for r in results)

    return {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "dataset": str(path.relative_to(PROJECT_ROOT)),
        "model_version": summary["model_version"],
        "behaviour": {
            "total": len(results),
            "passed": passed,
            "pass_rate": round(100.0 * passed / len(results), 1) if results else 0.0,
            "by_category": by_category,
            "cases": [r.to_dict() for r in results],
        },
        "accuracy": {
            "deployed": summary["model_type"],
            "deployed_kind": summary["deployed_kind"],
            "deployment_rationale": summary["deployment_rationale"],
            "test_weeks": summary["windows"]["test"],
            "test": summary["metrics"]["test"],
            "test_baselines": summary["metrics"]["test_baselines"],
            "diebold_mariano_pairwise": summary["metrics"]["diebold_mariano_pairwise"],
            "macro_wape_test": summary["metrics"]["macro_wape_test"],
        },
        "intervals": summary["conformal"],
        "leakage_audit_passed": summary["leakage_audit_passed"],
        "latency": latency_probe(),
    }


def write(report: dict) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    (REPORT_DIR / f"forecast-{stamp}.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8")
    latest = REPORT_DIR / "forecast-latest.json"
    latest.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return latest


def print_summary(report: dict) -> None:
    from rich.console import Console
    from rich.table import Table

    con = Console()
    b = report["behaviour"]
    con.print(f"\n[bold]Forecast behaviour[/bold]  {b['passed']}/{b['total']} "
              f"({b['pass_rate']}%)   model {report['model_version']}")

    t = Table("category", "passed", "total", "rate")
    for name, v in sorted(b["by_category"].items()):
        t.add_row(name, str(v["passed"]), str(v["total"]), f"{v['pass_rate']}%")
    con.print(t)

    for r in b["cases"]:
        if not r["passed"]:
            con.print(f"  [red]FAIL[/red] {r['id']}: {'; '.join(r['failures'])}")

    acc = report["accuracy"]
    con.print(f"\n[bold]Accuracy on the untouched test weeks "
              f"{acc['test_weeks']}[/bold]")
    at = Table("predictor", "MAE", "RMSE", "WAPE", "sMAPE")
    rows = sorted(acc["test_baselines"].items(), key=lambda kv: kv[1]["wape"])
    for name, s in rows:
        label = ("challenger model" if name == "__challenger_model__" else name)
        at.add_row(label, f"{s['mae']:.1f}", f"{s['rmse']:.1f}",
                   f"{s['wape']:.2f}%", f"{s['smape']:.2f}%")
    con.print(at)
    con.print(f"  deployed: [bold]{acc['deployed']}[/bold] ({acc['deployed_kind']})")
    con.print(f"  {acc['deployment_rationale']}")

    con.print("\n[bold]Prediction interval coverage[/bold]")
    for level, cov in report["intervals"].get("test_coverage", {}).items():
        con.print(f"  nominal {float(level):.0%} -> measured "
                  f"{cov['coverage']:.1%} (n={cov['n']})")

    lat = report["latency"]
    con.print(f"\n[bold]Inference latency[/bold] p50 {lat['p50_ms']:.3f} ms, "
              f"p95 {lat['p95_ms']:.3f} ms")
    con.print(f"Leakage audit passed: {report['leakage_audit_passed']}")
