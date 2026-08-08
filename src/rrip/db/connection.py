"""Database connections.

Raw psycopg is used rather than SQLAlchemy for the load path: COPY streams file
bytes straight into the server with no Python-side parsing, which is by a wide
margin the fastest way to move 36.8M rows.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import psycopg

from rrip.config import PROJECT_ROOT, settings


def conninfo() -> str:
    return (
        f"host={settings.pg_host} port={settings.pg_port} "
        f"user={settings.pg_user} password={settings.pg_password} "
        f"dbname={settings.pg_database}"
    )


@contextmanager
def connect(autocommit: bool = False) -> Iterator[psycopg.Connection]:
    conn = psycopg.connect(conninfo(), autocommit=autocommit)
    try:
        yield conn
    finally:
        conn.close()


def sql_statements(path: str | Path) -> Iterator[str]:
    """Split a .sql file into individual statements.

    Postgres rejects multi-statement execution over the extended protocol, and
    ALTER SYSTEM cannot run inside a transaction block, so files are applied one
    statement at a time.

    This scans character by character rather than using regexes. A regex that
    strips `--` comments also strips `--` occurring inside a string literal,
    which silently breaks the quoting -- exactly how a COMMENT ON body
    containing a double dash took down the first load run.
    """
    p = Path(path)
    src = (p if p.is_absolute() else PROJECT_ROOT / p).read_text(encoding="utf-8")

    out: list[str] = []
    i, n = 0, len(src)
    while i < n:
        ch = src[i]

        # line comment -- only outside a literal, which is the whole point
        if ch == "-" and src.startswith("--", i):
            j = src.find("\n", i)
            i = n if j == -1 else j  # keep the newline as whitespace
            continue

        # block comment
        if ch == "/" and src.startswith("/*", i):
            j = src.find("*/", i + 2)
            i = n if j == -1 else j + 2
            continue

        # single-quoted literal, '' escapes an embedded quote
        if ch == "'":
            j = i + 1
            while j < n:
                if src[j] == "'":
                    if j + 1 < n and src[j + 1] == "'":
                        j += 2
                        continue
                    break
                j += 1
            out.append(src[i:j + 1])
            i = j + 1
            continue

        # dollar-quoted block, e.g. $$ ... $$ or $tag$ ... $tag$
        if ch == "$":
            m = re.match(r"\$[A-Za-z_]*\$", src[i:])
            if m:
                tag = m.group(0)
                j = src.find(tag, i + len(tag))
                j = n if j == -1 else j + len(tag)
                out.append(src[i:j])
                i = j
                continue

        if ch == ";":
            stmt = "".join(out).strip()
            if stmt:
                yield stmt
            out = []
            i += 1
            continue

        out.append(ch)
        i += 1

    tail = "".join(out).strip()
    if tail:
        yield tail


def apply_sql_file(conn: psycopg.Connection, path: str | Path) -> int:
    n = 0
    with conn.cursor() as cur:
        for stmt in sql_statements(path):
            cur.execute(stmt)  # type: ignore[arg-type]
            n += 1
    return n
