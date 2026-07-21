"""
CLI entry point for Phase 2 (price/index) and Phase 3 (macro) ingestion.

Usage (run as a module from the project root):
    python -m scripts.run_ingestion

This script:
  1. Ensures the database and schema exist.
  2. Ensures dim_securities / dim_indices are populated from config.py.
  3. Runs price ingestion for each configured security and index.
  4. Runs the cross-instrument date consistency check - but ONLY if both
     D05.SI and ^STI ingestion succeeded in this run AND both normalized
     tables are non-empty (see validation/checks.py hardening note).
     Otherwise, reports that the check was skipped and why.
  5. Runs macro ingestion for each series in config.MACRO_SERIES (SORA,
     US_FED_FUNDS_RATE, SGD_USD_FX). A macro series failure is reported
     explicitly per series and never silently treated as success.
  6. Prints the data-quality report (now including macro coverage).

Phase 3 scope only extends to macro data STORAGE - no feature
engineering, labeling, or modeling is invoked here. See PROJECT_STATUS.md.
"""

from db.connection import get_connection
from config import SECURITIES, INDICES, MACRO_SERIES
from ingestion.prices import fetch_security_prices, fetch_index_prices, IngestionFailure
from ingestion.macro import fetch_macro_series
from validation.checks import check_cross_instrument_date_consistency, generate_data_quality_report


def _ensure_dims(con):
    """Populate dim_securities / dim_indices from config.py if not already present."""
    for i, s in enumerate(SECURITIES, start=1):
        con.execute(
            "INSERT INTO dim_securities (security_id, ticker, name, exchange, listed_date) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT (ticker) DO NOTHING",
            [i, s["ticker"], s["name"], s["exchange"], s.get("listed_date")],
        )
    for i, idx in enumerate(INDICES, start=1):
        con.execute(
            "INSERT INTO dim_indices (index_id, ticker, name) "
            "VALUES (?, ?, ?) ON CONFLICT (ticker) DO NOTHING",
            [i, idx["ticker"], idx["name"]],
        )


def main():
    con = get_connection()
    _ensure_dims(con)

    print(f"Database ready. Configured securities: {[s['ticker'] for s in SECURITIES]}")
    print(f"Configured indices: {[i['ticker'] for i in INDICES]}\n")

    results = []
    security_run_ok = True
    index_run_ok = True

    for i, s in enumerate(SECURITIES, start=1):
        print(f"Ingesting {s['ticker']} ...")
        try:
            result = fetch_security_prices(s["ticker"], i)
            results.append(result)
            print(f"  OK: {result['rows_received']} received, {result['rows_rejected']} rejected, "
                  f"{result['rows_inserted']} inserted, {result['rows_updated_revised']} revised, "
                  f"{result['rows_unchanged']} unchanged, {result['warnings']} warnings, "
                  f"coverage {result['coverage_start']} to {result['coverage_end']}")
        except IngestionFailure as e:
            security_run_ok = False
            print(f"  FAILED (no partial data committed): {e}")

    for i, idx in enumerate(INDICES, start=1):
        print(f"Ingesting {idx['ticker']} ...")
        try:
            result = fetch_index_prices(idx["ticker"], i)
            results.append(result)
            print(f"  OK: {result['rows_received']} received, {result['rows_rejected']} rejected, "
                  f"{result['rows_inserted']} inserted, {result['rows_updated_revised']} revised, "
                  f"{result['rows_unchanged']} unchanged, {result['warnings']} warnings, "
                  f"coverage {result['coverage_start']} to {result['coverage_end']}")
        except IngestionFailure as e:
            index_run_ok = False
            print(f"  FAILED (no partial data committed): {e}")

    print("\nCross-instrument date consistency check ...")
    if not (security_run_ok and index_run_ok):
        print("  SKIPPED: at least one of D05.SI / ^STI did not ingest successfully this run.")
    else:
        check_result = check_cross_instrument_date_consistency(con)
        if check_result["skipped"]:
            print(f"  SKIPPED: {check_result['reason']}")
        else:
            print(f"  Comparing overlap window {check_result['overlap_start']} to {check_result['overlap_end']} only "
                  f"(dates outside this window are not evaluated - see PROJECT_STATUS.md)")
            if check_result["anomalies"]:
                for a in check_result["anomalies"]:
                    print(f"  WARNING: {a['detail']} ({a['trade_date']})")
            else:
                print("  none found")

    print("\nMacro ingestion (Phase 3) ...")
    macro_run_ok = True
    for series_id in MACRO_SERIES:
        print(f"Ingesting {series_id} ...")
        try:
            result = fetch_macro_series(series_id)
            results.append(result)
            print(f"  OK: {result['rows_received']} received, {result['rows_rejected']} rejected, "
                  f"{result['rows_inserted']} inserted, {result['rows_updated_revised']} revised, "
                  f"{result['rows_unchanged']} unchanged, {result['warnings']} warnings, "
                  f"coverage {result['coverage_start']} to {result['coverage_end']}")
        except IngestionFailure as e:
            macro_run_ok = False
            print(f"  FAILED (no partial data committed): {e}")

    print("\n" + generate_data_quality_report(con))

    # FIX (Macha audit, Issue 2): previously security_run_ok/index_run_ok
    # were tracked but never actually used to affect the process exit
    # code, and macro ingestion wasn't tracked at all - a failure would
    # print an error but the script would still exit 0 (success) as far
    # as the OS/any calling automation is concerned. Now surfaced
    # explicitly via the return value, with sys.exit() set accordingly
    # in the __main__ block below (kept out of main() itself so main()
    # remains a plain, importable/testable function, not something that
    # terminates the process as a side effect).
    overall_ok = security_run_ok and index_run_ok and macro_run_ok
    if not overall_ok:
        print("\nOne or more ingestion steps failed this run - see FAILED lines above.")

    return results, overall_ok


if __name__ == "__main__":
    import sys
    _, ok = main()
    sys.exit(0 if ok else 1)
