"""Build and push the aggregate tier.

Two modes:
  --local-only   build the pub_* tables in the local database and measure them.
                 Used to verify size before touching a hosted tier.
  (default)      build locally, then COPY each table to RRIP_PUBLISH_DSN.

Idempotent: each table is dropped and recreated inside its own transaction, so
re-publishing is safe and a failure cannot leave a partially written table
visible to readers.
"""

from __future__ import annotations

import io
import time
from dataclasses import dataclass

import psycopg
from rich.console import Console
from rich.table import Table as RichTable

from rrip.db.connection import connect
from rrip.publish.tables import DIM_PRODUCT_SELECT, DIMENSIONS, TABLES

console = Console()


@dataclass
class Published:
    name: str
    rows: int
    bytes_: int
    build_ms: float
    push_ms: float = 0.0


def _size(conn: psycopg.Connection, name: str) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT pg_total_relation_size(%s)", (name,))
        return int(cur.fetchone()[0])


def build_local(conn: psycopg.Connection) -> list[Published]:
    """Materialise every pub_* table from the star schema."""
    out: list[Published] = []

    for t in TABLES:
        t0 = time.perf_counter()
        with conn.cursor() as cur:
            cur.execute(f"DROP TABLE IF EXISTS {t.name} CASCADE")
            cur.execute(t.ddl)
            cur.execute(f"INSERT INTO {t.name} {t.select}")
            rows = cur.rowcount
        conn.commit()
        out.append(Published(t.name, int(rows or 0), _size(conn, t.name),
                             (time.perf_counter() - t0) * 1000))
        console.print(f"  {t.name:30s} {rows or 0:>8,} rows  "
                      f"{_size(conn, t.name)/1024:>8,.0f} kB")

    # Headline metrics: measured once, stored, never recomputed by a consumer.
    t0 = time.perf_counter()
    with conn.cursor() as cur:
        cur.execute("""
            WITH pr AS (SELECT ft.product_id, round(sum(ft.sales_value),2) rev
                        FROM fact_transactions ft WHERE ft.sales_value > 0
                        GROUP BY 1),
                 rk AS (SELECT rev, row_number() OVER (ORDER BY rev DESC) rnk,
                               sum(rev) OVER (ORDER BY rev DESC
                                   ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) cum,
                               sum(rev) OVER () tot, count(*) OVER () n FROM pr)
            INSERT INTO pub_headline (metric, value, context)
            SELECT 'products_for_80pct_revenue', min(rnk)::text,
                   round(100.0*min(rnk)/max(n),2)::text || '% of catalogue'
            FROM rk WHERE 100.0*cum/tot >= 80""")
        cur.execute("""
            INSERT INTO pub_headline (metric, value, context)
            SELECT 'total_revenue', round(sum(sales_value),2)::text, 'net of retail discount'
            FROM fact_transactions
            UNION ALL SELECT 'total_baskets', count(DISTINCT basket_id)::text, ''
            FROM fact_transactions
            UNION ALL SELECT 'total_households', count(*)::text, 'panel size'
            FROM dim_household
            UNION ALL SELECT 'panel_days', max(day_number)::text, 'day 1 = Wed 2015-01-07'
            FROM dim_date""")
        cur.execute("SELECT count(*) FROM pub_headline")
        n = int(cur.fetchone()[0])
    conn.commit()
    for p in out:
        if p.name == "pub_headline":
            p.rows, p.bytes_ = n, _size(conn, "pub_headline")
            p.build_ms += (time.perf_counter() - t0) * 1000
    console.print(f"  {'pub_headline (filled)':30s} {n:>8,} rows")

    # Dimensions, copied as-is (dim_product trimmed to used columns).
    for d in DIMENSIONS:
        t0 = time.perf_counter()
        pub = f"pub_{d}"
        with conn.cursor() as cur:
            cur.execute(f"DROP TABLE IF EXISTS {pub}")
            cur.execute(f"CREATE TABLE {pub} AS SELECT * FROM {d}")
            cur.execute(f"SELECT count(*) FROM {pub}")
            rows = int(cur.fetchone()[0])
        conn.commit()
        out.append(Published(pub, rows, _size(conn, pub),
                             (time.perf_counter() - t0) * 1000))
        console.print(f"  {pub:30s} {rows:>8,} rows  {_size(conn, pub)/1024:>8,.0f} kB")

    t0 = time.perf_counter()
    with conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS pub_dim_product")
        cur.execute(f"CREATE TABLE pub_dim_product AS {DIM_PRODUCT_SELECT}")
        cur.execute("SELECT count(*) FROM pub_dim_product")
        rows = int(cur.fetchone()[0])
    conn.commit()
    out.append(Published("pub_dim_product", rows, _size(conn, "pub_dim_product"),
                         (time.perf_counter() - t0) * 1000))
    console.print(f"  {'pub_dim_product':30s} {rows:>8,} rows  "
                  f"{_size(conn, 'pub_dim_product')/1024:>8,.0f} kB")
    return out


