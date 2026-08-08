"""Phase 2 benchmark harness.

Design constraints this exists to satisfy:

* **Every figure in docs/performance.md must trace to a row.** Results go to
  perf_measurement; the document is generated from it. A number that cannot be
  produced by a query does not belong in the document.
* **Comparisons must be provably like-for-like.** Query text and its SHA-256 are
  stored with each measurement, so a query edited between the before and after
  runs is detectable rather than assumed identical.
* **Checkpoints must not contaminate a window.** bgwriter stats are reset
  immediately before each measurement and read immediately after, giving
  per-window counts rather than deltas against a cumulative baseline. A window
  containing a requested checkpoint is marked discarded, not silently kept.
* **Cache state is reported, not asserted.** Windows has no supported way to
  clear the OS file cache, so "cold" here means cold shared_buffers only. Rather
  than rely on the label, every measurement records shared_hit vs shared_read so
  a reader can judge the cache state directly.
"""

from __future__ import annotations

import hashlib
import json
import time

import psycopg
from rich.console import Console
from rich.table import Table

from rrip.config import PROJECT_ROOT
from rrip.db.connection import connect

console = Console()

QUERY_DIR = PROJECT_ROOT / "sql" / "perf"

# Relations pg_prewarm loads before a warm measurement, in a fixed order so
# warm-up is deterministic rather than incidental.
#
# Caveat recorded honestly: fact_causal is 3,193 MB against 1 GB of
# shared_buffers, so prewarming it cannot cache the whole relation -- only the
# tail of the read survives. Prewarm makes the state reproducible; it does not
# make it complete.
PREWARM_TABLES = ["dim_week", "dim_date", "dim_product", "dim_store",
                  "dim_household", "dim_campaign"]

SETTINGS_OF_INTEREST = (
    "shared_buffers", "work_mem", "maintenance_work_mem", "effective_cache_size",
    "random_page_cost", "effective_io_concurrency", "max_parallel_workers_per_gather",
    "max_wal_size", "synchronous_commit", "jit", "track_io_timing",
)


def load_queries(only: list[str] | None = None) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in sorted(QUERY_DIR.glob("q*.sql")):
        name = p.stem
        if only and not any(name.startswith(o) for o in only):
            continue
        out[name] = p.read_text(encoding="utf-8")
    if not out:
        raise FileNotFoundError(f"no queries matched under {QUERY_DIR}")
    return out


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def plan_fingerprint(plan: dict) -> str:
    """Structural hash of a plan: node types, relations, indexes, strategies.

    Deliberately excludes costs, row counts and timings, so the hash changes
    when the plan *shape* changes and not when the machine is merely busier.
    That is what makes "did the optimization change the plan?" answerable.
    """
    parts: list[str] = []

    def walk(node: dict, depth: int = 0) -> None:
        parts.append("|".join(str(node.get(k, "")) for k in (
            "Node Type", "Relation Name", "Index Name", "Join Type",
            "Strategy", "Partial Mode", "Scan Direction")) + f"@{depth}")
        for child in node.get("Plans", []) or []:
            walk(child, depth + 1)

    walk(plan)
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:16]


def _bgwriter(conn: psycopg.Connection) -> tuple[int, int]:
    with conn.cursor() as cur:
        cur.execute("SELECT checkpoints_timed, checkpoints_req FROM pg_stat_bgwriter")
        r = cur.fetchone()
    return int(r[0]), int(r[1])


def _wal_lsn(conn: psycopg.Connection) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT pg_current_wal_lsn() - '0/0'::pg_lsn")
        return int(cur.fetchone()[0])


def prewarm(conn: psycopg.Connection, include_facts: bool) -> None:
    with conn.cursor() as cur:
        for t in PREWARM_TABLES:
            cur.execute("SELECT pg_prewarm(%s)", (t,))
        if include_facts:
            # Partitioned parents cannot be prewarmed directly; walk children.
            cur.execute("""SELECT c.relname FROM pg_inherits i
                           JOIN pg_class p ON p.oid = i.inhparent
                           JOIN pg_class c ON c.oid = i.inhrelid
                           WHERE p.relname IN ('fact_transactions','fact_causal')
                           ORDER BY c.relname""")
            for (child,) in cur.fetchall():
                cur.execute("SELECT pg_prewarm(%s)", (child,))
    conn.commit()


