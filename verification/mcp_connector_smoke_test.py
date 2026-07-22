"""
MCP connector smoke test - NOT a Phase 3.5 verification stage.

Purpose: confirm the MarketPulse SGX MCP connector's write -> execute
pipeline works correctly, and that the WSL virtual environment itself
is healthy, BEFORE trusting it with anything higher-stakes (live network
calls, database writes, git commits).

Deliberately read-only and side-effect-free: no network calls, no
database writes, no file modifications beyond this script's own
existence. Safe to run at any time.

PORTABILITY NOTE (added after first real MCP connector test): unlike
this project's verification/*.py modules, which are designed to be run
via `python -m verification.xxx` (as run_verification_module correctly
does), this script is also meant to work when executed directly via
execute_python_script, which invokes scripts as `python <path>` rather
than `-m`. Direct script execution does not add the project root to
sys.path (the same class of issue pytest.ini's `pythonpath` option
fixed for pytest specifically - see MP-P3-026 - but pytest.ini has no
effect on this execution path). The two-line shim below makes this
script self-sufficient regardless of how it's invoked, without needing
any change to how it's called.
"""

import sys
import os
import importlib

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


def main():
    print(f"Python version: {sys.version}")
    print(f"Python executable: {sys.executable}\n")

    print("Package versions:")
    packages = ["duckdb", "yfinance", "requests", "pandas", "numpy", "pytest"]
    all_ok = True
    for pkg in packages:
        try:
            mod = importlib.import_module(pkg)
            version = getattr(mod, "__version__", "<no __version__ attribute>")
            print(f"  [OK] {pkg}: {version}")
        except ImportError as e:
            print(f"  [MISSING] {pkg}: {e}")
            all_ok = False

    print("\nProject module imports:")
    project_modules = [
        "config", "db.connection", "ingestion.prices", "ingestion.macro",
        "validation.checks",
    ]
    for mod_name in project_modules:
        try:
            importlib.import_module(mod_name)
            print(f"  [OK] {mod_name}")
        except Exception as e:
            print(f"  [FAIL] {mod_name}: {e!r}")
            all_ok = False

    print(f"\nOverall: {'PASS' if all_ok else 'FAIL'}")
    return all_ok


if __name__ == "__main__":
    ok = main()
    sys.exit(0 if ok else 1)
