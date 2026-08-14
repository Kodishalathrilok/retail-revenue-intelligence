"""6a -- Validated natural language to SQL.

The model proposes SQL. It never executes anything, and it never sees data
before the SQL has passed every gate below. Each gate is recorded so the
reject-and-retry behaviour is observable rather than inferred from a final
answer.

Gates, in order:
  0. route      -- answerability, BEFORE any model call (rrip.ai.router)
  1. shape      -- exactly one statement, it must be a SELECT, no recursion
  2. keywords   -- no DDL/DML, no multi-statement, no dangerous functions
  3. functions  -- every called function is on an allowlist; no catalog access
  4. explain    -- EXPLAIN (no ANALYZE) and reject above a cost ceiling
  5. execute    -- hard statement timeout, row cap
  6. result     -- validate the returned shape

A rejection at any gate is fed back to the model as an error message, up to a
configured attempt limit. Gate 0 is different: it rejects the QUESTION rather
than the SQL, so there is nothing to feed back and no model call is made. It was
added because the first benchmark run answered three of four questions this
schema holds no data for -- see rrip.ai.router.

THESE GATES ARE NOT THE SECURITY BOUNDARY

They are a program reasoning about SQL text, and every such program has a
bypass. This one had a real one: `SELECT query_to_xml('DELETE FROM t', ...)`
executes its argument, and the argument is a string literal -- which gate 2
strips before scanning, precisely so a keyword inside a quoted string cannot
cause a false reject. Gate 4 did not catch it either, because EXPLAIN without
ANALYZE never executes a function body. Gate 3 exists because a denylist could
not close that class: `query_to_xml` was simply not on it, and neither were the
functions nobody had thought of yet.

The boundary is the database role. sql/ddl/60_readonly_role.sql creates one that
holds SELECT and nothing else. Run it before exposing this endpoint. Gates are
what turn a rejection into a useful error message for the model to retry
against; the role is what makes a missed rejection survivable.

Measured, because the earlier version of this note overstated it: the DELETE
form of that bypass is refused by Postgres for everyone including a superuser
(query_to_xml is STABLE, SQLSTATE 0A000), so the role is not what stops it. The
SELECT form does execute, as the calling role -- which is where the role earns
its place, by bounding what the executed text can reach. See
src/rrip/eval/verify_role.py and reports/eval/readonly-role.json.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from rrip.ai.provider import LLMProvider
from rrip.ai.router import classify
from rrip.api.db import acquire

MAX_COST = 5_000_000.0
STATEMENT_TIMEOUT_MS = 15_000
MAX_ROWS = 5_000

# Rejected outright. Checked against the SQL with comments and string literals
# stripped, so a keyword inside a quoted string cannot trigger a false reject
# and a comment cannot hide one.
FORBIDDEN = (
    "insert", "update", "delete", "drop", "truncate", "alter", "create",
    "grant", "revoke", "vacuum", "analyze", "copy", "call", "do",
    "pg_read_file", "pg_write", "pg_ls_dir", "lo_import", "lo_export",
    "dblink", "pg_sleep", "set_config", "pg_terminate", "pg_cancel",
)

# Gate 3 is an ALLOWLIST, because the denylist above cannot be completed. It
# only has to cover what analytics on this star schema actually needs; anything
# absent is a rejection the model can retry against, not a silent failure.
#
# Deliberately ABSENT, each for a reason:
#   query_to_xml, query_to_xmlschema, ... -- execute their text argument
#   dblink, dblink_exec                   -- open outbound connections
#   generate_series, unnest               -- unbounded row generation; the
#                                            planner estimates a flat 1000 rows
#                                            regardless of arguments, so gate 4
#                                            cannot price them. dim_date and
#                                            dim_week already cover every date
#                                            series this schema needs.
#   lo_*, pg_*                            -- large objects, catalogs, files
ALLOWED_FUNCTIONS = frozenset({
    # aggregates
    "count", "sum", "avg", "min", "max", "stddev", "stddev_samp", "stddev_pop",
    "variance", "var_samp", "var_pop", "corr", "covar_pop", "covar_samp",
    "percentile_cont", "percentile_disc", "mode", "string_agg", "array_agg",
    "bool_and", "bool_or", "every",
    # window
    "row_number", "rank", "dense_rank", "percent_rank", "cume_dist", "ntile",
    "lag", "lead", "first_value", "last_value", "nth_value",
    # math
    "abs", "ceil", "ceiling", "floor", "round", "trunc", "sign", "sqrt",
    "power", "exp", "ln", "log", "mod", "div", "greatest", "least",
    "width_bucket",
    # string
    "lower", "upper", "initcap", "length", "char_length", "character_length",
    "substring", "substr", "trim", "btrim", "ltrim", "rtrim", "lpad", "rpad",
    "replace", "split_part", "position", "strpos", "concat", "concat_ws",
    "to_char", "left", "right", "reverse", "starts_with",
    # date and time
    "age", "date_trunc", "date_part", "extract", "to_date", "to_timestamp",
    "make_date", "make_interval", "justify_days", "now", "current_date",
    "current_timestamp",
    # conditional and null handling
    "coalesce", "nullif",
    # casts written in function form, and type names carrying a precision
    "cast", "numeric", "decimal", "integer", "int", "bigint", "smallint",
    "real", "float", "double", "text", "varchar", "char", "boolean", "date",
    "timestamp", "timestamptz", "time", "interval",
})

# Words that can legally sit immediately before "(" without being a call.
# Without these, `WHERE x IN (...)` reads as a call to a function named "in".
NON_CALL_KEYWORDS = frozenset({
    "select", "from", "where", "and", "or", "not", "in", "exists", "any",
    "all", "some", "values", "on", "using", "as", "by", "over", "partition",
    "order", "group", "having", "when", "then", "else", "case", "end",
    "union", "intersect", "except", "with", "recursive", "distinct", "filter",
    "within", "join", "inner", "left", "right", "full", "outer", "cross",
    "lateral", "natural", "limit", "offset", "fetch", "between", "is", "null",
    "true", "false", "asc", "desc", "nulls", "first", "last", "rows", "range",
    "groups", "preceding", "following", "unbounded", "current", "row",
    "returning", "into", "array", "at", "zone", "collate", "like", "ilike",
    "similar", "escape", "for", "of", "if",
})

CALL_RE = re.compile(r"([a-z_][a-z0-9_$]*)\s*\(", re.IGNORECASE)

# `WITH name AS (`, `WITH name(cols) AS (`, and the same after a comma. These
# names are call-shaped when a column list follows, so they are collected and
# exempted rather than reported as unknown functions.
CTE_RE = re.compile(
    r"(?:\bwith\b|,)\s*([a-z_][a-z0-9_$]*)\s*(?:\([^)]*\))?\s+as\s*"
    r"(?:(?:not\s+)?materialized\s*)?\(",
    re.IGNORECASE)

# Anything reaching the catalogs or the server filesystem. One rule rather than
# an enumeration, because the enumeration is what failed: pg_shadow, pg_stat_*,
# pg_read_binary_file and pg_ls_waldir were all absent from FORBIDDEN.
CATALOG_RE = re.compile(r"\b(pg_[a-z0-9_]*|information_schema)\b", re.IGNORECASE)

SCHEMA_PROMPT = """\
You write PostgreSQL SELECT queries against a retail star schema.

