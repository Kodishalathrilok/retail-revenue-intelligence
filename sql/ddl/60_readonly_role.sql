-- Read-only role for the API, and the real security boundary for NL->SQL.
--
-- The SQL validator in src/rrip/ai/nl2sql.py rejects statements before they
-- reach the database. That is defence in depth, not a boundary: it is a
-- program reasoning about SQL text, and a bypass in it is a bypass of
-- everything. `SELECT query_to_xml('DELETE FROM ...', ...)` passed every text
-- gate the validator had -- the payload is a string literal, and the validator
-- strips string literals before scanning keywords, so the scan could not see
-- it. EXPLAIN did not catch it either, because EXPLAIN without ANALYZE never
-- executes a function body.
--
-- What that bypass actually does under this role was MEASURED rather than
-- assumed, and the original claim here -- "it still runs, it just cannot delete
-- anything, because the role holds no DELETE privilege" -- was wrong in a way
-- worth recording. On PostgreSQL 16:
--
--   query_to_xml('DELETE FROM ...')  -> ERROR 0A000, "DELETE is not allowed in
--       a non-volatile function". A SUPERUSER gets the identical error, so the
--       role is not what stops this. query_to_xml is declared STABLE, and that
--       is what stops it.
--
--   query_to_xml('SELECT ...')       -> RUNS, as the calling role. Reading
--       dim_store through it succeeds; reading pg_authid through it fails with
--       42501. So the bypass is real, it is a READ bypass, and what bounds its
--       reach is exactly this role's grants.
--
-- The boundary argument survives, but it is about reach, not about writes: the
-- gates decide what SQL is sent, and the role decides what any SQL that gets
-- through is allowed to touch. src/rrip/eval/verify_role.py asserts both, and
-- reports/eval/readonly-role.json records the outcome per probe.
--
-- Requires superuser. Run once per database, AFTER the tables exist:
--
--   local:      psql -U postgres -d rrip -v ro_password='...' -f sql/ddl/60_readonly_role.sql
--   published:  psql "$RRIP_PUBLISH_DSN"  -v ro_password='...' -f sql/ddl/60_readonly_role.sql
--
-- Then point the API at it via RRIP_PG_DSN (or RRIP_PG_USER/RRIP_PG_PASSWORD).
-- The loader and `rrip publish` keep using the owning role -- they write.

\if :{?ro_password}
\else
  \echo '  ro_password not set. Re-run with:  -v ro_password=''<password>'''
  \quit
\endif

-- LOGIN but nothing else. No CREATEDB, no CREATEROLE, no BYPASSRLS, and
-- NOINHERIT so it cannot pick up privileges from a group it is later added to.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rrip_ro') THEN
        CREATE ROLE rrip_ro LOGIN NOINHERIT
            NOCREATEDB NOCREATEROLE NOSUPERUSER NOREPLICATION;
    END IF;
END
$$;

ALTER ROLE rrip_ro PASSWORD :'ro_password';

-- Connect and look, nothing more. GRANT takes a database identifier, not an
-- expression, so the name is interpolated rather than passed as a function.
DO $$
BEGIN
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO rrip_ro', current_database());
END
$$;

GRANT USAGE ON SCHEMA public TO rrip_ro;

-- Explicitly remove object creation. Postgres 15+ drops the old implicit
-- PUBLIC CREATE on schema public, but 13/14 still grant it -- and a role that
-- can CREATE can make a table, which is a write.
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
REVOKE CREATE ON SCHEMA public FROM rrip_ro;

-- SELECT on what exists now...
GRANT SELECT ON ALL TABLES IN SCHEMA public TO rrip_ro;
GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO rrip_ro;

-- ...and on what the loader or `rrip publish` creates later. Without this, a
-- freshly published pub_* table is invisible to the API and the failure looks
-- like a missing table rather than a missing grant.
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT ON TABLES TO rrip_ro;

-- Deny the write paths explicitly. ALL TABLES above never granted them, but an
-- object owner may have granted to PUBLIC, which rrip_ro inherits regardless of
-- NOINHERIT because PUBLIC is not a group.
REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER
    ON ALL TABLES IN SCHEMA public FROM rrip_ro;

-- Server-side file reach. Already superuser-only by default; these restate it
-- so the restriction survives someone granting EXECUTE to PUBLIC later.
--
-- Skipped rather than fatal when the current role does not own them: on managed
-- Postgres (Neon, RDS) you are not superuser, so the REVOKE raises
-- insufficient_privilege. That is not a failure of this script -- the same
-- managed platform is the reason the functions are unreachable anyway.
DO $$
DECLARE
    fn text;
BEGIN
    FOREACH fn IN ARRAY ARRAY['pg_read_file(text)', 'pg_read_binary_file(text)',
                              'pg_ls_dir(text)']
    LOOP
        BEGIN
            EXECUTE format('REVOKE EXECUTE ON FUNCTION %s FROM PUBLIC', fn);
        EXCEPTION
            WHEN insufficient_privilege OR undefined_function THEN
                RAISE NOTICE 'skipped REVOKE on %: %', fn, SQLERRM;
        END;
    END LOOP;
END
$$;

-- Verify, so a partial run is visible rather than assumed successful.
SELECT
    r.rolname,
    r.rolsuper, r.rolcreatedb, r.rolcreaterole, r.rolinherit,
    has_schema_privilege('rrip_ro', 'public', 'CREATE') AS can_create,
    (SELECT count(*) FROM information_schema.table_privileges
      WHERE grantee = 'rrip_ro'
        AND privilege_type IN ('INSERT','UPDATE','DELETE','TRUNCATE')) AS write_grants
FROM pg_roles r
WHERE r.rolname = 'rrip_ro';
