"""Phase 1 loader: resumable, idempotent bulk load of the dunnhumby star schema.

Shape of the load, and why:

  1. COPY each raw CSV into an UNLOGGED staging table, streaming file bytes with
     no Python-side parsing. This is the fast part.
  2. Assert against the staged data while rejecting it is still cheap.
  3. INSERT into the partitioned fact tables in batches keyed on the partition
     column, so throughput is visible per partition rather than as one opaque
     wall-clock number.
  4. Record each step as done. A killed load resumes from the last completed
     step instead of restarting.

Foreign keys and secondary indexes are created after the load, not before:
validating FKs and maintaining indexes row-by-row during COPY of 36.8M rows is
the most expensive possible ordering.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path

import psycopg
from rich.console import Console

from rrip.config import settings
from rrip.db.connection import apply_sql_file, connect
from rrip.ingest import steps as S
from rrip.profile.discovery import count_rows, discover

console = Console()

# The estimate given before the loader was written, carried here so actuals are
# checked automatically rather than by eye.
CAUSAL_CEILING_S = 30 * 60


class LoadError(RuntimeError):
    """Raised when the load cannot proceed safely or cannot be verified."""


def _is_done(conn: psycopg.Connection, step: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT status FROM etl_load_control WHERE step_name=%s", (step,))
        row = cur.fetchone()
    return bool(row and row[0] == "done")


def _run_step(conn: psycopg.Connection, name: str, fn: Callable[[], int],
              source: str | None = None) -> int:
    if _is_done(conn, name):
        console.print(f"  [dim]skip[/dim]   {name}")
        return 0
    console.print(f"  [cyan]run[/cyan]    {name}")
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO etl_load_control (step_name, source_file, status, started_at)
               VALUES (%s,%s,'running',now())
               ON CONFLICT (step_name) DO UPDATE SET status='running',
                   started_at=now(), finished_at=NULL, error=NULL,
                   rows_loaded=NULL, duration_s=NULL""",
            (name, source))
    conn.commit()

    t0 = time.perf_counter()
    try:
        rows = fn()
        conn.commit()
    except Exception as exc:
        conn.rollback()
        with conn.cursor() as cur:
            cur.execute("""UPDATE etl_load_control SET status='failed',
                           finished_at=now(), error=%s WHERE step_name=%s""",
                        (f"{type(exc).__name__}: {exc}"[:2000], name))
        conn.commit()
        raise
    dt = time.perf_counter() - t0
    with conn.cursor() as cur:
        cur.execute("""UPDATE etl_load_control SET status='done', finished_at=now(),
                       rows_loaded=%s, duration_s=%s WHERE step_name=%s""",
                    (rows, round(dt, 2), name))
    conn.commit()
    rate = f"  ({rows / dt:,.0f} rows/s)" if dt > 0.01 and rows else ""
    console.print(f"         {rows:,} rows in {dt:,.1f}s{rate}")
    return rows


def copy_csv(conn: psycopg.Connection, path: Path, table: str, columns: list[str]) -> int:
    sql = f"COPY {table} ({', '.join(columns)}) FROM STDIN WITH (FORMAT csv, HEADER true)"
    with conn.cursor() as cur:
        with cur.copy(sql) as cp, path.open("rb") as fh:  # type: ignore[arg-type]
            while chunk := fh.read(1 << 20):
                cp.write(chunk)
        return int(cur.rowcount or 0)


