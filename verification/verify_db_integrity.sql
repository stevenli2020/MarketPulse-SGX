-- Phase 3.5 - DuckDB integrity verification queries for raw_macro_series.
--
-- These are read-only SELECT queries only - nothing here writes to the
-- database. Each query's comment states the expected "healthy" result;
-- verify_db_integrity.py runs these exact queries and reports PASS/FAIL
-- against those expectations. Provided standalone here too so they can
-- be run manually (e.g. via the DuckDB CLI) or adapted for CI, per the
-- requirement for "SQL integrity verification script(s)".

-- === Macro table integrity ===================================================

-- Row count per series (informational - compare against expectations,
-- no single "correct" number).
SELECT series_id, COUNT(*) AS row_count, MIN(obs_date) AS earliest, MAX(obs_date) AS latest
FROM raw_macro_series
GROUP BY series_id
ORDER BY series_id;

-- Duplicate primary keys. Expected: ZERO rows. The real PK constraint
-- (series_id, obs_date, as_of_date) should make this structurally
-- impossible, but this independently verifies the constraint is actually
-- holding in the live database, not just assumed from the schema file.
SELECT series_id, obs_date, as_of_date, COUNT(*) AS n
FROM raw_macro_series
GROUP BY series_id, obs_date, as_of_date
HAVING COUNT(*) > 1;

-- NULL values in any required column. Expected: ZERO rows.
SELECT * FROM raw_macro_series
WHERE series_id IS NULL OR obs_date IS NULL OR value IS NULL
   OR as_of_date IS NULL OR source IS NULL;

-- Invalid dates: obs_date in the future. Expected: ZERO rows.
-- (Backs the defect fix in validate_macro_rows - this is the storage-
-- layer defense-in-depth check for the same condition.)
SELECT * FROM raw_macro_series WHERE obs_date > CURRENT_DATE;

-- Invalid as_of_date < obs_date. Expected: ZERO rows - validate_macro_rows
-- rejects this at insert time; this re-confirms it holds in storage.
SELECT * FROM raw_macro_series WHERE as_of_date < obs_date;

-- Orphan records: NOT APPLICABLE. raw_macro_series has no foreign-key
-- relationship to any other table (unlike prices_daily/index_daily,
-- which reference dim_securities/dim_indices) - series_id is a plain
-- string identifier, not an FK. Documented here rather than silently
-- omitted, per the requirement to cover this category "if applicable".

-- === Revision integrity ======================================================

-- Multiple vintages preserved: observations with more than one
-- as_of_date on record. Informational, not a failure condition - this
-- is expected to be rare/zero in normal operation (see PROJECT_STATUS.md
-- Phase 3 notes) but should never be silently lost if it does occur.
SELECT series_id, obs_date, COUNT(DISTINCT as_of_date) AS vintage_count
FROM raw_macro_series
GROUP BY series_id, obs_date
HAVING COUNT(DISTINCT as_of_date) > 1
ORDER BY series_id, obs_date;

-- Revision/vintage warnings logged vs. distinct series/obs_date pairs
-- they reference - a rough cross-check that logged revision events
-- correspond to something real in raw_macro_series (not a proof of
-- completeness in the other direction - see verify_db_integrity.py's
-- notes on this check's limitations).
SELECT warning_type, COUNT(*) AS n
FROM data_quality_warnings
WHERE warning_type IN ('macro_revision_detected', 'macro_new_vintage_for_existing_obs_date')
GROUP BY warning_type;

-- === Data quality ============================================================

-- Coverage sanity: MIN/MAX obs_date within plausible bounds
-- (MACRO_HISTORY_START_DATE to today). Expected: ZERO rows outside this.
SELECT series_id, MIN(obs_date) AS earliest, MAX(obs_date) AS latest
FROM raw_macro_series
GROUP BY series_id
HAVING MIN(obs_date) < DATE '1990-01-01' OR MAX(obs_date) > CURRENT_DATE;

-- Impossible values: outside the same broad sanity bounds
-- validate_macro_rows enforces at insert time (rates: -10 to 100;
-- SGD_USD_FX: 0.01 to 100). Expected: ZERO rows.
SELECT * FROM raw_macro_series
WHERE (series_id IN ('SORA', 'US_FED_FUNDS_RATE') AND (value < -10.0 OR value > 100.0))
   OR (series_id = 'SGD_USD_FX' AND (value < 0.01 OR value > 100.0));

-- No duplicate logical observations: covered by the duplicate-PK query
-- above (a "logical observation" for this schema is exactly the PK
-- tuple), restated here for completeness against the requirement list.
