"""The measured case for keeping regex gates instead of an AST parser.

THE QUESTION

Gate 3 extracts function calls with a regex and exempts a hand-maintained list
of SQL keywords that can legally precede "(" -- without it, `WHERE x IN (...)`
reads as a call to a function named "in". That list is a maintenance risk: a
keyword nobody thought of becomes a false rejection, and a false rejection is
the system refusing a question it can answer.

The obvious upgrade is to parse SQL properly (sqlglot) and validate the tree.
The spec for this work says to decide that by measurement rather than because an
AST sounds more advanced, so this test is the measurement.

WHAT IT MEASURES

Every real single-statement query this project has: the benchmark's reference
queries, which are hand-written and known correct, plus every distinct query the
model generated that passed grading in a recorded eval run. If the regex
approach had a keyword-coverage problem, it would show up here as a legitimate
query being rejected.

At the time of writing: 81 queries, 0 false positives.

THE DECISION

Keep the regex gates, for now, on these grounds:

  * 0 measured false positives on the queries that actually occur.
  * The gates are NOT the security boundary -- the read-only role is, and it is
    separately verified by tests/test_readonly_role.py. A parser would harden
    defence in depth, not the boundary.
  * sqlglot is not in the dependency set. The core set is deliberately 30 MB
    against a 250 MB serverless limit (see pyproject.toml), and the gates run on
    the deployed path.

This is a decision with an expiry condition rather than a conclusion. If this
test ever fails, the keyword list has hit the limit that was predicted for it,
and that is the point to introduce a parser -- with this corpus as the
regression suite proving the replacement is no worse.
"""

from __future__ import annotations

import glob
import json

import pytest

from rrip.ai.nl2sql import validate_functions, validate_keywords, validate_shape
from rrip.config import PROJECT_ROOT
from rrip.eval.cases import CASES

TEXT_GATES = (validate_shape, validate_keywords, validate_functions)


def _corpus() -> list[tuple[str, str]]:
    """Hand-written reference queries plus model queries that graded correct."""
    out: list[tuple[str, str]] = [
        (f"reference:{c.id}", c.reference_sql) for c in CASES if c.reference_sql]

    seen: set[str] = set()
    for path in glob.glob(str(PROJECT_ROOT / "reports" / "eval" / "*.json")):
        try:
            report = json.loads(open(path, encoding="utf-8").read())
        except (OSError, ValueError):
            continue
        for case in report.get("cases", []):
            sql = case.get("sql")
            if sql and case.get("passed") and sql not in seen:
                seen.add(sql)
                out.append((f"generated:{case['id']}", sql))
    return out


CORPUS = _corpus()


def test_the_corpus_is_not_empty():
    """A zero false-positive rate over zero queries is not a measurement."""
    assert len(CORPUS) >= 30, (
        f"only {len(CORPUS)} queries collected; run `rrip eval` so this test "
        "measures something")


@pytest.mark.parametrize("name,sql", CORPUS, ids=[n for n, _ in CORPUS])
def test_legitimate_sql_is_not_rejected(name, sql):
    for gate in TEXT_GATES:
        stage = gate(sql)
        assert stage.passed, (
            f"{name} rejected by gate {stage.stage!r}: {stage.detail}\n"
            f"This is a FALSE POSITIVE -- legitimate SQL refused. If it is a "
            f"keyword-coverage gap, that is the trigger to replace the regex "
            f"gates with a real parser.\nSQL: {sql}")
