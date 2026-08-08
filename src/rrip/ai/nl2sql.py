"""6a -- Validated natural language to SQL.

The model proposes SQL. It never executes anything, and it never sees data
before the SQL has passed every gate below. Each gate is recorded so the
reject-and-retry behaviour is observable rather than inferred from a final
answer.

Gates, in order:
  1. shape      -- exactly one statement, and it must be a SELECT
  2. keywords   -- no DDL/DML, no multi-statement, no dangerous functions
  3. explain    -- EXPLAIN (no ANALYZE) and reject above a cost ceiling
  4. execute    -- hard statement timeout, row cap
  5. result     -- validate the returned shape

A rejection at any gate is fed back to the model as an error message, up to a
configured attempt limit.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from rrip.ai.provider import LLMProvider
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
                 max_attempts: int = 2) -> NL2SQLResult:
    started = time.perf_counter()
    result = NL2SQLResult(question=question, succeeded=False, provider=provider.name)
    feedback: str | None = None

    for n in range(1, max_attempts + 1):
        prompt = question if feedback is None else (
            f"{question}\n\nYour previous SQL was rejected.\n"
            f"SQL:\n{result.attempts[-1].sql}\n\nError: {feedback}\n\n"
            "Return corrected SQL only.")

        try:
            resp = await provider.complete(prompt, system=SCHEMA_PROMPT)
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
