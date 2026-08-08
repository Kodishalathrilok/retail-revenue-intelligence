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


if __name__ == "__main__":
    app()
