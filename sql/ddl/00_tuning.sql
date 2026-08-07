-- Baseline Postgres tuning for the benchmark environment.
--
-- Applied via ALTER SYSTEM (writes postgresql.auto.conf) rather than by hand
-- editing postgresql.conf, so the benchmark environment is version controlled
-- and reproducible. Numbers in docs/performance.md are meaningless without it.
--
-- Target hardware: 7.7 GB RAM, i5-1235U (12 logical), NVMe SSD, Windows 11.
-- Requires superuser. Run:  psql -U postgres -d rrip -f sql/ddl/00_tuning.sql

-- Memory ---------------------------------------------------------------------

-- The 25%-of-RAM rule of thumb suggests ~2 GB, but Postgres on Windows does not
-- use shared memory the way it does on Linux and rarely benefits above ~1 GB.
-- 1 GB leaves headroom for the OS and the Next.js dev server. NEEDS RESTART.
ALTER SYSTEM SET shared_buffers = '1GB';

-- Planner hint only; allocates nothing. Reflects shared_buffers + OS page cache
-- so the planner prices index scans correctly.
ALTER SYSTEM SET effective_cache_size = '4GB';

-- Per sort/hash node, per parallel worker -- a single query can multiply this
-- several times over. Kept modest globally at 8 GB; benchmark sessions raise it
-- explicitly with SET, and record the value alongside the timing.
ALTER SYSTEM SET work_mem = '32MB';

-- Dominates CREATE INDEX and VACUUM time. The single biggest lever on post-load
-- index build duration.
ALTER SYSTEM SET maintenance_work_mem = '512MB';

-- Storage --------------------------------------------------------------------

-- NVMe SSD. The default of 4.0 assumes seek-bound spinning disks and will push
-- the planner toward sequential scans -- on its own enough to invalidate a
-- before/after comparison.
ALTER SYSTEM SET random_page_cost = 1.1;
ALTER SYSTEM SET effective_io_concurrency = 200;

-- Write-ahead log ------------------------------------------------------------

-- The 1 GB default forces near-constant checkpointing during bulk load.
ALTER SYSTEM SET max_wal_size = '4GB';
ALTER SYSTEM SET min_wal_size = '1GB';
ALTER SYSTEM SET checkpoint_timeout = '15min';
ALTER SYSTEM SET checkpoint_completion_target = 0.9;

-- Parallelism ----------------------------------------------------------------

-- 12 logical CPUs. Directly shapes the Phase 2 expensive-query plans.
ALTER SYSTEM SET max_worker_processes = 12;
ALTER SYSTEM SET max_parallel_workers = 8;
ALTER SYSTEM SET max_parallel_workers_per_gather = 4;
ALTER SYSTEM SET max_parallel_maintenance_workers = 4;

-- Observability --------------------------------------------------------------

-- Needed to capture real timings and buffer counts in Phase 2.
ALTER SYSTEM SET track_io_timing = on;
ALTER SYSTEM SET track_functions = 'pl';

SELECT pg_reload_conf();

-- shared_buffers and max_worker_processes require a restart to take effect.
-- Restart the service, then verify with sql/ddl/99_verify_settings.sql
