"""Tests for the NL->SQL validation gates.

Each bypass below passed every gate that existed before gate 3 was added. They
are pinned here because the failure they represent is silent: a validator that
stops rejecting something does not error, it just returns a Stage with
passed=True, and the query runs.

The false-positive tests matter as much. A gate that rejects legitimate
analytics is not safe, it is broken -- the model burns both retries and the user
gets "rejected after 2 attempts" for a question the schema can answer. So the
project's own analytical SQL is run through the gates as a corpus.
"""

from __future__ import annotations

import pytest

from rrip.ai.nl2sql import (
    ALLOWED_FUNCTIONS,
    strip_sql_noise,
    validate_functions,
    validate_keywords,
    validate_shape,
)


def gates(sql: str) -> list[tuple[str, bool, str]]:
    """Every synchronous gate, in order. Excludes explain/execute (need a DB)."""
    return [(s.stage, s.passed, s.detail)
            for s in (validate_shape(sql), validate_keywords(sql),
                      validate_functions(sql))]


def blocked(sql: str) -> bool:
    return any(not passed for _stage, passed, _detail in gates(sql))


def rejecting_gate(sql: str) -> str | None:
    for stage, passed, _detail in gates(sql):
        if not passed:
            return stage
    return None


# --- the bypasses -----------------------------------------------------------

# Each executes its text argument server-side. The argument is a string literal,
# which strip_sql_noise removes before the keyword scan -- so the payload is
# invisible to gate 2 by design, and EXPLAIN never runs a function body.
QUERY_EXECUTING_FUNCTIONS = [
    "SELECT query_to_xml('DELETE FROM pub_weekly_revenue', false, true, '')",
    "SELECT query_to_xml('DROP TABLE pub_headline', false, true, '')",
    "SELECT query_to_xmlschema('DELETE FROM fact_transactions', false, true, '')",
    "SELECT table_to_xml('fact_transactions', false, true, '')",
]


@pytest.mark.parametrize("sql", QUERY_EXECUTING_FUNCTIONS)
def test_blocks_functions_that_execute_their_argument(sql: str) -> None:
    assert blocked(sql), f"bypass not blocked: {sql}"
    assert rejecting_gate(sql) == "functions"


def test_payload_really_is_invisible_to_the_keyword_gate() -> None:
    """Documents WHY gate 3 exists rather than another denylist entry.

    If this ever starts failing because gate 2 catches it, the literal-stripping
    behaviour changed -- and that behaviour is what stops `WHERE name = 'drop
    table'` being a false reject. Check that before "fixing" this test.
    """
    sql = "SELECT query_to_xml('DELETE FROM pub_weekly_revenue', false, true, '')"
    assert "delete" not in strip_sql_noise(sql).lower()
    assert validate_keywords(sql).passed
    assert not validate_functions(sql).passed


def test_blocks_recursive_cte() -> None:
    sql = ("WITH RECURSIVE b(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM b) "
           "SELECT count(*) FROM b")
    assert rejecting_gate(sql) == "shape"


@pytest.mark.parametrize("sql", [
    "SELECT * FROM pg_shadow",
    "SELECT * FROM pg_stat_activity",
    "SELECT pg_read_binary_file('/etc/passwd')",
    "SELECT * FROM information_schema.tables",
    "SELECT pg_ls_waldir()",
])
def test_blocks_catalog_and_filesystem_access(sql: str) -> None:
    assert blocked(sql), f"catalog access not blocked: {sql}"


@pytest.mark.parametrize("sql", [
    "SELECT count(*) FROM generate_series(1, 100000000000)",
    "SELECT unnest(ARRAY[1, 2, 3])",
])
def test_blocks_unbounded_row_generation(sql: str) -> None:
    """The planner estimates these at a flat 1000 rows, so the cost gate is blind."""
    assert rejecting_gate(sql) == "functions"


def test_still_blocks_multi_statement() -> None:
    assert rejecting_gate("SELECT 1; DROP TABLE pub_headline") == "shape"


# --- false positives --------------------------------------------------------

LEGITIMATE = [
    "SELECT department, sum(revenue) FROM pub_weekly_revenue_by_dept GROUP BY 1",
    "SELECT round(avg(revenue), 2) AS avg_rev FROM pub_weekly_revenue",
    """SELECT week_no, revenue,
              rank() OVER (ORDER BY revenue DESC) AS r,
              lag(revenue) OVER (ORDER BY week_no) AS prev
         FROM pub_weekly_revenue LIMIT 10""",
    """WITH totals AS (SELECT department, sum(revenue) AS rev
                         FROM pub_weekly_revenue_by_dept GROUP BY 1)
       SELECT department, rev FROM totals ORDER BY rev DESC LIMIT 5""",
    # CTE with an explicit column list -- call-shaped, must not read as one.
    """WITH totals(dept, rev) AS (SELECT department, sum(revenue)
                                    FROM pub_weekly_revenue_by_dept GROUP BY 1)
       SELECT dept, rev FROM totals LIMIT 5""",
    # Derived-table alias with a column list -- also call-shaped.
    """SELECT t.a, t.b FROM (SELECT department, sum(revenue)
                               FROM pub_weekly_revenue_by_dept GROUP BY 1) t (a, b)""",
    "SELECT extract(YEAR FROM start_date) AS y FROM pub_weekly_revenue LIMIT 5",
    "SELECT date_trunc('month', start_date) AS m FROM pub_weekly_revenue LIMIT 5",
    "SELECT coalesce(revenue, 0)::numeric(12, 2) FROM pub_weekly_revenue LIMIT 5",
    "SELECT string_agg(DISTINCT department, ', ') FROM pub_weekly_revenue_by_dept",
    "SELECT count(*) FILTER (WHERE revenue > 100) FROM pub_weekly_revenue",
    "SELECT substring(department FROM 1 FOR 3) FROM pub_weekly_revenue_by_dept LIMIT 5",
    "SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY revenue) FROM pub_weekly_revenue",
    # A forbidden word inside a string literal is data, not a keyword.
    "SELECT department FROM pub_weekly_revenue_by_dept WHERE department = 'drop table'",
]


@pytest.mark.parametrize("sql", LEGITIMATE)
def test_allows_legitimate_analytics(sql: str) -> None:
    failures = [(stage, detail) for stage, passed, detail in gates(sql) if not passed]
    assert not failures, f"false positive on {sql!r}: {failures}"


def test_project_analytics_sql_passes_the_function_gate() -> None:
    """The shipped analytical queries are the realistic corpus for false positives.

    Only the function gate is asserted: these files are multi-statement and
    parameterised, so shape and keyword gates legitimately reject some of them.
    """
    from rrip.config import PROJECT_ROOT

    files = sorted((PROJECT_ROOT / "sql" / "analytics").glob("*.sql"))
    assert files, "no analytics SQL found -- the corpus moved"

    for path in files:
        stage = validate_functions(path.read_text(encoding="utf-8"))
        assert stage.passed, f"{path.name} rejected by the function gate: {stage.detail}"


def test_allowlist_excludes_the_query_executing_functions() -> None:
    """Pins the omissions, so a future 'add more functions' edit cannot undo them."""
    for name in ("query_to_xml", "query_to_xmlschema", "table_to_xml", "dblink",
                 "dblink_exec", "generate_series", "unnest", "lo_import",
                 "pg_read_file", "pg_sleep"):
        assert name not in ALLOWED_FUNCTIONS
