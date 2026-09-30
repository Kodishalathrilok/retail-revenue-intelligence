"""Regression tests for the read-only role and for the verifier itself.

Every test here exists because the first verification run got something wrong.
Skipped when no read-only DSN is configured, because a test that silently passes
without connecting would recreate the exact failure it is guarding against.
"""

from __future__ import annotations

import os

import psycopg
import pytest

from rrip.eval.verify_role import DATABASE_REFUSALS, ro_dsn, verify

DSN = ro_dsn()

# In the CI job that creates the role, a missing DSN must fail, not skip.
if DSN is None and os.getenv("RRIP_REQUIRE_RO_DB"):
    raise RuntimeError("RRIP_REQUIRE_RO_DB is set but RRIP_PG_READONLY_DSN is not")

requires_role = pytest.mark.skipif(
    DSN is None,
    reason="no read-only DSN; set RRIP_PG_READONLY_DSN after running "
           "sql/ddl/60_readonly_role.sql")


@pytest.fixture(scope="module")
def report():
    return verify(DSN)


@requires_role
def test_role_is_verified(report):
    assert report["status"] == "VERIFIED", (
        f"{report['status']}: "
        f"{[p['id'] for p in report['probes'] if not p['passed']]}")


@requires_role
def test_no_write_probe_succeeds(report):
    breaches = [p["id"] for p in report["probes"]
                if p["expected"] == "denied" and p["outcome"] == "allowed"]
    assert not breaches, f"write path open: {breaches}"


@requires_role
def test_role_holds_no_dangerous_attributes(report):
    a = report["role_attributes"]
    assert not a["rolsuper"]
    assert not a["rolcreatedb"]
    assert not a["rolcreaterole"]
    assert not a["can_create_in_public"]


@requires_role
def test_role_can_still_read(report):
    """A role that denies everything is not a working configuration.

    Without this, the verifier would report VERIFIED for a role with no grants
    at all -- maximally secure and completely useless.
    """
    reads = [p for p in report["probes"] if p["expected"] == "allowed"]
    assert reads and all(p["passed"] for p in reads)


@requires_role
def test_grant_to_self_confers_nothing():
    """REGRESSION: the first probe read the statement outcome, not the effect.

    `GRANT INSERT ON dim_store TO rrip_ro` executed by rrip_ro COMPLETES -- with
    a warning, granting nothing. Reading psycopg's "no exception" as success
    reported BREACH on an intact boundary.
    """
    with psycopg.connect(DSN, connect_timeout=10) as conn, conn.cursor() as cur:
        cur.execute("SELECT has_table_privilege(current_user,'dim_store','INSERT')")
        assert cur.fetchone()[0] is False
        cur.execute("GRANT INSERT ON dim_store TO rrip_ro")
        cur.execute("SELECT has_table_privilege(current_user,'dim_store','INSERT')")
        assert cur.fetchone()[0] is False, "GRANT to self actually conferred INSERT"
        conn.rollback()


@requires_role
def test_query_to_xml_write_is_blocked_by_volatility_not_privilege():
    """REGRESSION, and a correction to the project's own claim.

    60_readonly_role.sql used to say this payload "still runs, it just cannot
    delete anything, because the role holds no DELETE privilege". It does not
    run: query_to_xml is STABLE, so Postgres refuses a DELETE inside it with
    0A000 -- for a superuser too. The role is not the control here, and the docs
    now say so.
    """
    with psycopg.connect(DSN, connect_timeout=10) as conn, conn.cursor() as cur:
        with pytest.raises(psycopg.Error) as exc:
            cur.execute("SELECT query_to_xml('DELETE FROM dim_store', true, true, '')")
        assert exc.value.sqlstate == "0A000"
        conn.rollback()


@requires_role
def test_query_to_xml_read_executes_but_is_bounded_by_the_role():
    """The bypass is real for reads, and the role is what contains it.

    This is the probe that actually demonstrates the boundary: the same function
    that happily reads a granted table is refused on pg_authid, by privilege.
    """
    with psycopg.connect(DSN, connect_timeout=10) as conn, conn.cursor() as cur:
        cur.execute("SELECT query_to_xml('SELECT count(*) FROM dim_store',"
                    " true, true, '')::text")
        assert "<count>" in cur.fetchone()[0]

        with pytest.raises(psycopg.Error) as exc:
            cur.execute("SELECT query_to_xml('SELECT rolname FROM pg_authid',"
                        " true, true, '')")
        assert exc.value.sqlstate == "42501"
        conn.rollback()


