"""
Phase 3.5 - Idempotency verification.

Runs live macro ingestion TWICE, back to back, against the real
configured sources, and compares the outcomes. Requires live network
access and real DuckDB - not executable in Cola's development sandbox
(see PROJECT_STATUS.md). Run in Sprite's WSL environment:

    python -m verification.verify_idempotency

Expected outcome (per PHASE3.5 requirements):
  - Run 2 produces no duplicate rows (row count after Run 2 == row
    count after Run 1, modulo any genuine new observation becoming
    available in between - see note below).
  - Run 2 produces no unintended updates (rows_updated_revised on Run 2
    should be 0, or explainable by a genuine upstream revision that
    happened to occur in the gap between the two runs - flagged for
    manual judgment, not auto-failed, since this script cannot itself
    distinguish "a real revision happened" from "our logic is wrong"
    with certainty).
  - Any revisions on Run 2 are reported explicitly, not hidden, so
    Sprite can judge whether they're genuine.
"""

import sys
from datetime import datetime

from config import MACRO_SERIES
from db.connection import get_connection
from ingestion.macro import fetch_macro_series
from ingestion.prices import IngestionFailure


def _row_counts(con) -> dict:
    rows = con.execute("SELECT series_id, COUNT(*) FROM raw_macro_series GROUP BY series_id").fetchall()
    return {series_id: count for series_id, count in rows}


def _run_once(label: str) -> dict:
    print(f"\n=== {label} - {datetime.now().isoformat()} ===")
    outcomes = {}
    for series_id in MACRO_SERIES:
        try:
            result = fetch_macro_series(series_id)
            outcomes[series_id] = result
            print(f"  {series_id}: inserted={result['rows_inserted']} "
                  f"updated_revised={result['rows_updated_revised']} unchanged={result['rows_unchanged']}")
        except IngestionFailure as e:
            outcomes[series_id] = {"error": str(e)}
            print(f"  {series_id}: FAILED - {e}")
    return outcomes


def main():
    con = get_connection()

    counts_before = _row_counts(con)
    print(f"Row counts before Run 1: {counts_before}")

    run1 = _run_once("Run 1")
    counts_after_run1 = _row_counts(con)
    print(f"\nRow counts after Run 1: {counts_after_run1}")

    run2 = _run_once("Run 2 (immediately following Run 1)")
    counts_after_run2 = _row_counts(con)
    print(f"\nRow counts after Run 2: {counts_after_run2}")

    print("\n=== Idempotency Analysis ===")
    all_ok = True
    for series_id in MACRO_SERIES:
        if "error" in run1.get(series_id, {}) or "error" in run2.get(series_id, {}):
            print(f"  {series_id}: SKIPPED (one or both runs failed - see errors above) -> FAIL")
            all_ok = False
            continue

        run2_inserted = run2[series_id]["rows_inserted"]
        run2_updated = run2[series_id]["rows_updated_revised"]
        count_grew = counts_after_run2.get(series_id, 0) != counts_after_run1.get(series_id, 0)

        no_new_inserts = run2_inserted == 0
        series_ok = no_new_inserts and not count_grew

        print(f"  {series_id}:")
        print(f"    [{'PASS' if no_new_inserts else 'FAIL'}] Run 2 inserted 0 new rows (got {run2_inserted})")
        print(f"    [{'PASS' if not count_grew else 'FAIL'}] row count unchanged between Run 1 and Run 2 end states")
        if run2_updated > 0:
            print(f"    [REVIEW REQUIRED] Run 2 reported {run2_updated} revision(s) - these may be genuine "
                  f"upstream revisions that occurred in the gap between runs, or may indicate a logic issue. "
                  f"Not auto-classified as pass or fail - inspect data_quality_warnings for "
                  f"'macro_revision_detected'/'macro_new_vintage_for_existing_obs_date' entries with a "
                  f"detected_at timestamp between the two runs to judge which.")
        all_ok = all_ok and series_ok

    print(f"\nOverall (excluding any REVIEW REQUIRED items, which need manual judgment): "
          f"{'PASS' if all_ok else 'FAIL'}")
    return all_ok


if __name__ == "__main__":
    ok = main()
    sys.exit(0 if ok else 1)
