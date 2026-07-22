"""
Phase 3.5 - Failure / rollback verification.

Deliberately forces a failure partway through a macro ingestion write
and confirms the transaction rolls back cleanly, leaving zero partial
rows. Unlike the other verification scripts in this package, this one
does NOT touch live sources or the real project database - it operates
on a fully isolated, temporary DuckDB file created just for this run
and deleted afterward, so it is safe to run at any time without risk to
real ingested data.

This reuses the real, unmodified ingestion.macro functions (not a
reimplementation of rollback logic) via a monkeypatch that forces a
RuntimeError partway through the normalized-write step - simulating the
kind of failure a real storage error would cause, deterministically.

Run:
    python -m verification.verify_rollback

Exits 0 if rollback behaves correctly, 1 otherwise.
"""

import sys
import tempfile
import os
from datetime import date
from unittest.mock import patch

import duckdb

from config import SCHEMA_PATH
import ingestion.macro as macro_mod


def main():
    print("=== Phase 3.5 Rollback Verification ===")
    print("Using an isolated temporary DuckDB file - the real project database is not touched.\n")

    fd, tmp_path = tempfile.mkstemp(suffix=".duckdb")
    os.close(fd)
    os.remove(tmp_path)  # duckdb.connect creates it fresh

    try:
        con = duckdb.connect(tmp_path)
        with open(SCHEMA_PATH) as f:
            con.execute(f.read())

        rows = [
            {"series_id": "SORA", "obs_date": date(2024, 1, 2), "value": 3.51,
             "as_of_date": date(2024, 1, 3), "source": "MAS_API"},
            {"series_id": "SORA", "obs_date": date(2024, 1, 3), "value": 3.52,
             "as_of_date": date(2024, 1, 4), "source": "MAS_API"},
            {"series_id": "SORA", "obs_date": date(2024, 1, 4), "value": 3.53,
             "as_of_date": date(2024, 1, 5), "source": "MAS_API"},
        ]

        count_before = con.execute("SELECT COUNT(*) FROM raw_macro_series").fetchone()[0]
        print(f"Row count before forced-failure attempt: {count_before}")

        def poison_after_first_row(con_inner, series_id, valid_rows_inner):
            # Insert one row for real (simulating partial progress), then
            # raise - exactly the scenario a wrapping transaction exists
            # to protect against.
            con_inner.execute(
                "INSERT INTO raw_macro_series (series_id, obs_date, value, as_of_date, source, ingested_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [series_id, valid_rows_inner[0]["obs_date"], valid_rows_inner[0]["value"],
                 valid_rows_inner[0]["as_of_date"], valid_rows_inner[0]["source"], "2024-01-01"],
            )
            raise RuntimeError("Phase 3.5 verification: simulated storage failure partway through the batch")

        with patch.object(macro_mod, "get_connection", return_value=con), \
             patch.object(macro_mod, "_upsert_macro_series", poison_after_first_row):
            try:
                macro_mod._ingest_one_series("SORA", lambda: rows)
                print("\n[FAIL] Expected NormalizationFailure was not raised - the forced failure did not propagate.")
                return False
            except macro_mod.NormalizationFailure as e:
                print(f"\n[PASS] NormalizationFailure raised and caught, as expected: {e}")
            except Exception as e:
                print(f"\n[FAIL] Wrong exception type raised: {e!r} (expected NormalizationFailure)")
                return False

        count_after = con.execute("SELECT COUNT(*) FROM raw_macro_series").fetchone()[0]
        print(f"Row count after forced-failure attempt: {count_after}")

        checks = {
            "rollback occurred (row count unchanged)": count_after == count_before,
            "no partial writes remain": count_after == 0,
            "failure was reported (not silent)": True,  # the NormalizationFailure message above IS the report
        }
        for name, ok in checks.items():
            print(f"  [{'PASS' if ok else 'FAIL'}] {name}")

        overall = all(checks.values())
        print(f"\nOverall: {'PASS' if overall else 'FAIL'}")
        con.close()
        return overall

    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        print(f"\nTemporary verification database removed: {tmp_path}")


if __name__ == "__main__":
    ok = main()
    sys.exit(0 if ok else 1)