def test_missing_dsn_reports_not_configured_rather_than_passing():
    """A verifier that cannot connect must not look like a clean bill of health."""
    from rrip.eval.verify_role import verify as _verify

    r = _verify("host=127.0.0.1 port=1 user=nobody dbname=nothing connect_timeout=1")
    assert r["status"] == "NOT_DEPLOYED"
    assert r["probes"] == []


def test_only_refusal_sqlstates_count_as_denials():
    """42P01 (undefined table) must never be scored as a privilege denial."""
    assert "42501" in DATABASE_REFUSALS
    assert "0A000" in DATABASE_REFUSALS
    assert "42P01" not in DATABASE_REFUSALS


@requires_role
def test_role_carries_the_statement_timeout_default(report):
    td = report["statement_timeout_default"]
    assert td["ok"], f"rrip_ro statement_timeout default is {td['observed_ms']} ms"


def test_role_timeout_in_ddl_matches_the_api_ceiling():
    # No database needed: the DDL literal and the API constant must not drift.
    import re
    from pathlib import Path

    from rrip.api.db import MAX_TIMEOUT_MS

    ddl = (Path(__file__).parents[1] / "sql/ddl/60_readonly_role.sql").read_text()
    m = re.search(r"ALTER ROLE rrip_ro SET statement_timeout = '(\d+)s'", ddl)
    assert m, "60_readonly_role.sql no longer sets a statement_timeout default"
    assert int(m.group(1)) * 1000 == MAX_TIMEOUT_MS


# --- one DSN contract: what is verified is what serves ---------------------------

@requires_role
def test_the_api_pool_connects_as_the_verified_read_only_role():
    """Not "the variable is set": connect with the API's own conninfo() and ask
    the database who we are and what we may write."""
    from rrip.api.db import conninfo

    assert conninfo() == DSN
    with psycopg.connect(conninfo()) as conn:
        user = conn.execute("SELECT current_user").fetchone()[0]
        writable = conn.execute(
            "SELECT count(*) FROM information_schema.table_privileges "
            "WHERE grantee = current_user "
            "AND privilege_type IN ('INSERT','UPDATE','DELETE','TRUNCATE')").fetchone()[0]
        superuser = conn.execute(
            "SELECT rolsuper FROM pg_roles WHERE rolname = current_user").fetchone()[0]
    assert user == "rrip_ro"
    assert writable == 0 and not superuser


def test_published_tier_refuses_to_fall_back_to_the_owner(monkeypatch):
    from rrip.api import db
    from rrip.config import settings

    monkeypatch.setattr(settings, "tier", "published")
    monkeypatch.setattr(settings, "pg_readonly_dsn", "")
    monkeypatch.setattr(settings, "retired_ro_dsn", "")
    monkeypatch.setattr(settings, "retired_ro_password", "")
    with pytest.raises(db.ReadOnlyRoleMisconfigured):
        db.conninfo()


def test_local_tier_still_falls_back_for_a_fresh_clone(monkeypatch):
    from rrip.api import db
    from rrip.config import settings

    monkeypatch.setattr(settings, "tier", "local")
    monkeypatch.setattr(settings, "pg_readonly_dsn", "")
    monkeypatch.setattr(settings, "retired_ro_dsn", "")
    monkeypatch.setattr(settings, "retired_ro_password", "")
    monkeypatch.setattr(settings, "pg_dsn", "host=owner-fallback")
    assert db.conninfo() == "host=owner-fallback"


@pytest.mark.parametrize("retired", ["retired_ro_dsn", "retired_ro_password"])
def test_retired_variable_names_fail_loudly(monkeypatch, retired):
    from rrip.api import db
    from rrip.config import settings

    monkeypatch.setattr(settings, retired, "anything")
    with pytest.raises(db.ReadOnlyRoleMisconfigured):
        db.readonly_dsn()


def test_verify_role_and_the_api_resolve_the_same_dsn(monkeypatch):
    from rrip.api import db
    from rrip.config import settings

    monkeypatch.setattr(settings, "retired_ro_dsn", "")
    monkeypatch.setattr(settings, "retired_ro_password", "")
    monkeypatch.setattr(settings, "pg_readonly_dsn", "postgresql://rrip_ro@h/db")
    assert ro_dsn() == db.conninfo() == "postgresql://rrip_ro@h/db"
