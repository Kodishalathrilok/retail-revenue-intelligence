"""Every relative link in the README and docs must resolve to a TRACKED file.

Existence alone is not enough, and that is the whole reason this test exists:
the README cited reports/eval/*.md as its evidence while reports/ was
gitignored, so every link worked on the author's machine and was a 404 on
GitHub. Checking against `git ls-files` catches a file that is present locally
but will never be published.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOCS = [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]
LINK = re.compile(r"\]\(([^)\s]+)\)")


def _tracked() -> set[str]:
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True,
                         text=True, check=True).stdout
    return set(out.splitlines())


def _relative_links(doc: Path) -> list[str]:
    links = []
    for target in LINK.findall(doc.read_text(encoding="utf-8")):
        target = target.split("#", 1)[0]
        if not target or re.match(r"^[a-z][a-z0-9+.-]*:", target):
            continue                        # in-page anchor, or http:/mailto:
        links.append(target)
    return links


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: p.name)
def test_relative_links_resolve_to_tracked_files(doc: Path) -> None:
    tracked = _tracked()
    broken = []
    for target in _relative_links(doc):
        path = (doc.parent / target).resolve()
        rel = path.relative_to(ROOT).as_posix()
        # A directory link resolves if anything under it is tracked.
        ok = rel in tracked or any(t.startswith(rel.rstrip("/") + "/") for t in tracked)
        if not ok:
            broken.append(target)
    assert not broken, f"{doc.name}: untracked or missing link targets {broken}"
