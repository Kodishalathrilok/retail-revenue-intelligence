"""File discovery and row counting.

Row counting matters more than it looks: it is the basis of the Phase 1
reconciliation check, and causal_data is too large to count any other way.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rrip.profile.discovery import count_rows, discover


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode())
    return path


def test_count_rows_excludes_header(tmp_path: Path) -> None:
    f = write(tmp_path / "a.csv", "col1,col2\n1,2\n3,4\n5,6\n")
    assert count_rows(f) == 3


def test_count_rows_without_trailing_newline(tmp_path: Path) -> None:
    """A final record with no trailing newline must still be counted."""
    f = write(tmp_path / "a.csv", "col1,col2\n1,2\n3,4")
    assert count_rows(f) == 2


def test_count_rows_header_only(tmp_path: Path) -> None:
    f = write(tmp_path / "a.csv", "col1,col2\n")
    assert count_rows(f) == 0


def test_count_rows_empty_file(tmp_path: Path) -> None:
    f = write(tmp_path / "a.csv", "")
    assert count_rows(f) == 0


def test_count_rows_spans_read_buffer(tmp_path: Path) -> None:
    """Guard the 8 MB chunk boundary logic."""
    rows = 200_000
    f = write(tmp_path / "big.csv", "a,b\n" + "".join(f"{i},{i}\n" for i in range(rows)))
    assert count_rows(f) == rows


def _minimal_dataset(root: Path, prefix: str = "") -> None:
    base = root / prefix if prefix else root
    write(base / "transaction_data.csv", "household_key,DAY\n1,1\n")
    write(base / "campaign_table.csv", "household_key,CAMPAIGN\n1,1\n")
    write(base / "campaign_desc.csv", "CAMPAIGN,START_DAY\n1,1\n")


def test_discover_finds_top_level_files(tmp_path: Path) -> None:
    _minimal_dataset(tmp_path)
    found = discover(tmp_path)
    assert set(found) >= {"transactions", "campaign_members", "campaign_desc"}


def test_discover_searches_nested_folders(tmp_path: Path) -> None:
    """Some mirrors nest everything one folder deep."""
    _minimal_dataset(tmp_path, "dunnhumby - The Complete Journey/csv")
    found = discover(tmp_path)
    assert found["transactions"].name == "transaction_data.csv"


def test_discover_is_case_insensitive(tmp_path: Path) -> None:
    write(tmp_path / "TRANSACTION_DATA.csv", "household_key\n1\n")
    write(tmp_path / "Campaign_Table.csv", "household_key\n1\n")
    write(tmp_path / "CAMPAIGN_DESC.csv", "CAMPAIGN\n1\n")
    found = discover(tmp_path)
    assert "transactions" in found and "campaign_members" in found


def test_discover_prefers_shallowest_on_duplicates(tmp_path: Path) -> None:
    _minimal_dataset(tmp_path)
    write(tmp_path / "nested" / "deep" / "transaction_data.csv", "household_key\n9\n")
    found = discover(tmp_path)
    assert found["transactions"].parent == tmp_path


def test_discover_raises_on_missing_required_file(tmp_path: Path) -> None:
    write(tmp_path / "transaction_data.csv", "household_key\n1\n")
    with pytest.raises(FileNotFoundError, match="campaign"):
        discover(tmp_path)


def test_discover_raises_on_missing_directory(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="does not exist"):
        discover(tmp_path / "nope")


def test_optional_files_absent_is_not_fatal(tmp_path: Path) -> None:
    """causal_data and demographics are optional for the probe to run."""
    _minimal_dataset(tmp_path)
    found = discover(tmp_path)
    assert "causal" not in found
    assert "households" not in found
