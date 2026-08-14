"""rrip command line entrypoint."""

from __future__ import annotations

from pathlib import Path

import typer

app = typer.Typer(help="Retail Revenue Intelligence Platform", no_args_is_help=True)


@app.callback()
def main() -> None:
    """Force subcommand mode so `rrip profile` works and later commands can be added."""


@app.command()
def profile(
    raw_dir: Path = typer.Option(
        None, help="Directory holding the dunnhumby CSVs. Defaults to .env setting."
    ),
    out: Path = typer.Option(
        Path("docs/dataset-profile.md"), help="Where to write the profile report."
    ),
) -> None:
    """Phase 0: measure the raw dataset before any schema work."""
    from rrip.config import PROJECT_ROOT, settings
    from rrip.profile.phase0 import run

    target = raw_dir or settings.raw_dir
    out_path = out if out.is_absolute() else PROJECT_ROOT / out
    camp = run(target, out_path)

    # Non-zero exit if the dataset cannot support the causal module, so this is
    # usable as a gate rather than something you have to read carefully.
    if not bool(camp["viable"].any()):
        raise typer.Exit(code=1)


@app.command()
def load(
    raw_dir: Path = typer.Option(None, help="dunnhumby CSV directory. Defaults to .env."),
    reset: bool = typer.Option(False, help="Drop and rebuild the schema first."),
) -> None:
    """Phase 1: load the star schema. Resumable -- rerun to continue after a failure."""
    from rrip.config import settings
    from rrip.ingest.loader import run

    run(raw_dir or settings.raw_dir, reset=reset)


@app.command()
def reconcile() -> None:
    """Compare loaded row counts against raw file line counts."""
    from rich.console import Console

    from rrip.ingest.loader import reconcile as _rec

    console = Console()
    ok = True
    for table, source, expected, loaded, matched in _rec():
        mark = "[green]OK  [/green]" if matched else "[red]FAIL[/red]"
        dedup = f"  (source {source:,} less {source - expected:,} deduplicated)" \
            if expected != source else ""
        console.print(f"  {mark} {table:24s} expected={expected:>12,}  "
                      f"loaded={loaded:>12,}{dedup}")
        ok = ok and matched
    if not ok:
        raise typer.Exit(code=1)


@app.command()
def bench(
    variant: str = typer.Option("before", help="'before' or 'after'."),
    label: str = typer.Option("", help="Free-text note recorded with the run."),
    reps: int = typer.Option(5, help="Warm repetitions per query."),
    query: str = typer.Option("", help="Comma-separated query name prefixes, e.g. 'q1,q3'."),
) -> None:
    """Phase 2: measure the benchmark queries and record results."""
    from rrip.bench.runner import run, summary

    only = [q.strip() for q in query.split(",") if q.strip()] or None
    run_id = run(variant, label, reps=reps, only=only)
    summary(run_id)


@app.command("bench-summary")
def bench_summary(run_id: int = typer.Option(None, help="Defaults to latest run.")) -> None:
    """Show recorded benchmark results."""
    from rrip.bench.runner import summary
    summary(run_id)


@app.command()
def quality(
    category: str = typer.Option("", help="Run only one category, e.g. 'time_model'."),
) -> None:
    """Phase 4: run the data quality suite. Exits non-zero on any failure."""
    from rrip.quality.runner import run as run_quality

    failures, _warnings, _path = run_quality(category or None)
    if failures:
        raise typer.Exit(code=1)


@app.command("quality-list")
def quality_list() -> None:
    """List the data quality checks and what each one guards."""
    from rich.console import Console

    from rrip.quality.checks import CHECKS

    console = Console()
    for cat in sorted({c.category for c in CHECKS}):
        console.print(f"\n[bold]{cat}[/bold]")
        for c in [c for c in CHECKS if c.category == cat]:
            console.print(f"  {c.name}  [dim]({c.rule}"
                          f"{'' if c.threshold is None else f' {c.threshold}'})[/dim]")
            if c.rationale:
                console.print(f"      [dim]{c.rationale}[/dim]")


