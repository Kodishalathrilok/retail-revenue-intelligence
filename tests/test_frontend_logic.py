"""Run the frontend's pure-logic tests (node:test) as part of the Python suite.

frontend/lib/answer.mjs builds the Ask page's answer sentence, outcome kind,
chart eligibility and evidence from the API response; frontend/lib/overview.mjs
words the Overview's findings and holds the workflow steps; and
frontend/lib/metrics.mjs is the Explain content. They are plain JavaScript
precisely so Node's built-in test runner can exercise them with no build step
and no new tooling; running them from here puts them in CI's existing
lint-and-test job (GitHub's Ubuntu runners ship Node) without touching the
workflow.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1] / "frontend" / "lib"
TESTS = sorted(LIB.glob("*.test.mjs"))


def test_the_node_test_files_are_found() -> None:
    assert {p.name for p in TESTS} >= {"answer.test.mjs", "overview.test.mjs"}


@pytest.mark.parametrize("path", TESTS, ids=lambda p: p.name)
def test_frontend_logic_node_tests_pass(path: Path) -> None:
    node = shutil.which("node")
    if node is None:
        if os.getenv("CI"):
            pytest.fail("node is not on PATH in CI; the frontend logic tests would not run")
        pytest.skip("node not installed")
    run = subprocess.run([node, "--test", str(path)], capture_output=True, text=True,
                         timeout=120)
    assert run.returncode == 0, run.stdout[-3000:] + run.stderr[-2000:]
    assert "# fail 0" in run.stdout
