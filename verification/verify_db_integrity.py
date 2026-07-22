"""
Phase 3.5 - DuckDB integrity verification runner.

Executes the checks described in verify_db_integrity.sql (same queries,
same expectations) against the real project database and reports
PASS/FAIL. Read-only - issues no INSERT/UPDATE/DELETE anywhere.

Run in Sprite's WSL environment, after a live ingestion has populated
raw_macro_series:

    python -m verification.verify_db_integrity

Exits 0 if every check passes, 1 otherwise.
"""

import sys

from db.connection import get_connection


def _check(con, name: str, sql: str, expect_empty: bool = True) -> bool:
    rows = con.execute(sql).fetchall()
    if expect_empty:
        ok = len(rows) == 0
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok else f" - {len(rows)} offending row(s)"))
        if not ok:
            for r in rows[:5]:
                print(f"        {r}")
            if len(rows) > 5:
                print(f"        ... and {len(rows) - 5} more")
    else:
        ok = True  # informational query, not a pass/fail condition itself
        print(f"  [INFO] {name}: {len(rows)} row(s)")
        for r in rows[:10]:
            print(f"        {r}")
    return ok


def main():
    con = get_connection()
    print("=== Phase 3.5 DuckDB Integrity Verification ===\n")

    print("Row counts and coverage (informational):")
    _check(con, "row counts per series",
           "SELECT series_id, COUNT(*), MIN(obs_date), MAX(obs_date) FROM raw_macro_series GROUP BY series_id",
           expect_empty=False)

    print("\nMacro table integrity:")
    results = []
    results.append(_check(con, "no duplicate primary keys",
        "SELECT series_id, obs_date, as_of_date, COUNT(*) FROM raw_macro_series "
        "GROUP BY series_id, obs_date, as_of_date HAVING COUNT(*) > 1"))
    results.append(_check(con, "no NULL values in required columns",
        "SELECT * FROM raw_macro_series WHERE series_id IS NULL OR obs_date IS NULL "
        "OR value IS NULL OR as_of_date IS NULL OR source IS NULL"))
    results.append(_check(con, "no future obs_date",
        "SELECT * FROM raw_macro_series WHERE obs_date > CURRENT_DATE"))
    results.append(_check(con, "no as_of_date < obs_date",
        "SELECT * FROM raw_macro_series WHERE as_of_date < obs_date"))
    print("  [INFO] orphan records: not applicable - raw_macro_series has no foreign-key relationship "
          "to any other table (see verify_db_integrity.sql)")

    print("\nRevision integrity:")
    _check(con, "multiple vintages (informational, not a failure condition)",
        "SELECT series_id, obs_date, COUNT(DISTINCT as_of_date) AS vintage_count FROM raw_macro_series "
        "GROUP BY series_id, obs_date HAVING COUNT(DISTINCT as_of_date) > 1", expect_empty=False)
    _check(con, "revision/vintage warnings logged (informational)",
        "SELECT warning_type, COUNT(*) FROM data_quality_warnings "
        "WHERE warning_type IN ('macro_revision_detected', 'macro_new_vintage_for_existing_obs_date') "
        "GROUP BY warning_type", expect_empty=False)
    results.append(_check(con, "primary key uniqueness (re-verified)",
        "SELECT series_id, obs_date, as_of_date, COUNT(*) FROM raw_macro_series "
        "GROUP BY series_id, obs_date, as_of_date HAVING COUNT(*) > 1"))
    print("  [NOTE] revision-warning-vs-actual-change cross-checking is only a partial guarantee: "
          "raw_macro_series retains only the latest value per vintage, not a full change history, "
          "so this cannot independently re-derive whether every past silent change (if any occurred "
          "before this logging existed) was captured. Documented as a known limitation, not silently assumed complete.")

    print("\nData quality:")
    results.append(_check(con, "coverage within plausible bounds (1990-01-01 to today)",
        "SELECT series_id, MIN(obs_date), MAX(obs_date) FROM raw_macro_series GROUP BY series_id "
        "HAVING MIN(obs_date) < DATE '1990-01-01' OR MAX(obs_date) > CURRENT_DATE"))
    results.append(_check(con, "no impossible values (out of sanity bounds)",
        "SELECT * FROM raw_macro_series WHERE "
        "(series_id IN ('SORA', 'US_FED_FUNDS_RATE') AND (value < -10.0 OR value > 100.0)) "
        "OR (series_id = 'SGD_USD_FX' AND (value < 0.01 OR value > 100.0))"))
    print("  [INFO] no duplicate logical observations: covered by the primary-key-uniqueness check above")

    overall_ok = all(results)
    print(f"\nOverall: {'PASS' if overall_ok else 'FAIL'}")
    return overall_ok


if __name__ == "__main__":
    ok = main()
    sys.exit(0 if ok else 1)
