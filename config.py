"""
Shared settings for the MarketPulse SGX project.

Kept as plain constants (no config-management framework) since this is a
single-developer research project - see PROJECT_SPEC.md Rule 9.

Anything that would otherwise be hardcoded in multiple files should live
here instead, so there is exactly one place to change it.
"""

from pathlib import Path
import os

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

# --- Macro series to collect (Phase 3) --------------------------------------

MACRO_SERIES = [
    "SORA",
    "US_FED_FUNDS_RATE",
    "SGD_USD_FX",
]

MACRO_HISTORY_START_DATE = "1990-01-01"  # same floor as PRICE_HISTORY_START_DATE

# Optional. Read from the environment only - never hardcoded or committed.
# If unset, US_FED_FUNDS_RATE falls back to FRED's public, no-key CSV
# endpoint (see ingestion/macro.py) rather than requiring registration.
FRED_API_KEY = os.environ.get("FRED_API_KEY")

# Per-series source configuration. Kept as one dict so a source decision
# (URL, field names) lives in exactly one place, per this project's
# existing configuration style (see SECURITIES/INDICES above).
MACRO_SOURCE_CONFIG = {
    "SORA": {
        "source": "MAS_API",
        "base_url": "https://eservices.mas.gov.sg/api/action/datastore/search.json",
        # Official MAS "Domestic Interest Rates" dataset. This resource_id
        # was found via a documented third-party technical walkthrough of
        # the official MAS datastore API (mas.gov.sg/Statistics/APIs/API-
        # Documentation.aspx confirms the API pattern itself), not from
        # MAS's own docs directly, and could not be independently verified
        # against the live endpoint in this development environment (no
        # network access - see PROJECT_STATUS.md). MUST be confirmed on
        # first real run; ingestion/macro.py fails loudly with the actual
        # returned field names if this assumption is wrong, rather than
        # silently mismapping data - see _identify_sora_value_field().
        #
        # UPDATE (2026-07-19, after a live JSONDecodeError - empty/
        # non-JSON response body): a second, independently-sourced
        # technical walkthrough of this same MAS API uses a DIFFERENT
        # resource_id: "5f2b18a8-0883-4769-a635-879c63d3caac". I have not
        # verified which (if either) is currently correct for daily SORA
        # specifically - I'm not swapping this value based on an
        # unverified second source, since that would just be trading one
        # unverified guess for another. What I did change: added a
        # browser-like User-Agent header (see _SORA_REQUEST_HEADERS in
        # ingestion/macro.py), since that second source explicitly needed
        # one, and MAS's eServices platform silently rejecting requests
        # without one is a well-documented pattern for exactly this
        # empty-body symptom. If the enhanced diagnostics (also added)
        # show this resource_id genuinely doesn't exist (e.g. an explicit
        # "not found" in the response body), try the alternate ID above.
        "resource_id": "9a0bf149-308c-4bd2-832d-76c8e6cb47ed",
        "date_field": "end_of_day",
        # Candidate field names for the raw (non-compounded) daily SORA
        # rate - genuinely uncertain which one the live API uses, see
        # comment above. Tried in order; first present column wins.
        "value_field_candidates": ["sora", "sora_rate", "overnight_sora"],
    },
    "US_FED_FUNDS_RATE": {
        "source": "FRED",
        "series_id": "EFFR",
        "csv_url": "https://fred.stlouisfed.org/graph/fredgraph.csv",
        "api_url": "https://api.stlouisfed.org/fred/series/observations",
    },
    "SGD_USD_FX": {
        "source": "yfinance",
        "ticker": "USDSGD=X",  # per instruction: do not substitute SGDUSD=X
    },
}

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
