"""The public site must not show developer hints or swallow failures silently.

The deployed pages used to append "is the API running? `rrip serve`" to every
error a visitor saw, and several fetches ended in `.catch(() => {})`, so a
section could vanish without a word. Comments that describe those old
behaviours are fine; code that reintroduces them is not.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

FRONTEND = Path(__file__).resolve().parents[1] / "frontend"
SOURCES = sorted(p for d in ("app", "components", "lib")
                 for p in (FRONTEND / d).rglob("*.ts*"))


def _code_only(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)       # block + JSX comments
    return re.sub(r"(?m)^\s*//.*$", "", text)                # whole-line comments


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.relative_to(FRONTEND).as_posix())
def test_no_developer_hints_or_silent_catches(path: Path) -> None:
    code = _code_only(path.read_text(encoding="utf-8"))
    assert "rrip serve" not in code
    assert "is the API running" not in code
    assert not re.search(r"\.catch\(\s*\(\s*\)\s*=>\s*\{\s*\}\s*\)", code), "silent catch"


def test_the_scan_covers_the_pages() -> None:
    names = {p.relative_to(FRONTEND).as_posix() for p in SOURCES}
    assert {"app/page.tsx", "app/query/page.tsx", "app/forecast/page.tsx",
            "app/causal/page.tsx", "lib/api.ts"} <= names