TABLES
  fact_transactions(date_key date, basket_id bigint, product_id int,
      household_key int, store_id int, day_number smallint, week_no smallint,
      trans_time smallint, quantity int, sales_value numeric,
      retail_disc numeric, coupon_disc numeric, coupon_match_disc numeric,
      gross_value numeric GENERATED, is_weighted_item bool GENERATED,
      is_return bool GENERATED)
    -- 2,595,732 rows. Partitioned monthly on date_key.

  fact_causal(week_no smallint, product_id int, store_id int,
      display text, mailer text)
    -- 36,771,279 rows. Partitioned on week_no. display/mailer are single
    -- character CATEGORICAL CODES ('0'-'9','A'-'Z'), never numbers.

  dim_date(date_key date PK, day_number smallint, week_no smallint,
      day_of_week smallint, day_name text, is_weekend bool, month_number smallint,
      month_name text, month_start date, quarter_number smallint,
      year_number smallint, is_partial_week bool)   -- 711 rows

  dim_week(week_no smallint PK, start_day, end_day smallint,
      start_date, end_date date, day_count smallint, is_partial_week bool)  -- 102 rows

  dim_product(product_id int PK, manufacturer_id int, department text,
      brand text, commodity_desc text, sub_commodity_desc text,
      curr_size_of_product text)  -- 92,353 rows

  dim_household(household_key int PK, age_desc, marital_status_code, income_desc,
      homeowner_desc, hh_comp_desc, household_size_desc, kid_category_desc text,
      has_demographics bool)  -- 2,500 rows; only 801 have demographics

  dim_store(store_id int PK, has_promo_coverage bool)  -- 582 rows; 115 have promo data
  dim_campaign(campaign_id int PK, campaign_type text, start_day, end_day smallint,
      start_date, end_date date)  -- 30 rows
  dim_coupon(coupon_upc bigint PK)  -- 1,135 rows
  bridge_campaign_household(campaign_id, household_key)
  bridge_coupon_product(coupon_upc, product_id)
  bridge_coupon_campaign(coupon_upc, campaign_id)

