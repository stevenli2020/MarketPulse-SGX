"""
Phase 3.5 - Live ingestion verification.

Runs a REAL ingestion against all three configured macro sources (MAS
SORA, FRED EFFR, Yahoo Finance USDSGD=X) using the existing, unmodified
Phase 3 ingestion implementation (ingestion.macro.fetch_macro_series).

This script requires live network access and a real DuckDB installation
- it was written but NOT executed in Cola's development sandbox, which
has neither (see PROJECT_STATUS.md). Run it in Sprite's WSL environment:

    python -m verification.verify_live_ingestion

Exits 0 if every configured series ingests successfully, 1 otherwise -
never silently reports success on a failure (see PHASE3_5 logging
verification requirements).

For each source, reports PASS/FAIL against the requested checkpoints:
connection+download, normalization, validation, database insertion/
update, and overall completion. These are read off fetch_macro_series's
own return value and any exception it raises, rather than instrumenting
internals separately - this script does not modify or reach into
ingestion.macro's internals.
"""

import sys
import traceback
from datetime import datetime

from config import MACRO_SERIES
from ingestion.macro import fetch_macro_series
from ingestion.prices import IngestionFailure, NormalizationFailure


def _verify_one_series(series_id: str) -> dict:
    print(f"\n--- {series_id} ---")
    try:
        result = fetch_macro_series(series_id)
    except IngestionFailure as e:
        # Covers both a plain fetch failure (connection/download/shape)
        # and NormalizationFailure (fetch OK, storage transaction failed
        # and rolled back) - NormalizationFailure subclasses
        # IngestionFailure, so both are caught here uniformly.
        print(f"  FAIL: {e}")
        return {"series_id": series_id, "pass": False, "reason": str(e)}

    valid_count = result["rows_inserted"] + result["rows_updated_revised"] + result["rows_unchanged"]
    reconciles = valid_count == (result["rows_received"] - result["rows_rejected"])
    all_rejected = result["rows_received"] > 0 and result["rows_rejected"] == result["rows_received"]

    checks = {
        "connection_and_download": result["rows_received"] > 0,
        "normalization_and_validation_ran": True,  # implied - we got a structured result, not an exception
        "no_rows_lost_between_valid_and_stored": reconciles,
        "not_all_rows_rejected": not all_rejected,
        "database_insert_or_update_occurred": (result["rows_inserted"] + result["rows_updated_revised"]) > 0
            or result["rows_unchanged"] > 0,  # a rerun with nothing changed is still a pass
    }
    overall = all(checks.values())

    print(f"  rows_received={result['rows_received']} rows_rejected={result['rows_rejected']} "
          f"rows_inserted={result['rows_inserted']} rows_updated_revised={result['rows_updated_revised']} "
          f"rows_unchanged={result['rows_unchanged']} warnings={result['warnings']} "
          f"revisions={result['revisions']}")
    print(f"  coverage: {result['coverage_start']} to {result['coverage_end']}")
    for check_name, ok in checks.items():
        print(f"  [{'PASS' if ok else 'FAIL'}] {check_name}")
    print(f"  => {'PASS' if overall else 'FAIL'}")

    return {"series_id": series_id, "pass": overall, "result": result, "checks": checks}


def main():
    print(f"=== Phase 3.5 Live Ingestion Verification - {datetime.now().isoformat()} ===")
    print(f"Configured series: {MACRO_SERIES}")

    outcomes = []
    for series_id in MACRO_SERIES:
        try:
            outcomes.append(_verify_one_series(series_id))
        except Exception as e:
            # Anything NOT already converted to IngestionFailure by
            # ingestion.macro is itself a finding worth surfacing loudly,
            # not swallowing - this script must never mask an unexpected
            # exception as a clean failure.
            print(f"  UNEXPECTED EXCEPTION (not IngestionFailure): {e!r}")
            traceback.print_exc()
            outcomes.append({"series_id": series_id, "pass": False, "reason": f"unexpected exception: {e!r}"})

    print("\n=== Summary ===")
    overall_ok = True
    for o in outcomes:
        status = "PASS" if o["pass"] else "FAIL"
        print(f"  {o['series_id']}: {status}")
        overall_ok = overall_ok and o["pass"]

    print(f"\nOverall: {'PASS' if overall_ok else 'FAIL'}")
    return overall_ok


if __name__ == "__main__":
    ok = main()
    sys.exit(0 if ok else 1)
