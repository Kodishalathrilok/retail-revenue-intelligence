-- Revert the temporary bulk-load settings from 01_bulk_load_mode.sql back to
-- the 00_tuning.sql baseline.
--
-- RESET restores the value to what postgresql.conf specifies, which is not what
-- we want -- we want the tuned baseline back. So these re-assert 00_tuning.sql's
-- values explicitly rather than resetting.
--
-- The loader runs this automatically in a finally block. Run it by hand if a
-- load was killed hard (SIGKILL, power loss) and you want to confirm state.

ALTER SYSTEM SET synchronous_commit = on;
ALTER SYSTEM SET maintenance_work_mem = '512MB';
ALTER SYSTEM SET max_wal_size = '4GB';

SELECT pg_reload_conf();

-- Confirm the revert took effect.
SELECT name, setting, unit
FROM pg_settings
WHERE name IN ('synchronous_commit', 'maintenance_work_mem', 'max_wal_size')
ORDER BY name;