def _insert_batched(conn: psycopg.Connection, stepname: str, keys: list,
                    sql_for: Callable[[object], str], label: Callable[[object], str],
                    ceiling_s: float | None = None) -> int:
    total, t_start, warned = 0, time.perf_counter(), False
    for i, key in enumerate(keys, 1):
        t0 = time.perf_counter()
        with conn.cursor() as cur:
            cur.execute(sql_for(key))  # type: ignore[arg-type]
            n = int(cur.rowcount or 0)
        dt = time.perf_counter() - t0
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO etl_batch_log (step_name, batch_key,
                           rows_loaded, duration_s, rows_per_s) VALUES (%s,%s,%s,%s,%s)""",
                        (stepname, label(key), n, round(dt, 3),
                         round(n / dt, 1) if dt > 0 else 0))
        conn.commit()
        total += n

        elapsed = time.perf_counter() - t_start
        if i % 15 == 0 or i == len(keys):
            eta = elapsed / i * (len(keys) - i)
            console.print(f"         [{i:>3}/{len(keys)}] {label(key):>9}  "
                          f"{total:>11,} rows  {elapsed:6.1f}s  "
                          f"eta {timedelta(seconds=int(eta))}")
        if ceiling_s and not warned and i >= 5:
            projected = elapsed / i * len(keys)
            if projected > ceiling_s:
                warned = True
                console.print(f"         [bold yellow]PROJECTION WARNING[/bold yellow]: "
                              f"~{projected/60:.0f} min projected after {i} batches, "
                              f"above the {ceiling_s/60:.0f} min ceiling")
    return total


def run(raw_dir: Path | None = None, reset: bool = False) -> None:
    raw = raw_dir or settings.raw_dir
    files = discover(raw)
    d1 = settings.day1_date

    console.rule("[bold]Phase 1 -- load")
    console.print(f"source: [cyan]{raw}[/cyan]")
    console.print(f"anchor: day 1 = {d1} ({d1.strftime('%A')}), "
                  f"day 6 = {(d1 + timedelta(days=5)).strftime('%A')}\n")

    with connect() as conn:
        apply_sql_file(conn, "sql/ddl/05_control.sql")
        conn.commit()

        if reset:
            console.print("[yellow]reset: dropping and rebuilding schema[/yellow]")
            with conn.cursor() as cur:
                cur.execute("DROP SCHEMA public CASCADE")
                cur.execute("CREATE SCHEMA public")
            conn.commit()
            apply_sql_file(conn, "sql/ddl/05_control.sql")
            conn.commit()

        _run_step(conn, "ddl_dimensions",
                  lambda: (apply_sql_file(conn, "sql/ddl/10_dimensions.sql"), 0)[1])

        with conn.cursor() as cur:
            for stmt in S.STAGING_DDL.split(";"):
                if stmt.strip():
                    cur.execute(stmt)  # type: ignore[arg-type]
        conn.commit()

        # -- stage every source file ---------------------------------------
        for logical, (table, cols) in S.COPY_SPECS.items():
            path = S.file_for(files, logical)

            def _copy(p: Path = path, t: str = table, c: list[str] = cols) -> int:
                with conn.cursor() as cur:
                    cur.execute(f"TRUNCATE {t}")
                return copy_csv(conn, p, t, c)

            _run_step(conn, f"stage_{logical}", _copy, path.name)

        # dim_week and dim_date are derived from the anchor rule rather than
        # loaded. They come before the assertions because the causal week check
        # validates staged weeks against dim_week, and before 20_facts.sql,
        # which reads dim_date to derive partition bounds.
        _run_step(conn, "dim_week", lambda: _derive_week(conn, d1))
        _run_step(conn, "dim_date", lambda: _derive_date(conn, d1))

        # -- assertions, before anything reaches a fact table ---------------
        console.print("\n[bold]assertions[/bold]")
        S.assert_week_rule(conn)
        console.print("  [green]ok[/green]     week_no = (day + 8) / 7 on every staged row")
        _, lo, hi = S.assert_causal_weeks_known(conn)
        console.print(f"  [green]ok[/green]     causal weeks {lo}-{hi} all present in dim_week")
        dupes = S.check_causal_duplicates(conn)
        verdict = "[green]ok[/green]    " if dupes == 0 else "[yellow]warn[/yellow]  "
        console.print(f"  {verdict} causal (week,product,store) duplicates: {dupes:,}")
        anomalies = S.anomaly_counts(conn)
        console.print(f"  [dim]anomalies: {anomalies}[/dim]\n")

        # Persist, so reconciliation and the Phase 4 suite read measured values
        # from this run rather than recomputing or hardcoding them.
        _record_quality(conn, "causal_duplicates_removed", dupes,
                        "rows collapsed by DISTINCT ON (week_no, product_id, store_id)")
        for k, v in anomalies.items():
            _record_quality(conn, k, v, "measured at load time from staged data")
        conn.commit()

        # -- remaining dimensions, in dependency order ----------------------
        for name in ("dim_product", "dim_household", "dim_store", "dim_campaign",
                     "dim_coupon", "bridge_coupon_product", "bridge_coupon_campaign",
                     "bridge_campaign_household"):
            def _ins(sql: str = S.DIM_SQL[name]) -> int:
                with conn.cursor() as cur:
                    cur.execute(sql)  # type: ignore[arg-type]
                    return int(cur.rowcount or 0)
            _run_step(conn, name, _ins)

        _run_step(conn, "ddl_facts",
                  lambda: (apply_sql_file(conn, "sql/ddl/20_facts.sql"), 0)[1])

        # -- facts ----------------------------------------------------------
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT month_start FROM dim_date ORDER BY 1")
            months = [r[0] for r in cur.fetchall()]
            cur.execute("SELECT DISTINCT week_no FROM stg_causal ORDER BY 1")
            weeks = [r[0] for r in cur.fetchall()]

        _run_step(conn, "fact_transactions", lambda: _insert_batched(
            conn, "fact_transactions", months, S.fact_transactions_month_sql,
            lambda m: str(m)[:7]))

        _run_step(conn, "fact_causal", lambda: _insert_batched(
            conn, "fact_causal", weeks, S.fact_causal_week_sql,
            lambda w: f"w{w}", ceiling_s=CAUSAL_CEILING_S))

        _run_step(conn, "fact_coupon_redemption", lambda: _exec(
            conn, S.DIM_SQL["fact_coupon_redemption"]))

        _run_step(conn, "constraints",
                  lambda: (apply_sql_file(conn, "sql/ddl/30_constraints.sql"), 0)[1])
        _run_step(conn, "indexes",
                  lambda: (apply_sql_file(conn, "sql/ddl/40_indexes.sql"), 0)[1])

        console.print("\n[bold green]load complete[/bold green]")


def _exec(conn: psycopg.Connection, sql: str) -> int:
    with conn.cursor() as cur:
        cur.execute(sql)  # type: ignore[arg-type]
        return int(cur.rowcount or 0)


def _derive_week(conn: psycopg.Connection, d1) -> int:
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO dim_week (week_no, start_day, end_day, start_date, end_date,
                                  day_count, is_partial_week)
            SELECT w, greatest(1, 7*w-8), least(711, 7*w-2),
                   %(d1)s::date + (greatest(1, 7*w-8) - 1),
                   %(d1)s::date + (least(711, 7*w-2) - 1),
                   least(711,7*w-2) - greatest(1,7*w-8) + 1,
                   (least(711,7*w-2) - greatest(1,7*w-8) + 1) <> 7
            FROM generate_series(1,102) AS w
            ON CONFLICT (week_no) DO NOTHING""", {"d1": d1})
        return int(cur.rowcount or 0)


