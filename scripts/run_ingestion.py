"""
CLI entry point for Phase 2: D05.SI and ^STI daily OHLCV ingestion.

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
  5. Prints the data-quality report.

Only Phase 2 scope: no macro, fundamentals, features, situation
matching, ML, or UI here - see PROJECT_STATUS.md.
"""

from db.connection import get_connection
from config import SECURITIES, INDICES
from ingestion.prices import fetch_security_prices, fetch_index_prices, IngestionFailure
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

    print("\n" + generate_data_quality_report(con))

    return results


if __name__ == "__main__":
    main()
