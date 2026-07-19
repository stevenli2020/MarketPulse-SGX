"""
Shared settings for the MarketPulse SGX project.

Kept as plain constants (no config-management framework) since this is a
single-developer research project - see PROJECT_SPEC.md Rule 9.

Anything that would otherwise be hardcoded in multiple files should live
here instead, so there is exactly one place to change it.
"""

from pathlib import Path

# --- Storage ---------------------------------------------------------------

# Single DuckDB file for the whole project. Kept out of git (see .gitignore).
PROJECT_ROOT = Path(__file__).resolve().parent
DB_PATH = PROJECT_ROOT / "marketpulse.duckdb"
SCHEMA_PATH = PROJECT_ROOT / "db" / "schema.sql"

# --- Securities --------------------------------------------------------------
# Structured as a list from day one so a second SGX stock can be added later
# without changing any code, only this list (see PROJECT_SPEC.md Section 4).

SECURITIES = [
    # listed_date is intentionally left as None for now rather than
    # populated with an unverified historical date - this project treats
    # unverified "facts" as worse than an explicit gap. The listing-date
    # validation check in validation/checks.py is wired up and will
    # activate automatically once a confirmed listed_date is filled in
    # here (or in dim_securities directly).
    {"ticker": "D05.SI", "name": "DBS Group Holdings", "exchange": "SGX", "listed_date": None},
]

INDICES = [
    {"ticker": "^STI", "name": "Straits Times Index"},
]

# --- Macro series to collect (ingestion not yet implemented) ---------------

MACRO_SERIES = [
    "SORA",
    "US_FED_FUNDS_RATE",
    "SGD_USD_FX",
]

# --- Prediction horizons -----------------------------------------------------
# Used later by labeling/labels.py. Defined here so features and labels
# both reference the same numbers rather than duplicating them.

PREDICTION_HORIZONS_DAYS = [5, 10]

# --- Price ingestion defaults (Phase 2) -------------------------------------

# Full-history backfill by default; Phase 2 is an initial load, not yet an
# incremental daily-update scheduler (that is a later phase's concern).
PRICE_HISTORY_START_DATE = "1990-01-01"

# Explicitly NOT relying on yfinance's default for this argument - see
# ingestion/prices.py and PROJECT_SPEC.md Phase 2 notes for why the
# distinction between actual traded prices and dividend/split-adjusted
# prices must be preserved rather than collapsed by the library.
YFINANCE_AUTO_ADJUST = False

# --- Point-in-time convention for daily price data (Phase 2) ---------------
#
# A daily OHLCV observation for trade_date T becomes available only after
# T's trading session has closed. In this project's schema, that is
# recorded explicitly as availability_date = trade_date (see
# db/schema.sql, prices_daily / index_daily).
#
# This convention governs how price data may be used once feature
# engineering and labeling are built (later phases):
#   - Features computed "as of" date T may use price data through the
#     close of T (i.e. availability_date <= T).
#   - Prediction targets/labels for an observation date T must begin
#     from the next trading date after T, never from T itself or earlier.
#   - No feature or target may use any price row with availability_date
#     later than T when making a prediction as of T.
#
# This is documented here (and restated in db/schema.sql and
# ingestion/prices.py) rather than enforced by new schema changes, per
# Steven's instruction to preserve the current availability_date design.
