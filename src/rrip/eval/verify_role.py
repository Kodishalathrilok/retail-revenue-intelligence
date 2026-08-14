"""Verify the read-only role is a real boundary, by trying to break it.

WHY THIS EXISTS

sql/ddl/60_readonly_role.sql claims the database role is the security boundary
and the SQL gates are defence in depth. That claim is worth exactly as much as
the evidence for it. A DDL script in the repository proves that someone wrote a
GRANT, not that a connection is actually constrained -- the script may never
have been run, may have been run against a different database, or may have been
partially applied because a REVOKE on a superuser-owned function was skipped.

So this connects AS the role and attempts each forbidden thing. A probe passes
when the database refuses it.

THE DISTINCTION THIS PRESERVES

Every probe records WHO refused:

  database   -- Postgres raised insufficient_privilege. This is the boundary.
  gates      -- the application validator rejected it before it was sent.
  nobody     -- it succeeded. If the probe was a write, that is a breach.

That separation is the whole point. A run where the gates block everything and
the database would have allowed it is a system with one layer, not two, and it
looks identical from the outside until the gates have a bypass -- which they
have had once already (`query_to_xml`). The probes therefore run RAW SQL,
bypassing the validator entirely, because the question is what the database
does, not what the application does.

WHEN THE ROLE IS NOT DEPLOYED

Reporting "unverified" is a result. Reporting a pass because nothing was checked
is the failure this module exists to prevent, so a missing role is a loud
`status: NOT_DEPLOYED` and a non-zero exit, never a silent skip.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

import psycopg

from rrip.config import PROJECT_ROOT, settings

ROLE = "rrip_ro"

# Each probe is (id, description, sql, why_it_matters).
#
# All are expected to FAIL. A probe that succeeds is reported as a breach, with
# the exception of the two read probes at the end, which are expected to work --
# a role that cannot read is not a working configuration either, and a verifier
# that only checks denials would pass on a role with no grants at all.
WRITE_PROBES: tuple[tuple[str, str, str], ...] = (
    ("insert", "INSERT INTO dim_store",
     "INSERT INTO dim_store (store_id, has_promo_coverage) VALUES (-999, false)"),
    ("update", "UPDATE dim_store",
     "UPDATE dim_store SET has_promo_coverage = false WHERE store_id = -999"),
    ("delete", "DELETE FROM dim_store",
     "DELETE FROM dim_store WHERE store_id = -999"),
    ("truncate", "TRUNCATE dim_store", "TRUNCATE dim_store"),
    ("create_table", "CREATE TABLE in public",
     "CREATE TABLE rrip_ro_probe (id int)"),
    ("create_function", "CREATE FUNCTION in public",
     "CREATE FUNCTION rrip_ro_probe_fn() RETURNS int AS $$ SELECT 1 $$ LANGUAGE sql"),
    ("drop", "DROP a table", "DROP TABLE IF EXISTS dim_store"),
    ("alter_role", "ALTER own role to superuser", f"ALTER ROLE {ROLE} SUPERUSER"),
    # The bypass that defeated the text gates, in both directions.
    #
    # WRITE: refused with 0A000, and a superuser gets the same error, so this
    # one is stopped by query_to_xml being STABLE -- not by the role. Recorded
    # honestly because the repo previously claimed the role was what stopped it.
    ("query_to_xml_delete", "query_to_xml executing a DELETE",
     "SELECT query_to_xml('DELETE FROM dim_store WHERE store_id = -999', "
     "true, true, '')"),
    # READ: this one DOES execute its argument as the calling role. It is the
    # probe that actually demonstrates the boundary, because the only thing
    # standing between it and pg_authid is the role's privileges.
    ("query_to_xml_read_forbidden", "query_to_xml reading pg_authid",
     "SELECT query_to_xml('SELECT rolname, rolpassword FROM pg_authid', "
     "true, true, '')"),
    ("read_file", "pg_read_file on the server filesystem",
     "SELECT pg_read_file('postgresql.conf')"),
    ("password_hashes", "read credential material from pg_authid",
     "SELECT rolname, rolpassword FROM pg_authid LIMIT 1"),
)

READ_PROBES: tuple[tuple[str, str, str], ...] = (
    ("select_fact", "SELECT from a fact table",
     "SELECT count(*) FROM fact_transactions"),
    ("select_dim", "SELECT from a dimension", "SELECT count(*) FROM dim_store"),
)


@dataclass
class Probe:
    id: str
    description: str
    expected: str          # "denied" or "allowed"
    outcome: str           # "denied" or "allowed"
    passed: bool
    refused_by: str        # database | nobody | n/a
    sqlstate: str | None = None
    detail: str = ""


# SQLSTATEs that mean "the server refused this operation", as opposed to "the
# statement was malformed" or "the object is missing". Only these count as a
# denial; a probe that fails because a table does not exist has demonstrated
# nothing about privileges, and scoring it as a pass would be the verifier
# lying about its own coverage.
#
#   42501  insufficient_privilege -- the role boundary.
#   0A000  feature_not_supported  -- reached on the query_to_xml write path,
#          because query_to_xml is declared STABLE and Postgres refuses a DELETE
#          inside a non-volatile function. Measured, not assumed: a superuser
#          gets the identical error, so this refusal is not the role's doing.
DATABASE_REFUSALS = {"42501", "0A000"}


def _classify_error(exc: Exception) -> tuple[str, str | None]:
    """Who refused, and with what SQLSTATE."""
    sqlstate = getattr(exc, "sqlstate", None)
    if sqlstate in DATABASE_REFUSALS:
        return "database", sqlstate
    return "error", sqlstate


def _probe_grant_has_no_effect(conn) -> Probe:
    """GRANT is special: failing to have privilege is a WARNING, not an ERROR.

    Postgres accepts `GRANT INSERT ON dim_store TO rrip_ro` from a role with no
    grant option, completes the statement, and grants nothing -- it emits
    'no privileges were granted'. The first version of this probe read the
    statement outcome and reported BREACH on a boundary that was intact.

    So the assertion is on the EFFECT: the privilege must be absent before, and
    still absent after.
    """
    check = "SELECT has_table_privilege(current_user, 'dim_store', 'INSERT')"
    try:
        with conn.cursor() as cur:
            cur.execute(check)
            before = cur.fetchone()[0]
            cur.execute(f"GRANT INSERT ON dim_store TO {ROLE}")
            cur.execute(check)
            after = cur.fetchone()[0]
        conn.rollback()
    except psycopg.Error as exc:
        conn.rollback()
        who, sqlstate = _classify_error(exc)
        return Probe("grant", "GRANT INSERT to self", "denied", "denied",
                     passed=who == "database", refused_by=who, sqlstate=sqlstate,
                     detail=str(exc).strip().split("\n")[0][:160])

    granted = bool(after) and not before
    return Probe(
        "grant", "GRANT INSERT to self", "denied",
        "allowed" if granted else "denied", passed=not granted,
        refused_by="database" if not granted else "nobody",
        detail=(f"has_table_privilege before={before} after={after}; the "
                "statement completes with a warning but confers nothing"))


def _run_probe(conn, pid: str, desc: str, sql: str, expect_denied: bool) -> Probe:
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            try:
                cur.fetchall()
            except psycopg.ProgrammingError:
                pass            # a statement with no result set
        conn.rollback()
        return Probe(pid, desc, "denied" if expect_denied else "allowed", "allowed",
                     passed=not expect_denied, refused_by="nobody",
                     detail="statement completed")
    except psycopg.Error as exc:
        conn.rollback()
        who, sqlstate = _classify_error(exc)
        # For a write probe, only a privilege error is a pass. An undefined
        # table or a syntax error means the probe never reached the check.
        passed = expect_denied and who == "database"
        return Probe(pid, desc, "denied" if expect_denied else "allowed", "denied",
                     passed=passed, refused_by=who, sqlstate=sqlstate,
                     detail=str(exc).strip().split("\n")[0][:160])


def verify(dsn: str) -> dict:
    """Connect as the role and attempt every forbidden operation."""
    started = datetime.now(UTC)
    probes: list[Probe] = []

    try:
        conn = psycopg.connect(dsn, connect_timeout=10, autocommit=False)
    except psycopg.OperationalError as exc:
        return {
            "status": "NOT_DEPLOYED",
            "role": ROLE,
            "checked_at": started.isoformat(),
            "reason": (f"could not connect as {ROLE}: {str(exc).strip()[:200]}"),
            "remedy": ("Run sql/ddl/60_readonly_role.sql against this database "
                       "with -v ro_password='...', then set RRIP_RO_DSN."),
            "probes": [],
        }

    with conn:
        with conn.cursor() as cur:
            cur.execute("SELECT current_user, current_database(), version()")
            user, db, version = cur.fetchone()
            cur.execute(
                "SELECT rolsuper, rolcreatedb, rolcreaterole, rolinherit "
                "FROM pg_roles WHERE rolname = current_user")
            attrs = cur.fetchone()
            cur.execute(
                "SELECT has_schema_privilege(current_user, 'public', 'CREATE')")
            can_create = cur.fetchone()[0]
        conn.rollback()

        for pid, desc, sql in WRITE_PROBES:
            probes.append(_run_probe(conn, pid, desc, sql, expect_denied=True))
        probes.append(_probe_grant_has_no_effect(conn))
        for pid, desc, sql in READ_PROBES:
            probes.append(_run_probe(conn, pid, desc, sql, expect_denied=False))

    breaches = [p for p in probes if p.expected == "denied" and p.outcome == "allowed"]
    inconclusive = [p for p in probes
                    if p.expected == "denied" and p.refused_by == "error"]
    reads_ok = all(p.passed for p in probes if p.expected == "allowed")

    if breaches:
        status = "BREACH"
    elif not reads_ok:
        status = "MISCONFIGURED"      # denies writes but cannot read either
    elif inconclusive:
        status = "PARTIAL"
    else:
        status = "VERIFIED"

    return {
        "status": status,
        "role": ROLE,
        "connected_as": user,
        "database": db,
        "server": version.split(",")[0],
        "checked_at": started.isoformat(),
        "role_attributes": {
            "rolsuper": attrs[0], "rolcreatedb": attrs[1],
            "rolcreaterole": attrs[2], "rolinherit": attrs[3],
            "can_create_in_public": can_create,
        },
        "n_probes": len(probes),
        "n_passed": sum(p.passed for p in probes),
        "n_breaches": len(breaches),
        "n_inconclusive": len(inconclusive),
        "refused_by": {
            "database": sum(1 for p in probes if p.refused_by == "database"),
            "nobody": sum(1 for p in probes if p.refused_by == "nobody"),
        },
        "probes": [asdict(p) for p in probes],
    }


def ro_dsn() -> str | None:
    """DSN for the read-only role.

    Explicit RRIP_RO_DSN wins. Otherwise the local connection is rewritten to
    use the role, which only works if RRIP_RO_PASSWORD is set -- there is no
    default password, because a verifier that silently tries a guessed one
    reports NOT_DEPLOYED for the wrong reason.
    """
    import os

    explicit = os.getenv("RRIP_RO_DSN")
    if explicit:
        return explicit
    password = os.getenv("RRIP_RO_PASSWORD")
    if not password:
        return None
    return (f"host={settings.pg_host} port={settings.pg_port} "
            f"user={ROLE} password={password} dbname={settings.pg_database}")


def run(dsn: str | None = None) -> dict:
    from rich.console import Console
    from rich.table import Table

    console = Console()
    target = dsn or ro_dsn()
    if not target:
        report = {
            "status": "NOT_CONFIGURED",
            "role": ROLE,
            "checked_at": datetime.now(UTC).isoformat(),
            "reason": ("No read-only DSN configured. Set RRIP_RO_DSN, or "
                       "RRIP_RO_PASSWORD to build one from the local settings."),
            "probes": [],
        }
    else:
        report = verify(target)

    out = PROJECT_ROOT / "reports" / "eval"
    out.mkdir(parents=True, exist_ok=True)
    (out / "readonly-role.json").write_text(
        json.dumps(report, indent=2, default=str), encoding="utf-8")

    colour = {"VERIFIED": "green", "PARTIAL": "yellow", "BREACH": "red",
              "MISCONFIGURED": "red", "NOT_DEPLOYED": "yellow",
              "NOT_CONFIGURED": "yellow"}.get(report["status"], "white")
    console.print(f"\n[bold]Read-only role[/bold] {ROLE}: "
                  f"[{colour}]{report['status']}[/{colour}]")
    if report.get("reason"):
        console.print(f"  [dim]{report['reason']}[/dim]")
    if report.get("remedy"):
        console.print(f"  [dim]{report['remedy']}[/dim]")

    if report["probes"]:
        t = Table(show_header=True, header_style="bold")
        t.add_column("Probe")
        t.add_column("Expected")
        t.add_column("Outcome")
        t.add_column("Refused by")
        t.add_column("", justify="center")
        for p in report["probes"]:
            t.add_row(p["description"], p["expected"], p["outcome"],
                      p["refused_by"],
                      "[green]OK[/green]" if p["passed"] else "[red]FAIL[/red]")
        console.print(t)
        console.print(f"[dim]{report['n_passed']}/{report['n_probes']} probes passed; "
                      f"{report['refused_by']['database']} refusals came from the "
                      f"database[/dim]")
    return report
