"""
Phase 3.5 - Single entry point running the full verification package.

Order matters: rollback and logging/exit-code checks first (fully
isolated, no live sources or real DB touched), then live ingestion
(populates real data), then DB integrity and idempotency (which read/
re-run against that real data).

    python -m verification.run_all_verifications

Exits 0 only if every stage passes. Prints a final summary table
suitable for pasting into PHASE3_5_VERIFICATION_REPORT_TEMPLATE.md.
"""

import sys

from verification import verify_rollback
from verification import verify_logging_and_exit_code
from verification import verify_live_ingestion
from verification import verify_db_integrity
from verification import verify_idempotency


def main():
    stages = [
        ("Rollback verification", verify_rollback.main),
        ("Logging / exit-code verification", verify_logging_and_exit_code.main),
        ("Live ingestion verification", verify_live_ingestion.main),
        ("DuckDB integrity verification", verify_db_integrity.main),
        ("Idempotency verification", verify_idempotency.main),
    ]

    results = []
    for name, fn in stages:
        print(f"\n{'=' * 70}\nSTAGE: {name}\n{'=' * 70}")
        try:
            ok = fn()
        except Exception as e:
            print(f"STAGE CRASHED (not a clean PASS/FAIL, a real exception): {e!r}")
            ok = False
        results.append((name, bool(ok)))

    print(f"\n{'=' * 70}\nFINAL SUMMARY\n{'=' * 70}")
    for name, ok in results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")

    overall = all(ok for _, ok in results)
    print(f"\nOVERALL PHASE 3.5 RESULT: {'PASS' if overall else 'FAIL'}")
    return overall


if __name__ == "__main__":
    ok = main()
    sys.exit(0 if ok else 1)
