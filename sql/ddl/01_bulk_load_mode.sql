-- Temporary settings for bulk load ONLY. Reverted by 02_bulk_load_revert.sql.
--
-- The loader applies and reverts these automatically in a try/finally, so a
-- crashed load does not silently leave the server in this state. This file
-- exists so the change is inspectable and so recovery is possible by hand.
--
-- Safe here because the database is fully reproducible from the source CSVs.
-- It would NOT be safe on anything holding data you cannot regenerate.

-- Do not wait for WAL flush on commit. Trades crash durability for load speed:
-- a crash can lose recent transactions but cannot corrupt the database.
ALTER SYSTEM SET synchronous_commit = off;

-- Raised for the post-load CREATE INDEX pass.
ALTER SYSTEM SET maintenance_work_mem = '1GB';

-- Bulk COPY produces heavy WAL churn; widen the checkpoint window further.
ALTER SYSTEM SET max_wal_size = '8GB';

SELECT pg_reload_conf();
