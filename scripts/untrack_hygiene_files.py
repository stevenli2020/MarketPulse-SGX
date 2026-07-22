"""
One-time housekeeping utility (2026-07-19): untracks two files from git
that were committed before .gitignore excluded them - a compiled
bytecode file and the DuckDB database file. This does NOT delete either
file from disk - `git rm --cached` only removes them from git's index;
the working-tree copy is untouched. .gitignore already excludes both
patterns (*.duckdb, __pycache__/, *.pyc), confirmed before running this,
so they will correctly stay untracked going forward rather than
reappearing as "untracked files" on the next status check.

Run via execute_python_script rather than the run_git_command MCP tool,
since that tool's supported actions (status/diff/add/commit/push/pull)
don't include 'rm'.
"""
import subprocess
import sys

FILES_TO_UNTRACK = [
    "__pycache__/config.cpython-312.pyc",
    "marketpulse.duckdb",
]


def main():
    all_ok = True
    for f in FILES_TO_UNTRACK:
        print(f"Untracking {f} (file remains on disk, only removed from git's index)...")
        result = subprocess.run(["git", "rm", "--cached", f], capture_output=True, text=True)
        print(result.stdout.strip())
        if result.returncode != 0:
            print(f"  STDERR: {result.stderr.strip()}")
            print(f"  [FAIL] could not untrack {f}")
            all_ok = False
        else:
            print(f"  [OK] {f} untracked")
    return all_ok


if __name__ == "__main__":
    ok = main()
    sys.exit(0 if ok else 1)