@app.command()
def serve(
    host: str = typer.Option("", help="Bind address. Defaults to RRIP_API_HOST."),
    port: int = typer.Option(0, help="Port. Defaults to RRIP_API_PORT (8010)."),
    reload: bool = typer.Option(False, help="Auto-reload on code change."),
) -> None:
    """Phase 5: run the API."""
    from rrip.api.run import serve as _serve
    from rrip.config import settings

    _serve(host=host or settings.api_host,
           port=port or settings.api_port, reload=reload)


@app.command("eval")
def eval_nl2sql(
    tier: str = typer.Option("", help="'local' or 'published'. Defaults to RRIP_TIER."),
    category: str = typer.Option("", help="Run one category, e.g. 'adversarial'."),
    limit: int = typer.Option(0, help="Run only the first N cases."),
    attempts: int = typer.Option(2, help="Max NL->SQL attempts per question."),
    provider: str = typer.Option("", help="Override the configured LLM provider."),
    router: bool = typer.Option(
        True, "--router/--no-router",
        help="Answerability routing. --no-router is the baseline to compare against."),
) -> None:
    """Phase 6: measure NL->SQL accuracy, safety, retry recovery and latency.

    Results go to reports/ as JSON. Model responses are cached, so re-running
    the same cases costs no quota and reproduces the same figures.
    """
    from rrip.ai.provider import get_provider
    from rrip.config import settings
    from rrip.eval.runner import print_summary, run_sync

    report = run_sync(
        get_provider(provider or None),
        tier or ("published" if settings.is_published else "local"),
        category=category or None, limit=limit or None, max_attempts=attempts,
        use_router=router)
    print_summary(report)

    # Non-zero exit if anything harmful executed, so this is usable as a CI
    # gate rather than something someone has to read carefully.
    if report["summary"]["adversarial"]["harm_executed"]:
        raise typer.Exit(code=1)


@app.command("eval-narration")
def eval_narration(
    limit: int = typer.Option(0, help="Run only the first N cases."),
    category: str = typer.Option("", help="Run one category, e.g. 'rounding'."),
    provider: str = typer.Option("", help="Override the configured LLM provider."),
) -> None:
    """Measure narration faithfulness: raw rows vs derived metrics, live model.

    Both paths run every case against the same model. Fails loudly rather than
    substituting a provider if no key is configured -- a faithfulness number
    obtained from a different model than the one deployed is not a measurement
    of this system.
    """
    from rrip.ai.provider import get_provider
    from rrip.eval.narration_bench import print_summary, run_sync

    report = run_sync(get_provider(provider or None),
                      limit=limit or None, category=category or None)
    print_summary(report)


@app.command("causal-validate")
def causal_validate(
    sims: int = typer.Option(100, help="Simulations for the coverage study."),
) -> None:
    """Recover known effects from synthetic panels; report bias and CI coverage."""
    import json
    from dataclasses import asdict

    from rich.console import Console

    from rrip.ai.causal_validate import coverage_study, run_suite
    from rrip.config import PROJECT_ROOT

    console = Console()
    suite = [asdict(r) for r in run_suite()]
    for r in suite:
        ok = "[green]OK[/green]" if r["covered"] else "[red]CI MISSES TRUTH[/red]"
        console.print(f"  {r['scenario']:36} true={r['true_effect']:5.1f} "
                      f"est={r['estimated']:7.3f} {ok}")

    cov = coverage_study(n_sims=sims)
    console.print(f"\n[bold]{cov['n_simulations']} simulations[/bold]  "
                  f"bias={cov['bias']:+.4f}  "
                  f"95% CI coverage={cov['ci_coverage_pct']}% "
                  f"(nominal {cov['nominal_coverage_pct']}%)")

    out = PROJECT_ROOT / "reports" / "eval"
    out.mkdir(parents=True, exist_ok=True)
    (out / "causal-validation.json").write_text(
        json.dumps({"recovery": suite, "coverage": cov}, indent=2, default=str),
        encoding="utf-8")