def start_run(conn: psycopg.Connection, variant: str, label: str) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT version()")
        ver = cur.fetchone()[0]
        cur.execute(
            "SELECT jsonb_object_agg(name, setting || coalesce(' ' || unit, '')) "
            "FROM pg_settings WHERE name = ANY(%s)", (list(SETTINGS_OF_INTEREST),))
        settings = cur.fetchone()[0]
        cur.execute(
            """INSERT INTO perf_run (variant, label, pg_version, settings, on_ac_power)
               VALUES (%s,%s,%s,%s,true) RETURNING run_id""",
            (variant, label, ver, json.dumps(settings)))
        run_id = int(cur.fetchone()[0])
    conn.commit()
    return run_id


def measure(conn: psycopg.Connection, run_id: int, name: str, sql: str,
            cache_state: str, rep: int, setup_sql: str | None = None) -> dict:
    """Run one repetition under EXPLAIN (ANALYZE, BUFFERS) inside a clean window.

    `setup_sql` runs immediately before the measured statement, in the same
    session -- used for per-session settings such as work_mem. It runs outside
    the measured window so its own cost is not attributed to the query.
    """
    if setup_sql:
        with conn.cursor() as cur:
            cur.execute(setup_sql)  # type: ignore[arg-type]
    with conn.cursor() as cur:
        cur.execute("SELECT pg_stat_reset_shared('bgwriter')")
    conn.commit()
    wal_before = _wal_lsn(conn)

    t0 = time.perf_counter()
    with conn.cursor() as cur:
        cur.execute(f"EXPLAIN (ANALYZE, BUFFERS, TIMING, FORMAT JSON) {sql}")
        payload = cur.fetchone()[0]
    wall_ms = (time.perf_counter() - t0) * 1000
    conn.commit()

    root = payload[0]
    plan = root["Plan"]
    ck_timed, ck_req = _bgwriter(conn)
    wal_bytes = _wal_lsn(conn) - wal_before

    rec = {
        "planning_ms": root.get("Planning Time"),
        "execution_ms": root.get("Execution Time", wall_ms),
        "rows_returned": plan.get("Actual Rows"),
        "shared_hit": plan.get("Shared Hit Blocks"),
        "shared_read": plan.get("Shared Read Blocks"),
        "shared_dirtied": plan.get("Shared Dirtied Blocks"),
        "shared_written": plan.get("Shared Written Blocks"),
        "temp_read": plan.get("Temp Read Blocks"),
        "temp_written": plan.get("Temp Written Blocks"),
        "io_read_ms": root.get("Planning", {}).get("I/O Read Time") or plan.get("I/O Read Time"),
        "io_write_ms": plan.get("I/O Write Time"),
        "plan_hash": plan_fingerprint(plan),
        "checkpoints_timed": ck_timed,
        "checkpoints_req": ck_req,
        "wal_bytes": wal_bytes,
        "discarded": ck_req > 0,
        "discard_reason": f"requested checkpoint during window (req={ck_req})" if ck_req else None,
    }

    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO perf_measurement (
                   run_id, query_name, query_sql, query_sha256, plan_hash,
                   cache_state, rep, planning_ms, execution_ms, rows_returned,
                   shared_hit, shared_read, shared_dirtied, shared_written,
                   temp_read, temp_written, io_read_ms, io_write_ms,
                   checkpoints_timed, checkpoints_req, wal_bytes,
                   discarded, discard_reason, plan)
               VALUES (%(run_id)s,%(name)s,%(sql)s,%(sha)s,%(plan_hash)s,
                       %(cache_state)s,%(rep)s,%(planning_ms)s,%(execution_ms)s,
                       %(rows_returned)s,%(shared_hit)s,%(shared_read)s,
                       %(shared_dirtied)s,%(shared_written)s,%(temp_read)s,
                       %(temp_written)s,%(io_read_ms)s,%(io_write_ms)s,
                       %(checkpoints_timed)s,%(checkpoints_req)s,%(wal_bytes)s,
                       %(discarded)s,%(discard_reason)s,%(plan)s)""",
            {**rec, "run_id": run_id, "name": name, "sql": sql,
             "sha": sha256(sql), "cache_state": cache_state, "rep": rep,
             "plan": json.dumps(payload)})
    conn.commit()
    return rec


def run(variant: str, label: str, reps: int = 5,
        only: list[str] | None = None) -> int:
    queries = load_queries(only)
    console.rule(f"[bold]Phase 2 benchmark -- {variant}")
    console.print(f"queries: {', '.join(queries)}")
    console.print(f"reps: {reps} warm per query")
    # Cold measurement needs shared_buffers cleared, which needs a service
    # restart, which needs elevation this process does not have. Rather than
    # mislabel a warm run as cold, the harness measures warm only and reports
    # shared_hit vs shared_read so the actual cache state is visible as data.
    console.print("[dim]cache state: warm only "
                  "(cold requires an elevated service restart)[/dim]\n")

    with connect() as conn:
        run_id = start_run(conn, variant, label)
        console.print(f"run_id = {run_id}\n")
        prewarm(conn, include_facts=True)

        for name, sql in queries.items():
            console.print(f"[cyan]{name}[/cyan]")
            durations: list[float] = []
            for rep in range(1, reps + 1):
                rec = measure(conn, run_id, name, sql, "warm", rep)
                flag = " [yellow](discarded: checkpoint)[/yellow]" if rec["discarded"] else ""
                console.print(f"    rep {rep}: {rec['execution_ms']:>10,.1f} ms  "
                              f"hit={rec['shared_hit'] or 0:>9,} "
                              f"read={rec['shared_read'] or 0:>8,} "
                              f"temp_w={rec['temp_written'] or 0:>7,}{flag}")
                if not rec["discarded"]:
                    durations.append(float(rec["execution_ms"]))
            if durations:
                durations.sort()
                med = durations[len(durations) // 2]
                console.print(f"    [bold]median {med:,.1f} ms[/bold]  "
                              f"(min {durations[0]:,.1f} / max {durations[-1]:,.1f})\n")

        with conn.cursor() as cur:
            cur.execute("UPDATE perf_run SET finished_at = clock_timestamp() "
                        "WHERE run_id = %s", (run_id,))
        conn.commit()
    return run_id


def summary(run_id: int | None = None) -> None:
    with connect() as conn, conn.cursor() as cur:
        if run_id is None:
            cur.execute("SELECT max(run_id) FROM perf_run")
            run_id = cur.fetchone()[0]
        cur.execute("""
            SELECT query_name, cache_state, count(*) FILTER (WHERE NOT discarded),
                   round(percentile_cont(0.5) WITHIN GROUP (ORDER BY execution_ms)
                         FILTER (WHERE NOT discarded)::numeric, 1),
                   round(min(execution_ms) FILTER (WHERE NOT discarded)::numeric, 1),
                   round(max(execution_ms) FILTER (WHERE NOT discarded)::numeric, 1),
                   max(shared_read), max(temp_written),
                   count(*) FILTER (WHERE discarded),
                   min(left(plan_hash, 8))
            FROM perf_measurement WHERE run_id = %s
            GROUP BY 1,2 ORDER BY 1,2""", (run_id,))
        rows = cur.fetchall()

    t = Table(title=f"perf_run {run_id}")
    for c in ("query", "cache", "n", "median ms", "min", "max",
              "sh_read", "temp_w", "disc", "plan"):
        t.add_column(c, justify="right" if c not in ("query", "cache", "plan") else "left")
    for r in rows:
        t.add_row(r[0].replace("_", " ")[:26], r[1], str(r[2]), f"{r[3]:,}", f"{r[4]:,}",
                  f"{r[5]:,}", f"{r[6] or 0:,}", f"{r[7] or 0:,}", str(r[8]), r[9] or "")
    console.print(t)
