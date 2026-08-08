-- ETL load control.
--
-- Makes the load resumable and idempotent. Each step commits its own row, so a
-- load killed mid-run resumes from the last completed step rather than
-- restarting -- which matters when the largest step moves 36.8M rows.
--
-- Kept outside the star schema (etl_ prefix) because it is operational
-- metadata, not analytical data.

CREATE TABLE IF NOT EXISTS etl_load_control (
    step_name   TEXT        PRIMARY KEY,
    source_file TEXT,
    rows_loaded BIGINT,
    status      TEXT        NOT NULL,
    started_at  TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    duration_s  NUMERIC(10,2),
    error       TEXT,

    CONSTRAINT etl_status_valid CHECK (status IN ('running', 'done', 'failed'))
);

-- Data quality results recorded from the actual load, not recomputed later.
--
-- Reconciliation needs these: fact_causal is deliberately deduplicated on its
-- natural key, so loaded rows will never equal source lines. Without the
-- recorded duplicate count, reconciliation reports a false failure on a known
-- and documented condition -- which trains people to ignore it.
CREATE TABLE IF NOT EXISTS etl_data_quality (
    check_name  TEXT        PRIMARY KEY,
    value       BIGINT      NOT NULL,
    note        TEXT,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

-- Per-batch throughput, so docs/performance.md can report measured load rates
-- instead of a single wall-clock number, and so a slow partition is visible.
CREATE TABLE IF NOT EXISTS etl_batch_log (
    id          BIGSERIAL   PRIMARY KEY,
    step_name   TEXT        NOT NULL,
    batch_key   TEXT        NOT NULL,
    rows_loaded BIGINT      NOT NULL,
    duration_s  NUMERIC(10,3) NOT NULL,
    rows_per_s  NUMERIC(12,1) NOT NULL,
    -- clock_timestamp(), not now(). now() returns TRANSACTION start time, and
    -- the batch INSERT and this log row share a transaction -- so now() records
    -- when the batch began, not when it was logged. That is subtly misleading
    -- when correlating batch timings against external events.
    logged_at   TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
