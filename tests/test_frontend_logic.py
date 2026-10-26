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
import re
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
    # Force the TAP reporter so the summary format is identical on every Node
    # version. The default reporter changed between Node releases -- a newer
    # runner prints "ℹ fail 0" where older ones printed "# fail 0" -- which
    # broke a bare "# fail 0" check in CI even though the tests passed. TAP is a
    # stable built-in reporter (Node 20+) and always prints "# pass N"/"# fail N".
    run = subprocess.run([node, "--test", "--test-reporter=tap", str(path)],
                         capture_output=True, text=True, timeout=120)
    diagnostic = run.stdout[-3000:] + run.stderr[-2000:]
    # Three independent guarantees, none of which tolerates a failed test:
    #   1. the process exited 0 -- node --test returns non-zero on any failure,
    assert run.returncode == 0, diagnostic
    #   2. at least one test actually ran (not a vacuous pass on zero tests),
    passed = re.search(r"^# pass (\d+)$", run.stdout, re.MULTILINE)
    assert passed and int(passed.group(1)) > 0, diagnostic
    #   3. and the failure count is exactly zero.
    assert re.search(r"^# fail 0$", run.stdout, re.MULTILINE), diagnostic