RULES
  - Return ONE SELECT statement. No semicolon-separated statements. No DDL or DML.
  - Always add a LIMIT unless the query aggregates to few rows.
  - sales_value is the NET amount charged. gross_value = sales_value - retail_disc
    (retail_disc is stored NEGATIVE).
  - Returns are quantity <= 0, never negative sales_value.
  - quantity > 1000 means weighted goods recorded in GRAMS -- exclude these from
    any "units sold" measure.
  - When filtering fact_causal by time, filter on week_no with literal integers.
    Filtering via a join to dim_week prevents partition pruning.
  - Weekday labels are a modelling convention, not source data. Do not answer
    day-of-week questions.

Return ONLY the SQL. No markdown fences, no commentary."""


PUBLISHED_SCHEMA_PROMPT = """\
You write PostgreSQL SELECT queries against a PRE-AGGREGATED retail dataset.

IMPORTANT: there are no transaction-level or promotion-level fact tables here.
Every table below already holds a computed result. You cannot answer questions
about individual baskets, individual households' purchases, or product-level
promotion exposure. If a question needs that granularity, return a SELECT that
explains the limitation, for example:
    SELECT 'This deployment exposes aggregate tables only; transaction-level
    data is not available.' AS message;

TABLES
  pub_weekly_revenue(week_no smallint, start_date date, is_partial_week bool,
      revenue numeric, baskets int, households int,
      cumulative_revenue numeric, rolling_7wk_avg numeric)   -- 102 rows
  pub_weekly_revenue_by_dept(week_no, department text, revenue numeric,
      baskets int, households int)
  pub_rfm_segments(segment text, households int, pct_of_panel numeric,
      avg_recency_days numeric, avg_baskets numeric, avg_lifetime_value numeric,
      segment_revenue numeric, pct_of_revenue numeric)       -- 7 rows
  pub_retention_tenure(segment text, tenure_month int, cohort_size int,
      active_households int, retention_pct numeric)          -- 72 rows
  pub_pareto_products(revenue_rank int, product_id int, commodity_desc text,
      department text, revenue numeric, cumulative_pct numeric)  -- top 5,000
  pub_commodity_affinity(commodity_a text, commodity_b text, pair_baskets int,
      support numeric, lift numeric)
  pub_reorder_by_department(department text, products int,
      avg_household_reorder_rate numeric, avg_line_reorder_rate numeric)
  pub_promo_exposure(week_no smallint, department text, promo_rows bigint,
      on_display bigint, in_mailer bigint, display_pct numeric)
  pub_anomalies(week_no, start_date, revenue, z_score, mean_revenue, is_partial_week)
  pub_headline(metric text, value text, context text)
  pub_causal_results(campaign_id int, treated_n, control_n int,
      contaminated_pct, naive_difference, did_estimate, did_pvalue numeric, ...)
  pub_dim_product(product_id int, department, brand, commodity_desc,
      sub_commodity_desc text)                               -- 92,353 rows
  pub_dim_date, pub_dim_week, pub_dim_store, pub_dim_household, pub_dim_campaign
  pub_manifest(table_name text, rows bigint, bytes bigint, published_at timestamptz)