def _derive_date(conn: psycopg.Connection, d1) -> int:
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO dim_date (date_key, day_number, week_no, day_of_week,
                day_name, is_weekend, month_number, month_name, month_start,
                quarter_number, year_number, is_partial_week)
            SELECT x.dt, d, (d+8)/7,
                   EXTRACT(ISODOW FROM x.dt)::smallint,
                   trim(to_char(x.dt,'Day')),
                   EXTRACT(ISODOW FROM x.dt) >= 6,
                   EXTRACT(MONTH FROM x.dt)::smallint,
                   trim(to_char(x.dt,'Month')),
                   date_trunc('month', x.dt)::date,
                   EXTRACT(QUARTER FROM x.dt)::smallint,
                   EXTRACT(YEAR FROM x.dt)::smallint,
                   w.is_partial_week
            FROM generate_series(1,711) AS d
            CROSS JOIN LATERAL (SELECT %(d1)s::date + (d-1) AS dt) x
            JOIN dim_week w ON w.week_no = (d+8)/7
            ON CONFLICT (date_key) DO NOTHING""", {"d1": d1})
        return int(cur.rowcount or 0)


def _record_quality(conn: psycopg.Connection, name: str, value: int, note: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO etl_data_quality (check_name, value, note)
               VALUES (%s,%s,%s)
               ON CONFLICT (check_name) DO UPDATE
               SET value=EXCLUDED.value, note=EXCLUDED.note,
                   recorded_at=clock_timestamp()""",
            (name, value, note))


def reconcile(raw_dir: Path | None = None) -> list[tuple[str, int, int, int, bool]]:
    """Compare loaded row counts against raw file line counts.

    Returns (table, source_rows, expected_rows, loaded_rows, ok).

    fact_causal is deliberately deduplicated on its natural key, so `expected`
    subtracts the duplicate count recorded during the load. Treating that as a
    mismatch would be a false failure on a documented condition.
    """
    raw = raw_dir or settings.raw_dir
    files = discover(raw)
    checks = [
        ("transactions", "fact_transactions", None),
        ("causal", "fact_causal", "causal_duplicates_removed"),
        ("products", "dim_product", None),
        ("coupon_redemptions", "fact_coupon_redemption", None),
    ]
    out: list[tuple[str, int, int, int, bool]] = []
    with connect() as conn, conn.cursor() as cur:
        for logical, table, dedup_key in checks:
            cur.execute(f"SELECT count(*) FROM {table}")  # noqa: S608
            loaded = int(cur.fetchone()[0])
            source = count_rows(files[logical]) if logical in files else -1
            deducted = 0
            if dedup_key:
                cur.execute("SELECT value FROM etl_data_quality WHERE check_name=%s",
                            (dedup_key,))
                row = cur.fetchone()
                if row is None:
                    raise LoadError(
                        f"reconciliation cannot verify {table}: no recorded "
                        f"'{dedup_key}'. Re-run the load so the value is measured "
                        "rather than assumed.")
                deducted = int(row[0])
            expected = source - deducted
            out.append((table, source, expected, loaded, expected == loaded))
    return out