def _manifest(conn: psycopg.Connection, published: list[Published]) -> None:
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS pub_manifest (
                table_name text PRIMARY KEY, rows bigint, bytes bigint,
                published_at timestamptz NOT NULL DEFAULT clock_timestamp())""")
        cur.execute("TRUNCATE pub_manifest")
        for p in published:
            cur.execute("INSERT INTO pub_manifest (table_name, rows, bytes) "
                        "VALUES (%s,%s,%s)", (p.name, p.rows, p.bytes_))
    conn.commit()


def push(dsn: str, published: list[Published]) -> list[Published]:
    """COPY each built table from local to the target, one transaction each."""
    names = [p.name for p in published]
    with connect() as src, psycopg.connect(dsn) as dst:
        for p in published:
            t0 = time.perf_counter()
            with src.cursor() as scur:
                scur.execute("""
                    SELECT 'CREATE TABLE ' || %s || ' (' || string_agg(
                        quote_ident(column_name) || ' ' || data_type, ', '
                        ORDER BY ordinal_position) || ')'
                    FROM information_schema.columns WHERE table_name = %s""",
                    (p.name, p.name))
                ddl = scur.fetchone()[0]

            buf = io.BytesIO()
            with src.cursor() as scur, scur.copy(
                    f"COPY {p.name} TO STDOUT (FORMAT binary)") as cp:
                for chunk in cp:
                    buf.write(chunk)
            buf.seek(0)

            with dst.cursor() as dcur:
                dcur.execute(f"DROP TABLE IF EXISTS {p.name} CASCADE")
                dcur.execute(ddl)
                with dcur.copy(f"COPY {p.name} FROM STDIN (FORMAT binary)") as cp:
                    cp.write(buf.read())
            dst.commit()
            p.push_ms = (time.perf_counter() - t0) * 1000
            console.print(f"  pushed {p.name:28s} {p.rows:>8,} rows  "
                          f"{p.push_ms:>7,.0f} ms")

        with dst.cursor() as dcur:
            dcur.execute("""
                CREATE TABLE IF NOT EXISTS pub_manifest (
                    table_name text PRIMARY KEY, rows bigint, bytes bigint,
                    published_at timestamptz NOT NULL DEFAULT clock_timestamp())""")
            dcur.execute("TRUNCATE pub_manifest")
            for p in published:
                dcur.execute("INSERT INTO pub_manifest (table_name, rows, bytes) "
                             "VALUES (%s,%s,%s)", (p.name, p.rows, p.bytes_))
        dst.commit()
    console.print(f"\n  manifest written for {len(names)} tables")
    return published


def report(published: list[Published]) -> int:
    t = RichTable(title="Published aggregate tier")
    for c in ("table", "rows", "size"):
        t.add_column(c, justify="right" if c != "table" else "left")
    total_rows = total_bytes = 0
    for p in sorted(published, key=lambda x: -x.bytes_):
        t.add_row(p.name, f"{p.rows:,}", f"{p.bytes_/1024:,.0f} kB")
        total_rows += p.rows
        total_bytes += p.bytes_
    t.add_row("[bold]TOTAL", f"[bold]{total_rows:,}", f"[bold]{total_bytes/1024**2:,.1f} MB")
    console.print(t)
    return total_bytes


def run(dsn: str | None = None, local_only: bool = False) -> int:
    console.rule("[bold]rrip publish")
    with connect() as conn:
        console.print("building aggregates from the local star schema...\n")
        published = build_local(conn)
        console.print("\ncomputing causal results (statsmodels, not SQL)...\n")
        published += build_causal(conn)
        console.print("\ncopying precomputed forecasts...\n")
        published += build_forecast(conn)
        _manifest(conn, published)
    total = report(published)

    if local_only or not dsn:
        console.print("\n[yellow]local-only[/yellow]: nothing pushed. "
                      "Set RRIP_PUBLISH_DSN to publish.")
        return total

    console.print("\npushing to target...\n")
    push(dsn, published)
    return total


def build_causal(conn: psycopg.Connection) -> list[Published]:
    """Compute and store the causal results.

    These are the one part of the tier that SQL cannot build: the estimates come
    from statsmodels. They are PRECOMPUTED by design -- the hosted tier has no
    fact table to re-estimate from, which is stated in the UI rather than left
    for a user to discover.
    """
    import json

    from rrip.ai.causal import (
        build_panel,
        check_parallel_trends,
        confidence_verdict,
        estimate_did,
        household_attributes,
        naive_difference,
    )
    from rrip.publish.tables import CAUSAL_CAMPAIGNS, CAUSAL_DDL, CONTAMINATION

    t0 = time.perf_counter()
    with conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS pub_causal_results")
        cur.execute("DROP TABLE IF EXISTS pub_causal_pretrend")
        for ddl in CAUSAL_DDL:
            cur.execute(ddl)
    conn.commit()

    confounders = ["pre_spend", "pre_baskets", "coupon_offers", "has_demographics"]
    pre_rows = 0

    for cid in CAUSAL_CAMPAIGNS:
        df = build_panel(conn, cid)
        if df.empty:
            console.print(f"  [yellow]campaign {cid}: no panel data, skipped")
            continue
        attrs = household_attributes(conn, cid)
        pt = check_parallel_trends(df)
        res = estimate_did(df)
        adj = estimate_did(df, confounders, attrs)
        treated_n = int(df[df.treated == 1].household_key.nunique())
        control_n = int(df[df.treated == 0].household_key.nunique())
        contam = CONTAMINATION.get(cid, 0.0)
        verdict, warnings = confidence_verdict(pt, contam, treated_n, res)

        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO pub_causal_results VALUES
                (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (cid, treated_n, control_n, contam,
                 round(naive_difference(df), 4),
                 round(res["did_estimate"], 4), round(res["did_stderr"], 4),
                 round(res["did_pvalue"], 6), round(res["ci_low"], 4),
                 round(res["ci_high"], 4),
                 (round(adj["adjusted_estimate"], 4)
                  if adj.get("adjusted_estimate") is not None else None),
                 (round(adj["adjusted_stderr"], 4)
                  if adj.get("adjusted_stderr") is not None else None),
                 ",".join(adj.get("confounders_used", [])),
                 pt.passed, round(pt.treated_slope, 4), round(pt.control_slope, 4),
                 round(pt.interaction_pvalue, 6), pt.pre_weeks, pt.verdict,
                 verdict, json.dumps(warnings)))

            pre = df[df.post == 0].groupby(
                ["treated", "week_no"])["spend"].mean().reset_index()
            for r in pre.itertuples():
                cur.execute(
                    "INSERT INTO pub_causal_pretrend VALUES (%s,%s,%s,%s) "
                    "ON CONFLICT DO NOTHING",
                    (cid, "treated" if r.treated == 1 else "control",
                     int(r.week_no), round(float(r.spend), 3)))
                pre_rows += 1
        conn.commit()
        console.print(f"  campaign {cid:<3} did={res['did_estimate']:+8.4f}  "
                      f"naive={naive_difference(df):+8.4f}  {verdict}")

    dt = (time.perf_counter() - t0) * 1000
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM pub_causal_results")
        n = int(cur.fetchone()[0])
    return [
        Published("pub_causal_results", n, _size(conn, "pub_causal_results"), dt),
        Published("pub_causal_pretrend", pre_rows,
                  _size(conn, "pub_causal_pretrend"), 0.0),
    ]


def build_forecast(conn: psycopg.Connection) -> list[Published]:
    """Copy the precomputed forecasts into the published tier.

    NOTHING IS RECOMPUTED HERE. The rows come from the artifact that
    `rrip forecast-train` wrote and that the local API already serves, so the
    hosted tier returns numbers identical to the local one by construction.
    Rebuilding them from the panel would be a second implementation, and two
    implementations of the same forecast is one more than can be kept in
    agreement.

    Returns an empty list, with a warning, when no artifact exists. A publish
    that fails because the optional forecasting component has not been trained
    would block the whole aggregate tier over a component the rest of it does
    not depend on.
    """
    import json
    import time

    from rrip.forecast import service as FS
    from rrip.publish.tables import FORECAST_DDL

    t0 = time.perf_counter()

    try:
        store = FS.load_store(force=True)
    except FS.ForecastUnavailable as exc:
        console.print(f"  [yellow]skipped[/yellow]: {exc}")
        return []

    with conn.cursor() as cur:
        for name in ("pub_forecast", "pub_forecast_history",
                     "pub_forecast_departments", "pub_forecast_summary"):
            cur.execute(f"DROP TABLE IF EXISTS {name}")
        for ddl in FORECAST_DDL:
            cur.execute(ddl)
    conn.commit()

    next_week = store.observed_until_week + 1
    per = store.metadata["metrics"]["test"].get("per_department", {})

    payload_rows = history_rows = 0
    with conn.cursor() as cur:
        for dept in store.departments:
            for week in store.weeks:
                row = store.rows.get((dept, week))
                if row is None:
                    continue

                try:
                    payload = json.dumps(FS.forecast(dept, week))
                except FS.ForecastRequestError as exc:
                    # A refusal is published too. The hosted tier must decline
                    # for the same reason and with the same code as local --
                    # otherwise INSUFFICIENT_HISTORY becomes a 404 in
                    # production and looks like a missing department.
                    payload = json.dumps(exc.to_dict())

                cur.execute(
                    "INSERT INTO pub_forecast VALUES (%s,%s,%s,%s)",
                    (dept, week, week == next_week, payload))
                payload_rows += 1

                cur.execute(
                    "INSERT INTO pub_forecast_history VALUES "
                    "(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (dept, week, row["split"],
                     None if row["actual"] != row["actual"] else row["actual"],
                     row["prediction"], row["lower"], row["upper"],
                     row["baseline_prediction"], store.version))
                history_rows += 1

            target = store.rows.get((dept, next_week))
            cur.execute(
                "INSERT INTO pub_forecast_departments VALUES (%s,%s,%s,%s)",
                (dept,
                 float(per[dept]["wape"]) if dept in per else None,
                 bool(target["servable"]) if target else False,
                 float(target["scale_usd"]) if target else None))

        cur.execute("INSERT INTO pub_forecast_summary VALUES (%s)",
                    (json.dumps(FS.summary()),))
    conn.commit()

    dt = (time.perf_counter() - t0) * 1000
    console.print(f"  {len(store.departments)} departments, {payload_rows} "
                  f"forecast payloads, model {store.version}")
    return [
        Published("pub_forecast", payload_rows, _size(conn, "pub_forecast"), dt),
        Published("pub_forecast_history", history_rows,
                  _size(conn, "pub_forecast_history"), 0.0),
        Published("pub_forecast_departments", len(store.departments),
                  _size(conn, "pub_forecast_departments"), 0.0),
        Published("pub_forecast_summary", 1,
                  _size(conn, "pub_forecast_summary"), 0.0),
    ]
