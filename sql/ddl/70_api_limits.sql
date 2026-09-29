-- Counters for the AI endpoints' abuse protection, and the only role that may
-- write them. Read by src/rrip/api/limits.py.
--
-- WHY A SEPARATE ROLE. The API pool connects as rrip_ro, which holds SELECT and
-- nothing else, and that is what bounds the NL->SQL read bypass recorded in
-- 60_readonly_role.sql. Counters need writes. Granting them to rrip_ro would put
-- a writable table behind the connection that executes model-proposed SQL, so
-- the limiter connects as rrip_limiter, which can touch these two tables and
-- nothing else -- and rrip_ro is explicitly denied them.
--
-- WHY POSTGRES. The API runs as serverless functions; each instance has its own
-- memory, so in-process counters would give every instance its own allowance.
--
-- Requires superuser (or the database owner on managed Postgres). Run once per
-- database, AFTER 60_readonly_role.sql:
--
--   local:      psql -U postgres -d rrip -v limiter_password='...' -f sql/ddl/70_api_limits.sql
--   published:  psql "$RRIP_PUBLISH_DSN" -v limiter_password='...' -f sql/ddl/70_api_limits.sql
--
-- Then set RRIP_LIMITER_DSN to a DSN for rrip_limiter. Idempotent.

\if :{?limiter_password}
\else
  \echo '  limiter_password not set. Re-run with:  -v limiter_password=''<password>'''
  \quit
\endif

-- One row per client bucket per window. bucket_key is a hash of the client
-- address, never the address itself.
CREATE TABLE IF NOT EXISTS api_rate_limit (
    bucket_key    text        NOT NULL,
    window_start  timestamptz NOT NULL,
    request_count integer     NOT NULL CHECK (request_count > 0),
    PRIMARY KEY (bucket_key, window_start)
);

-- The primary key serves the per-request upsert. This serves the expiry
-- delete, which filters on window_start alone.
CREATE INDEX IF NOT EXISTS api_rate_limit_window_idx
    ON api_rate_limit (window_start);

-- One row per UTC day: model calls made (and refused) by the whole deployment.
CREATE TABLE IF NOT EXISTS llm_usage_daily (
    usage_date date    PRIMARY KEY,
    calls      integer NOT NULL CHECK (calls > 0)
);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rrip_limiter') THEN
        CREATE ROLE rrip_limiter LOGIN NOINHERIT
            NOCREATEDB NOCREATEROLE NOSUPERUSER NOREPLICATION;
    END IF;
END
$$;

ALTER ROLE rrip_limiter PASSWORD :'limiter_password';

-- Every limiter statement is a single-row upsert or a small indexed delete. A
-- limiter query that runs long is a broken limiter; fail it fast, and the API
-- refuses the request rather than queueing behind it.
ALTER ROLE rrip_limiter SET statement_timeout = '2s';

DO $$
BEGIN
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO rrip_limiter', current_database());
END
$$;

GRANT USAGE ON SCHEMA public TO rrip_limiter;

-- Exactly what limits.py does, and nothing more: upsert and expire buckets,
-- upsert the day's count. No TRUNCATE, no DDL, nothing on any other table.
GRANT SELECT, INSERT, UPDATE, DELETE ON api_rate_limit  TO rrip_limiter;
GRANT SELECT, INSERT, UPDATE         ON llm_usage_daily TO rrip_limiter;

-- 60_readonly_role.sql grants rrip_ro SELECT on every table the owner creates,
-- these included. Take it back: the counters are not data the NL->SQL
-- endpoint should read, and PUBLIC gets nothing either.
REVOKE ALL ON api_rate_limit, llm_usage_daily FROM PUBLIC;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rrip_ro') THEN
        REVOKE ALL ON api_rate_limit, llm_usage_daily FROM rrip_ro;
    END IF;
END
$$;

-- Verify, so a partial run is visible rather than assumed successful.
SELECT
    t.tbl,
    has_table_privilege('rrip_limiter', t.tbl, 'INSERT') AS limiter_can_write,
    CASE WHEN EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rrip_ro')
         THEN has_table_privilege('rrip_ro', t.tbl, 'SELECT') END AS ro_can_read
FROM (VALUES ('api_rate_limit'), ('llm_usage_daily')) AS t(tbl);
