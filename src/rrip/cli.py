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


if __name__ == "__main__":
    app()
