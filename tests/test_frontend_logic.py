"""Run the frontend's pure-logic tests (node:test) as part of the Python suite.

frontend/lib/answer.mjs builds the Ask page's answer sentence, outcome kind,
chart eligibility and evidence from the API response. It is plain JavaScript
precisely so Node's built-in test runner can exercise it with no build step and
no new tooling; running it from here puts it in CI's existing lint-and-test job
(GitHub's Ubuntu runners ship Node) without touching the workflow.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parents[1] / "frontend" / "lib" / "answer.test.mjs"


def test_answer_logic_node_tests_pass() -> None:
    node = shutil.which("node")
    if node is None:
        if os.getenv("CI"):
            pytest.fail("node is not on PATH in CI; the frontend logic tests would not run")
        pytest.skip("node not installed")
    run = subprocess.run([node, "--test", str(TESTS)], capture_output=True, text=True,
                         timeout=120)
    assert run.returncode == 0, run.stdout[-3000:] + run.stderr[-2000:]
    assert "# fail 0" in run.stdout
