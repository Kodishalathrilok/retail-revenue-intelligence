"""Phase 2 interventions: one distinct fix per query, measured.

Each function applies its intervention, records measurements into
perf_measurement under a labelled perf_run, and returns what changed. Nothing
here decides whether an intervention "worked" -- that is read off the recorded
plan hashes and timings afterwards, including when the answer is "it didn't".
"""

from __future__ import annotations

import time

from rich.console import Console

from rrip.bench.mechanisms import walk
from rrip.bench.runner import load_queries, measure, start_run
from rrip.db.connection import connect

console = Console()

WORK_MEM_SWEEP = ["32MB", "64MB", "128MB", "256MB", "512MB"]


def _median(xs: list[float]) -> float:
    xs = sorted(xs)
    return xs[len(xs) // 2]


def q2_work_mem_sweep(reps: int = 3) -> None:
    """Q2: the spill is in a Sort node, so work_mem is the lever.

    HashAgg Batches was 0 in the baseline, so hash_mem_multiplier would do
    nothing -- worth stating because it is the obvious wrong knob to reach for.
    """
    sql = load_queries(["q2"])["q2_basket_affinity"]
    console.rule("[bold]Q2 -- work_mem sweep")
    with connect() as conn:
        for wm in WORK_MEM_SWEEP:
            run_id = start_run(conn, "after", f"q2 work_mem={wm}")
            times, temps = [], []
            for rep in range(1, reps + 1):
                rec = measure(conn, run_id, "q2_basket_affinity", sql, "warm", rep,
                              setup_sql=f"SET work_mem = '{wm}'")
                times.append(float(rec["execution_ms"]))
                temps.append(int(rec["temp_written"] or 0))
            console.print(f"  work_mem={wm:>6}  median {_median(times):>10,.1f} ms  "
                          f"temp_written {max(temps):>8,} blocks")
        with conn.cursor() as cur:
            cur.execute("RESET work_mem")


def q3_expression_index() -> None:
    """Q3: the predicate is non-sargable; make the expression indexable."""
    sql = load_queries(["q3"])["q3_reorder_by_commodity"]
    console.rule("[bold]Q3 -- expression index")
    with connect() as conn:
        with conn.cursor() as cur:
            t0 = time.perf_counter()
            cur.execute("""CREATE INDEX IF NOT EXISTS ix_dim_product_commodity_upper
                           ON dim_product (upper(commodity_desc))""")
            build = (time.perf_counter() - t0) * 1000
            cur.execute("ANALYZE dim_product")
        conn.commit()
        console.print(f"  index built in {build:,.0f} ms")

        run_id = start_run(conn, "after", "q3 expression index on upper(commodity_desc)")
        times = []
        for rep in range(1, 6):
            rec = measure(conn, run_id, "q3_reorder_by_commodity", sql, "warm", rep)
            times.append(float(rec["execution_ms"]))
        console.print(f"  median {_median(times):,.1f} ms")

        with conn.cursor() as cur:
            cur.execute("EXPLAIN (FORMAT JSON) " + sql)
            plan = cur.fetchone()[0][0]["Plan"]
        nodes = [n for n in walk(plan) if n.get("Relation Name") == "dim_product"]
        console.print(f"  dim_product access: {[n.get('Node Type') for n in nodes]} "
                      f"index={[n.get('Index Name') for n in nodes]}")


def _q4_worst_estimate(conn) -> tuple[float, str, float, float]:
    from rrip.bench.mechanisms import _max_estimate_error
    sql = load_queries(["q4"])["q4_promo_lift_by_department"]
    with conn.cursor() as cur:
        cur.execute("EXPLAIN (ANALYZE, FORMAT JSON) " + sql)
        plan = cur.fetchone()[0][0]["Plan"]
    ratio, node = _max_estimate_error(plan)
    where = f"{node.get('Node Type')}" if node else "n/a"
    return ratio, where, float(node.get("Plan Rows") or 0), float(node.get("Actual Rows") or 0)


def q4_extended_statistics() -> None:
    """Q4: pre-registered as unlikely to help. Applied and reported regardless."""
    sql = load_queries(["q4"])["q4_promo_lift_by_department"]
    console.rule("[bold]Q4 -- extended statistics (pre-registered as likely ineffective)")
    with connect() as conn:
        before = _q4_worst_estimate(conn)
        console.print(f"  BEFORE: worst estimate error {before[0]:,.1f}x at {before[1]} "
                      f"(est {before[2]:,.0f} vs actual {before[3]:,.0f})")

        with conn.cursor() as cur:
            try:
                cur.execute("""CREATE STATISTICS IF NOT EXISTS stx_fact_causal_store_week
                               (dependencies, ndistinct)
                               ON store_id, week_no, product_id FROM fact_causal""")
                conn.commit()
                created = "on partitioned parent"
            except Exception as exc:
                conn.rollback()
                created = f"parent failed ({type(exc).__name__}); creating per-partition"
                with conn.cursor() as c2:
                    c2.execute("""SELECT c.relname FROM pg_inherits i
                                  JOIN pg_class p ON p.oid=i.inhparent
                                  JOIN pg_class c ON c.oid=i.inhrelid
                                  WHERE p.relname='fact_causal'""")
                    for (child,) in c2.fetchall():
                        c2.execute(f"""CREATE STATISTICS IF NOT EXISTS stx_{child}
                                       (dependencies, ndistinct)
                                       ON store_id, week_no, product_id FROM {child}""")
                conn.commit()
        console.print(f"  statistics: {created}")

        with conn.cursor() as cur:
            cur.execute("ANALYZE fact_causal")
        conn.commit()

        after = _q4_worst_estimate(conn)
        console.print(f"  AFTER : worst estimate error {after[0]:,.1f}x at {after[1]} "
                      f"(est {after[2]:,.0f} vs actual {after[3]:,.0f})")

        run_id = start_run(conn, "after", "q4 extended statistics")
        times = []
        for rep in range(1, 6):
            rec = measure(conn, run_id, "q4_promo_lift_by_department", sql, "warm", rep)
            times.append(float(rec["execution_ms"]))
        console.print(f"  median {_median(times):,.1f} ms")


def q5_materialized_view() -> None:
    """Q5: matview, plus the refresh cost that decides whether it is worth it."""
    sql = load_queries(["q5"])["q5_cohort_retention"].rstrip().rstrip(";")
    console.rule("[bold]Q5 -- materialized view and refresh cost")
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("DROP MATERIALIZED VIEW IF EXISTS mv_cohort_retention")
            t0 = time.perf_counter()
            cur.execute(f"CREATE MATERIALIZED VIEW mv_cohort_retention AS {sql}")
            build = (time.perf_counter() - t0) * 1000
            # CONCURRENTLY requires a unique index on the matview.
            cur.execute("""CREATE UNIQUE INDEX ux_mv_cohort_retention
                           ON mv_cohort_retention (cohort_month, active_month)""")
            cur.execute("SELECT count(*) FROM mv_cohort_retention")
            rows = cur.fetchone()[0]
            cur.execute("SELECT pg_total_relation_size('mv_cohort_retention')")
            size = cur.fetchone()[0]
        conn.commit()
        console.print(f"  built in {build:,.0f} ms -- {rows:,} rows, {size / 1024:.0f} kB")

        run_id = start_run(conn, "after", "q5 materialized view")
        mv_sql = "SELECT * FROM mv_cohort_retention ORDER BY cohort_month, active_month"
        times = []
        for rep in range(1, 6):
            rec = measure(conn, run_id, "q5_cohort_retention", mv_sql, "warm", rep)
            times.append(float(rec["execution_ms"]))
        console.print(f"  query median {_median(times):,.1f} ms")

        for mode in ("full", "concurrent"):
            stmt = ("REFRESH MATERIALIZED VIEW mv_cohort_retention" if mode == "full"
                    else "REFRESH MATERIALIZED VIEW CONCURRENTLY mv_cohort_retention")
            durs = []
            for _ in range(3):
                t0 = time.perf_counter()
                with conn.cursor() as cur:
                    cur.execute(stmt)
                conn.commit()
                durs.append((time.perf_counter() - t0) * 1000)
            med = _median(durs)
            with conn.cursor() as cur:
                cur.execute("""INSERT INTO perf_refresh (matview, mode, duration_ms,
                               rows_after, size_bytes)
                               SELECT 'mv_cohort_retention', %s, %s,
                                      (SELECT count(*) FROM mv_cohort_retention),
                                      pg_total_relation_size('mv_cohort_retention')""",
                            (mode, round(med, 3)))
            conn.commit()
            console.print(f"  refresh {mode:<11} median {med:>9,.1f} ms")


def run_all() -> None:
    q2_work_mem_sweep()
    q3_expression_index()
    q4_extended_statistics()
    q5_materialized_view()
