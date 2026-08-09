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
        _manifest(conn, published)
    total = report(published)

    if local_only or not dsn:
        console.print("\n[yellow]local-only[/yellow]: nothing pushed. "
                      "Set RRIP_PUBLISH_DSN to publish.")
        return total

    console.print("\npushing to target...\n")
    push(dsn, published)
    return total
