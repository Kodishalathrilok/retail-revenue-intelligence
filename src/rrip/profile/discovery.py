"""Locate and load the dunnhumby CSVs.

The "Complete Journey" dataset ships under several conventions depending on the
mirror: files may sit at the top level or inside a nested folder, and column
names mix `household_key` (lower) with `SALES_VALUE` (upper). Everything here
normalises to lowercase snake_case so the rest of the codebase sees one shape.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

# Logical name -> candidate file stems seen across mirrors.
EXPECTED_FILES: dict[str, tuple[str, ...]] = {
    "transactions": ("transaction_data", "transactions"),
    "households": ("hh_demographic", "hh_demographics", "demographic"),
    "products": ("product", "products"),
    "campaign_members": ("campaign_table", "campaign_memberships"),
    "campaign_desc": ("campaign_desc", "campaign_description"),
    "coupons": ("coupon",),
    "coupon_redemptions": ("coupon_redempt", "coupon_redemption"),
    "causal": ("causal_data", "causal"),
}

# Files without which Phase 0 cannot answer its questions.
REQUIRED = ("transactions", "campaign_members", "campaign_desc")


def discover(raw_dir: Path) -> dict[str, Path]:
    """Map logical names to actual files, searching recursively, case-insensitively."""
    if not raw_dir.exists():
        raise FileNotFoundError(f"Raw data directory does not exist: {raw_dir}")

    # Index every csv under raw_dir by lowercase stem.
    on_disk: dict[str, list[Path]] = {}
    for p in raw_dir.rglob("*.csv"):
        on_disk.setdefault(p.stem.lower(), []).append(p)

    found: dict[str, Path] = {}
    for logical, stems in EXPECTED_FILES.items():
        for stem in stems:
            if stem in on_disk:
                # Prefer the shallowest path if a mirror duplicates files.
                found[logical] = min(on_disk[stem], key=lambda p: len(p.parts))
                break

    missing_required = [n for n in REQUIRED if n not in found]
    if missing_required:
        raise FileNotFoundError(
            f"Missing required file(s): {', '.join(missing_required)}. "
            f"Searched under {raw_dir}. Found: {sorted(on_disk) or 'nothing'}"
        )
    return found


def count_rows(path: Path) -> int:
    """Count data rows (excluding header) without loading the file into memory.

    Needed because causal_data.csv is far too large to count via pandas on 8 GB.
    """
    newlines = 0
    last_byte = b"\n"
    with path.open("rb") as fh:
        while chunk := fh.read(8 * 1024 * 1024):
            newlines += chunk.count(b"\n")
            last_byte = chunk[-1:]
    # A file not ending in a newline still has a final record.
    if last_byte not in (b"\n", b""):
        newlines += 1
    return max(newlines - 1, 0)  # drop the header


def normalise_columns(df: pd.DataFrame) -> pd.DataFrame:
    df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]
    return df


def read_csv(path: Path, **kwargs) -> pd.DataFrame:
    return normalise_columns(pd.read_csv(path, **kwargs))