RULES
  - Return ONE SELECT statement. No semicolon-separated statements. No DDL or DML.
  - Always add a LIMIT unless the query aggregates to few rows.
  - Weekday labels are a modelling convention, not source data. Do not answer
    day-of-week questions.
  - Weeks 1 and 102 are partial (5 and 6 days) and not comparable to full weeks.

Return ONLY the SQL. No markdown fences, no commentary."""


def schema_prompt() -> str:
    """Pick the schema description matching the configured tier.

    The published tier genuinely has a different schema -- not a subset of the
    same tables, a different set. Handing the model the local schema there would
    produce SQL that references tables which do not exist, and every failure
    would look like a model error rather than a configuration one.
    """
    from rrip.config import settings
    from rrip.semantic import render_prompt

    base = PUBLISHED_SCHEMA_PROMPT if settings.is_published else SCHEMA_PROMPT
    # Metric definitions are appended from rrip.semantic rather than written
    # inline, so "average basket value is per basket_id" exists in exactly one
    # place and reaches the model, the router, the evaluator and the docs from
    # that one edit. It used to exist in four, which is three chances to drift.
    return f"{base}\n\n{render_prompt(published=settings.is_published)}"


@dataclass
class Stage:
    stage: str
    passed: bool
    detail: str = ""
    duration_ms: float = 0.0


@dataclass
class Attempt:
    attempt: int
    sql: str
    stages: list[Stage] = field(default_factory=list)
    rejected_reason: str | None = None
    error_fed_back: str | None = None


@dataclass
class NL2SQLResult:
    question: str
    succeeded: bool
    sql: str | None = None
    columns: list[str] = field(default_factory=list)
    rows: list[dict] = field(default_factory=list)
    attempts: list[Attempt] = field(default_factory=list)
    total_duration_ms: float = 0.0
    provider: str | None = None
    failure_reason: str | None = None
    # Set when the answerability router decided the question before any model
    # call. None means the router was disabled or waved the question through.
    routing: dict | None = None


def strip_sql_noise(sql: str) -> str:
    """Remove comments and string literals so keyword checks see only code.

    Without this, `WHERE name = 'drop table'` would be rejected and
    `SELECT 1 -- ; DROP TABLE x` could hide a statement separator.
    """
    out, i, n = [], 0, len(sql)
    while i < n:
        if sql.startswith("--", i):
            j = sql.find("\n", i)
            i = n if j == -1 else j
        elif sql.startswith("/*", i):
            j = sql.find("*/", i + 2)
            i = n if j == -1 else j + 2
        elif sql[i] == "'":
            j = i + 1
            while j < n:
                if sql[j] == "'":
                    if j + 1 < n and sql[j + 1] == "'":
                        j += 2
                        continue
                    break
                j += 1
            out.append("''")
            i = j + 1
        else:
            out.append(sql[i])
            i += 1
    return "".join(out)


def clean_model_sql(raw: str) -> str:
    """Strip markdown fences a model may add despite instructions."""
    text = raw.strip()
    text = re.sub(r"^```(?:sql)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def validate_shape(sql: str) -> Stage:
    t0 = time.perf_counter()
    bare = strip_sql_noise(sql).strip()
    ms = (time.perf_counter() - t0) * 1000

    if not bare:
        return Stage("shape", False, "empty statement", ms)

    # One statement only. A trailing semicolon is fine; a second statement is not.
    body = bare.rstrip().rstrip(";")
    if ";" in body:
        return Stage("shape", False,
                     "multiple statements detected -- exactly one SELECT is allowed", ms)

    first = re.match(r"\s*(with|select)\b", body, re.IGNORECASE)
    if not first:
        return Stage("shape", False,
                     f"statement must start with SELECT or WITH, got "
                     f"{body.split()[0][:24] if body.split() else '(empty)'!r}", ms)

    # A CTE chain must still terminate in SELECT, and must not contain a
    # data-modifying CTE, which Postgres permits inside WITH.
    if first.group(1).lower() == "with" and not re.search(
            r"\)\s*select\b", body, re.IGNORECASE | re.DOTALL):
        return Stage("shape", False, "WITH clause does not terminate in a SELECT", ms)

    # WITH RECURSIVE is rejected outright. The cost gate cannot price it: the
    # planner guesses a fixed multiple of the non-recursive term, so
    # `WITH RECURSIVE b(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM b)` estimates
    # cheap and runs until the statement timeout. Nothing this star schema is
    # asked about needs recursion -- there is no hierarchy in it.
    if re.search(r"\bwith\s+recursive\b", body, re.IGNORECASE):
        return Stage("shape", False,
                     "WITH RECURSIVE is not allowed -- recursive CTEs cannot be "
                     "cost-bounded; express the query without recursion", ms)

    return Stage("shape", True, "single SELECT", ms)


def validate_keywords(sql: str) -> Stage:
    t0 = time.perf_counter()
    bare = strip_sql_noise(sql).lower()
    found = [k for k in FORBIDDEN if re.search(rf"\b{re.escape(k)}\b", bare)]
    ms = (time.perf_counter() - t0) * 1000
    if found:
        return Stage("keywords", False,
                     f"forbidden keyword(s): {', '.join(sorted(set(found)))}", ms)
    return Stage("keywords", True, "no forbidden keywords", ms)


def validate_functions(sql: str) -> Stage:
    """Allowlist every called function, and refuse catalog access outright.

    Runs on SQL stripped of comments and string literals, so a name inside a
    quoted string is not mistaken for a call. That stripping is also why this
    gate has to exist: it is what hid `query_to_xml`'s payload from the keyword
    scan, and no denylist entry would have covered the functions nobody had
    thought of.
    """
    t0 = time.perf_counter()
    bare = strip_sql_noise(sql)

    catalog = CATALOG_RE.search(bare)
    if catalog:
        ms = (time.perf_counter() - t0) * 1000
        return Stage("functions", False,
                     f"reference to {catalog.group(0)!r} -- system catalogs and "
                     "the information schema are not queryable", ms)

    cte_names = {m.group(1).lower() for m in CTE_RE.finditer(bare)}

    unknown: list[str] = []
    for m in CALL_RE.finditer(bare):
        name = m.group(1).lower()
        if name in NON_CALL_KEYWORDS or name in ALLOWED_FUNCTIONS or name in cte_names:
            continue
        # An identifier directly after a closing paren is a derived-table alias
        # carrying a column list -- `(SELECT ...) t (a, b)` -- not a call.
        prefix = bare[:m.start()].rstrip()
        if prefix.endswith(")"):
            continue
        unknown.append(name)

    ms = (time.perf_counter() - t0) * 1000
    if unknown:
        names = ", ".join(sorted(set(unknown)))
        return Stage("functions", False,
                     f"function(s) not on the allowlist: {names} -- rewrite using "
                     "standard aggregate, window, string, math or date functions", ms)
    return Stage("functions", True, "all called functions allowed", ms)


async def validate_cost(sql: str, max_cost: float = MAX_COST) -> tuple[Stage, float]:
    """EXPLAIN without ANALYZE -- plans the query without running it."""
    t0 = time.perf_counter()
    try:
        async with acquire(timeout_ms=10_000) as conn:
            async with conn.cursor() as cur:
                await cur.execute(f"EXPLAIN (FORMAT JSON) {sql}")
                row = await cur.fetchone()
        plan = list(row.values())[0][0]["Plan"]
        cost = float(plan.get("Total Cost", 0.0))
    except Exception as exc:
        ms = (time.perf_counter() - t0) * 1000
        return Stage("explain", False, f"{type(exc).__name__}: {exc}", ms), 0.0

    ms = (time.perf_counter() - t0) * 1000
    if cost > max_cost:
        return Stage("explain", False,
                     f"estimated cost {cost:,.0f} exceeds ceiling {max_cost:,.0f}",
                     ms), cost
    return Stage("explain", True, f"estimated cost {cost:,.0f}", ms), cost


async def execute_guarded(sql: str) -> tuple[Stage, list[dict], list[str]]:
    t0 = time.perf_counter()
    try:
        async with acquire(timeout_ms=STATEMENT_TIMEOUT_MS) as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql)
                rows = await cur.fetchmany(MAX_ROWS)
                cols = [d.name for d in (cur.description or [])]
    except Exception as exc:
        ms = (time.perf_counter() - t0) * 1000
        return Stage("execute", False, f"{type(exc).__name__}: {exc}", ms), [], []
    ms = (time.perf_counter() - t0) * 1000
    return Stage("execute", True, f"{len(rows)} rows in {ms:,.0f} ms", ms), rows, cols


def validate_result_shape(rows: list[dict], cols: list[str]) -> Stage:
    if not cols:
        return Stage("result_shape", False, "query returned no columns")
    if len(rows) >= MAX_ROWS:
        return Stage("result_shape", False,
                     f"result truncated at {MAX_ROWS} rows -- add a LIMIT or aggregate")
    return Stage("result_shape", True, f"{len(cols)} columns, {len(rows)} rows")


async def answer(question: str, provider: LLMProvider,
                 max_attempts: int = 2, use_router: bool = True) -> NL2SQLResult:
    """Answer a question, or decline before spending a model call on it.

    `use_router` exists so the benchmark can run both ways against the same
    cases. A claim that the router improved anything is only worth making if the
    same suite has been measured without it -- see reports/eval/.
    """
    started = time.perf_counter()
    result = NL2SQLResult(question=question, succeeded=False, provider=provider.name)
    feedback: str | None = None

    if use_router:
        routing = classify(question)
        result.routing = routing.to_dict()
        if not routing.should_generate_sql:
            # Declining here is the answer, not an error. The reason and the
            # clarification are what the UI shows, and no SQL is generated --
            # which is also why an UNSUPPORTED question costs no quota.
            result.failure_reason = routing.reason
            result.total_duration_ms = (time.perf_counter() - started) * 1000
            return result

    for n in range(1, max_attempts + 1):
        prompt = question if feedback is None else (
            f"{question}\n\nYour previous SQL was rejected.\n"
            f"SQL:\n{result.attempts[-1].sql}\n\nError: {feedback}\n\n"
            "Return corrected SQL only.")

        try:
            resp = await provider.complete(prompt, system=schema_prompt())
        except Exception as exc:
            result.failure_reason = f"provider error: {type(exc).__name__}: {exc}"
            break

        sql = clean_model_sql(resp.text)
        att = Attempt(attempt=n, sql=sql)
        result.attempts.append(att)

        s = validate_shape(sql)
        att.stages.append(s)
        if not s.passed:
            att.rejected_reason = s.detail
            feedback = att.error_fed_back = s.detail
            continue

        s = validate_keywords(sql)
        att.stages.append(s)
        if not s.passed:
            att.rejected_reason = s.detail
            feedback = att.error_fed_back = s.detail
            continue

        s = validate_functions(sql)
        att.stages.append(s)
        if not s.passed:
            att.rejected_reason = s.detail
            feedback = att.error_fed_back = s.detail
            continue

        s, _cost = await validate_cost(sql)
        att.stages.append(s)
        if not s.passed:
            att.rejected_reason = s.detail
            feedback = att.error_fed_back = s.detail
            continue

        s, rows, cols = await execute_guarded(sql)
        att.stages.append(s)
        if not s.passed:
            att.rejected_reason = s.detail
            feedback = att.error_fed_back = s.detail
            continue

        s = validate_result_shape(rows, cols)
        att.stages.append(s)
        if not s.passed:
            att.rejected_reason = s.detail
            feedback = att.error_fed_back = s.detail
            continue

        result.succeeded = True
        result.sql, result.rows, result.columns = sql, rows, cols
        break

    if not result.succeeded and not result.failure_reason:
        last = result.attempts[-1].rejected_reason if result.attempts else "no attempts"
        result.failure_reason = f"rejected after {len(result.attempts)} attempt(s): {last}"

    result.total_duration_ms = (time.perf_counter() - started) * 1000
    return result
