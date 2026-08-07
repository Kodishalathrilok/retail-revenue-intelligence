-- Capture the live configuration as the documented baseline for
-- docs/performance.md. A benchmark without its configuration is not
-- reproducible, so this output belongs in the doc verbatim.
--
--   psql -U postgres -d rrip -f sql/ddl/99_verify_settings.sql

\echo '--- Server version ---'
SELECT version();

\echo '--- Tuned settings (pending_restart flags anything needing a restart) ---'
SELECT
    name,
    setting,
    COALESCE(unit, '') AS unit,
    source,
    pending_restart
FROM pg_settings
WHERE name IN (
    'shared_buffers', 'effective_cache_size', 'work_mem', 'maintenance_work_mem',
    'random_page_cost', 'effective_io_concurrency',
    'max_wal_size', 'min_wal_size', 'checkpoint_timeout', 'checkpoint_completion_target',
    'max_worker_processes', 'max_parallel_workers', 'max_parallel_workers_per_gather',
    'max_parallel_maintenance_workers',
    'synchronous_commit', 'track_io_timing', 'jit'
)
ORDER BY name;

\echo '--- Anything still awaiting a restart ---'
SELECT name, setting FROM pg_settings WHERE pending_restart;