@app.command("forecast-train")
def forecast_train(
    refresh: bool = typer.Option(False, "--refresh",
                                 help="Re-query the panel instead of using the "
                                      "cached extract."),
) -> None:
    """Train, evaluate and store the one-week-ahead department revenue forecaster.

    Runs the leakage audit BEFORE fitting anything and aborts on failure, then
    selects on rolling-origin validation, unlocks the test weeks exactly once,
    and applies the promotion rule that decides whether the fitted model or the
    baseline is deployed.
    """
    from rich.console import Console

    from rrip.db.connection import connect
    from rrip.forecast import train as FT

    console = Console()
    with connect() as conn:
        result = FT.run(conn, use_cache=not refresh)

    d = result["decision"]
    c = result["comparison"]
    console.print(f"\n[bold]Deployed:[/bold] {d['deployed_predictor']} "
                  f"({d['deployed_kind']})")
    console.print(f"  {d['rationale']}")
    console.print(f"\n  test WAPE  {c['scores']['wape']:.2f}%   "
                  f"baseline {c['baseline_name']} {c['baseline_scores']['wape']:.2f}%")
    console.print(f"  leakage audit passed: {result['audit']['passed']}")
    for level, cov in result["interval_coverage"].items():
        console.print(f"  interval nominal {float(level):.0%} -> measured "
                      f"{cov['coverage']:.1%}")


@app.command("forecast-eval")
def forecast_eval() -> None:
    """Run the predictive benchmark and write reports/eval/forecast-latest.json.

    Exits non-zero if any behaviour case fails or if the stored leakage audit
    did not pass, so it is usable as a CI gate.
    """
    import sys

    from rrip.eval.forecast_bench import print_summary, run, write

    report = run()
    print_summary(report)
    path = write(report)
    print(f"\nwrote {path}")

    if report["behaviour"]["passed"] != report["behaviour"]["total"]:
        sys.exit(1)
    if not report["leakage_audit_passed"]:
        sys.exit(1)


@app.command("forecast-report")
def forecast_report() -> None:
    """Assemble reports/eval/forecast_release.md from the measured artifacts."""
    from rich.console import Console

    from rrip.forecast.report import write

    Console().print(f"wrote {write()}")


@app.command("eval-report")
def eval_report() -> None:
    """Assemble reports/eval/latest.md from whatever has actually been measured."""
    from rich.console import Console

    from rrip.eval.report import write

    Console().print(f"wrote {write()}")


@app.command("verify-role")
def verify_role(
    dsn: str = typer.Option("", help="DSN for the read-only role. "
                                     "Defaults to RRIP_RO_DSN / RRIP_RO_PASSWORD."),
) -> None:
    """Connect as rrip_ro and attempt every forbidden operation.

    Exits non-zero on anything other than VERIFIED, so it is usable as a
    deployment gate. A role that is not deployed reports NOT_DEPLOYED and fails
    rather than being skipped.
    """
    from rrip.eval.verify_role import run as _run

    report = _run(dsn or None)
    if report["status"] != "VERIFIED":
        raise typer.Exit(code=1)


@app.command("eval-cases")
def eval_cases() -> None:
    """List the benchmark questions and how each is graded."""
    from rich.console import Console

    from rrip.eval.cases import CASES

    console = Console()
    for cat in sorted({c.category for c in CASES}):
        console.print(f"\n[bold]{cat}[/bold]")
        for c in [c for c in CASES if c.category == cat]:
            console.print(f"  {c.id:8} [dim]({c.expectation}, {c.tier})[/dim]  "
                          f"{c.question}")
            if c.note:
                console.print(f"           [dim]{c.note}[/dim]")


@app.command()
def publish(
    local_only: bool = typer.Option(
        False, help="Build and measure the aggregate tier without pushing."),
    dsn: str = typer.Option("", help="Target DSN. Defaults to RRIP_PUBLISH_DSN."),
) -> None:
    """Phase 8: build the pub_* aggregate tier and push it to hosted Postgres."""
    from rrip.config import settings
    from rrip.publish.publisher import run as _run

    _run(dsn or settings.publish_dsn or None, local_only=local_only)


if __name__ == "__main__":
    app()
