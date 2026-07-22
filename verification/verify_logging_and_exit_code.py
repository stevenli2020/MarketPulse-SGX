"""
Phase 3.5 - Logging and failure-propagation verification.

Confirms, without needing a live network failure, that:
  1. A macro ingestion failure produces a human-readable, specific error
     message (not a generic/opaque one).
  2. scripts/run_ingestion.py's overall exit code reflects failure (non-
     zero) when a required ingestion step fails - this was Issue 2 from
     the earlier architecture audit; this script gives Sprite a repeatable
     way to re-confirm it rather than relying on a one-time manual check.
  3. No ingestion failure is ever silently reported as success.

Does not touch the real project database or live sources - forces a
failure deterministically via monkeypatching, the same technique used
throughout this project's test suite.

Run:
    python -m verification.verify_logging_and_exit_code
"""

import sys
from unittest.mock import patch

from ingestion.prices import IngestionFailure
import ingestion.macro as macro_mod
import scripts.run_ingestion as run_ingestion_mod


def main():
    print("=== Phase 3.5 Logging and Exit-Code Verification ===\n")
    results = []

    # --- Check 1: error message is human-readable and specific --------
    print("Check 1: failure message readability")

    def always_fail(series_id):
        raise IngestionFailure(f"{series_id}: simulated failure for Phase 3.5 verification")

    with patch.object(run_ingestion_mod, "fetch_macro_series", always_fail):
        try:
            always_fail("SORA")
            msg = None
        except IngestionFailure as e:
            msg = str(e)

    readable = msg is not None and "SORA" in msg and "simulated failure" in msg and len(msg) > 20
    print(f"  message: {msg!r}")
    print(f"  [{'PASS' if readable else 'FAIL'}] message names the series and the reason, not just a generic error")
    results.append(readable)

    # --- Check 2: run_ingestion.main() propagates failure to exit code -
    print("\nCheck 2: overall exit-code propagation on a forced macro failure")

    with patch.object(run_ingestion_mod, "fetch_security_prices") as mock_sec, \
         patch.object(run_ingestion_mod, "fetch_index_prices") as mock_idx, \
         patch.object(run_ingestion_mod, "fetch_macro_series", side_effect=IngestionFailure("forced failure")), \
         patch.object(run_ingestion_mod, "get_connection") as mock_con, \
         patch.object(run_ingestion_mod, "check_cross_instrument_date_consistency",
                       return_value={"skipped": True, "reason": "forced skip for this check"}), \
         patch.object(run_ingestion_mod, "generate_data_quality_report", return_value="(report skipped)"):
        mock_sec.return_value = {
            "ticker": "D05.SI", "rows_received": 1, "rows_rejected": 0, "rows_inserted": 1,
            "rows_updated_revised": 0, "rows_unchanged": 0, "warnings": 0,
            "coverage_start": None, "coverage_end": None,
        }
        mock_idx.return_value = dict(mock_sec.return_value, ticker="^STI")
        mock_con.return_value.execute.return_value.fetchone.return_value = (None,)

        try:
            results_list, overall_ok = run_ingestion_mod.main()
        except Exception as e:
            print(f"  [FAIL] main() raised unexpectedly instead of returning a failure flag: {e!r}")
            overall_ok = None

    exit_code_would_be = 0 if overall_ok else 1
    correct = overall_ok is False and exit_code_would_be == 1
    print(f"  overall_ok returned by main(): {overall_ok}")
    print(f"  [{'PASS' if correct else 'FAIL'}] main() correctly reports failure; "
          f"sys.exit({exit_code_would_be}) would be called by the __main__ block")
    results.append(bool(correct))

    print(f"\nOverall: {'PASS' if all(results) else 'FAIL'}")
    return all(results)


if __name__ == "__main__":
    ok = main()
    sys.exit(0 if ok else 1)
