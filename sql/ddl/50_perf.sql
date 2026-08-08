-- Benchmark measurement storage.
--
-- docs/performance.md is generated from these tables rather than transcribed by
-- hand. That is the mechanical guard against a figure being inferred instead of
-- measured: every number in the document traces to a row here.

CREATE EXTENSION IF NOT EXISTS pg_prewarm;

CREATE TABLE IF NOT EXISTS perf_run (
    run_id       BIGSERIAL   PRIMARY KEY,
    variant      TEXT        NOT NULL,     -- 'before' | 'after'
    label        TEXT,
    started_at   TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    finished_at  TIMESTAMPTZ,
    pg_version   TEXT,
    on_ac_power  BOOLEAN,
    settings     JSONB,                    -- pg_settings snapshot

    CONSTRAINT perf_run_variant CHECK (variant IN ('before', 'after'))
);

CREATE TABLE IF NOT EXISTS perf_measurement (
    id            BIGSERIAL   PRIMARY KEY,
    run_id        BIGINT      NOT NULL REFERENCES perf_run (run_id) ON DELETE CASCADE,
    query_name    TEXT        NOT NULL,

    -- Edit detection. If a query changes between the before and after runs the
    -- comparison is invalid; storing the text and its hash makes that
    -- detectable rather than assumed.
    query_sql     TEXT        NOT NULL,
    query_sha256  TEXT        NOT NULL,

    -- Structural fingerprint of the plan: node types, relations, index names,
    -- join strategies. Excludes costs and timings, so it changes only when the
    -- plan shape changes -- which is what an optimization is supposed to do.
    plan_hash     TEXT,

    -- 'cold_shared_buffers' means shared_buffers was cleared by a service
    -- restart. The Windows file cache is NOT cleared -- there is no supported
    -- mechanism -- so the OS cache stays warm. Labelled precisely rather than
    -- called "cold".
    cache_state   TEXT        NOT NULL,
    rep           INT         NOT NULL,

    planning_ms   NUMERIC(12,3),
    execution_ms  NUMERIC(12,3),
    rows_returned BIGINT,

    shared_hit     BIGINT,
    shared_read    BIGINT,
    shared_dirtied BIGINT,
    shared_written BIGINT,
    temp_read      BIGINT,
    temp_written   BIGINT,
    io_read_ms     NUMERIC(12,3),
    io_write_ms    NUMERIC(12,3),

    -- Checkpoint isolation: bgwriter stats are reset immediately before each
    -- measurement window and read immediately after, so these are per-window
    -- counts rather than deltas against a cumulative baseline.
    checkpoints_timed INT,
    checkpoints_req   INT,
    wal_bytes         BIGINT,

    -- A window with a requested checkpoint in it is not a clean measurement.
    -- Recorded and excluded rather than silently dropped.
    discarded      BOOLEAN NOT NULL DEFAULT false,
    discard_reason TEXT,

    plan          JSONB,
    measured_at   TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),

    CONSTRAINT perf_cache_state CHECK (cache_state IN ('cold_shared_buffers', 'warm'))
);

CREATE INDEX IF NOT EXISTS ix_perf_meas_run   ON perf_measurement (run_id, query_name);
CREATE INDEX IF NOT EXISTS ix_perf_meas_query ON perf_measurement (query_name, cache_state);

-- Materialized view refresh cost (Q5). A matview that makes a query fast is
-- unremarkable; whether the total system is better depends on refresh cost
-- against query frequency, which is what this records.
CREATE TABLE IF NOT EXISTS perf_refresh (
    id           BIGSERIAL   PRIMARY KEY,
    matview      TEXT        NOT NULL,
    mode         TEXT        NOT NULL,     -- 'full' | 'concurrent'
    duration_ms  NUMERIC(12,3) NOT NULL,
    rows_after   BIGINT,
    size_bytes   BIGINT,
    measured_at  TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),

    CONSTRAINT perf_refresh_mode CHECK (mode IN ('full', 'concurrent'))
);
