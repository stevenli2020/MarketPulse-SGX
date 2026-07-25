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

# Required for SORA ingestion (MP-P3-029b, 2026-07-19). Unlike FRED,
# there is no no-key fallback for SORA - the legacy no-auth endpoint is
# permanently retired (see PROJECT_STATUS.md). Obtained by Sprite via
# self-service registration at https://eservices.mas.gov.sg/apimg-portal
# ("API for Domestic Interest Rates - Daily" product). Read from the
# environment only - never hardcoded or committed. If unset, SORA
# ingestion fails loud with a clear, specific message telling the
# operator to set this variable, rather than a confusing HTTP-level error.
MAS_APIMG_SUBSCRIPTION_KEY = os.environ.get("MAS_APIMG_SUBSCRIPTION_KEY")

# Per-series source configuration. Kept as one dict so a source decision
# (URL, field names) lives in exactly one place, per this project's
# existing configuration style (see SECURITIES/INDICES above).
MACRO_SOURCE_CONFIG = {
    "SORA": {
        "source": "MAS_APIMG",
        # RESOLVED 2026-07-19 (MP-P3-029/029b investigation, see
        # PROJECT_STATUS.md for the full story): the legacy CKAN
        # datastore endpoint above was confirmed permanently retired
        # (byte-identical maintenance page for every resource_id tested,
        # including MAS's own documented example). The real, current,
        # PUBLIC production endpoint is MAS's APIMG gateway, confirmed
        # live and working via a real subscription key obtained by
        # Sprite through https://eservices.mas.gov.sg/apimg-portal -
        # "API for Domestic Interest Rates - Daily" product.
        "base_url": (
            "https://eservices.mas.gov.sg/apimg-gw/server/"
            "monthly_statistical_bulletin_non610mssql/domestic_interest_rates_daily/"
            "views/domestic_interest_rates_daily"
        ),
        # Auth: a "keyid" HTTP header (confirmed via MAS's own code
        # sample on the product page - NOT the generic Ocp-Apim-
        # Subscription-Key header a typical Azure APIM setup would use).
        # The key itself is read from the environment at request time
        # (MAS_APIMG_SUBSCRIPTION_KEY) - never hardcoded or committed,
        # matching the existing FRED_API_KEY pattern exactly.
        "auth_header_name": "keyid",
        "date_field": "end_of_day",
        "value_field": "sora",  # confirmed directly against live data - no longer a guess
        # Real MAS-provided publication-date field, confirmed present on
        # recent rows (null on older historical rows, where the fallback
        # business-day convention in ingestion/macro.py is used instead -
        # mirrors the existing FRED vintage-vs-fallback pattern).
        "published_date_field": "published_date",
        # Backend is Denodo (confirmed via the Content-Type response
        # header: application/json;subtype=denodo-8.0). Filtering uses
        # Denodo's OData-style $filter syntax, confirmed working live:
        # $filter=end_of_day >= '2024-01-01' and end_of_day <= '2024-01-10'
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
