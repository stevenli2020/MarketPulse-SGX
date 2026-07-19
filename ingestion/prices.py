"""
Price/volume data collection (D05.SI, ^STI) - Phase 2.

Data flow (see PROJECT_SPEC.md / PROJECT_STATUS.md Phase 2 notes):

    price_fetches -> raw_price_rows -> validation -> prices_daily / index_daily

- price_fetches:  one row per ingestion ATTEMPT (metadata only - did it
                   succeed, how many rows, any error). Written eagerly
                   (autocommit), outside the normalization transaction -
                   see the hardening note below.
- raw_price_rows: the actual observations received during a successful
                   fetch, referencing the fetch_id that produced them.
                   Never deduplicated across runs - this is the permanent,
                   unprocessed audit trail. Also written eagerly, for the
                   same reason: the raw layer should reflect exactly what
                   was received, regardless of whether normalization
                   downstream succeeds or fails.
- prices_daily / index_daily: the normalized, deduplicated, validated
                   tables everything downstream reads from.

POINT-IN-TIME CONVENTION: a trade_date's data is only knowable after that
session's close, so availability_date is always set equal to trade_date
when writing into prices_daily / index_daily. This means: features may
use price data through the close of trading date T; prediction targets
must begin from the next trading date after T; no feature or target may
use information from T+1 or later when making a prediction as of T.
See config.py for the full statement of this convention.

FAIL-LOUD CONTRACT: if the source returns zero rows, an unexpected
schema, or an otherwise invalid response - including the yfinance call
itself raising an exception (network error, rate limit, etc.) - this
module raises IngestionFailure and writes NOTHING to raw_price_rows or
prices_daily/index_daily. A failed attempt is still recorded in
price_fetches (status='failed'), because that itself is an auditable
fact. Zero rows is never interpreted as "already up to date".

HARDENING NOTE (Phase 2 patch): the normalized-table update portion of
ingestion (upsert -> revision logging -> coverage log update) is wrapped
in an explicit DuckDB transaction. If anything in that portion fails,
the whole transaction is rolled back, so no partial data is ever left in
prices_daily / index_daily. This is deliberately scoped to the
normalization step only, not the raw fetch/raw_price_rows write: a fetch
that genuinely succeeded should stay recorded as a genuine success even
if normalization fails afterward - those are two different facts and
should not be conflated. A normalization failure is raised as
NormalizationFailure (a subclass of IngestionFailure, so existing
callers that catch IngestionFailure still catch it).
"""

from datetime import datetime, date

import pandas as pd
import yfinance

from config import YFINANCE_AUTO_ADJUST, PRICE_HISTORY_START_DATE
from db.connection import get_connection
from validation.checks import validate_price_rows

EXPECTED_COLUMNS = ["Open", "High", "Low", "Close", "Adj Close", "Volume"]

# Fields compared to decide whether an existing normalized row has been
# genuinely revised vs. an idempotent re-fetch of the same values.
# adj_close is only relevant for prices_daily (securities); index_daily
# has no adj_close column - see _relevant_fields().
_COMPARE_FIELDS_SECURITY = ["open", "high", "low", "close", "adj_close", "volume"]
_COMPARE_FIELDS_INDEX = ["open", "high", "low", "close", "volume"]

_FLOAT_TOLERANCE = 1e-6


class IngestionFailure(Exception):
    """Raised when a source response is unusable. No DB writes follow this."""
    pass


class NormalizationFailure(IngestionFailure):
    """
    Raised when the fetch itself succeeded (raw_price_rows was written)
    but something failed while updating the normalized tables. The
    transaction wrapping that step is rolled back before this is raised,
    so prices_daily/index_daily are guaranteed unchanged by the failed
    attempt. Subclasses IngestionFailure so existing callers that catch
    IngestionFailure handle this consistently without special-casing.
    """
    pass


def _next_fetch_id(con) -> int:
    """Simple surrogate key: max existing fetch_id + 1 (0 if table is empty)."""
    result = con.execute("SELECT COALESCE(MAX(fetch_id), 0) FROM price_fetches").fetchone()
    return result[0] + 1


def _next_warning_id(con) -> int:
    result = con.execute("SELECT COALESCE(MAX(warning_id), 0) FROM data_quality_warnings").fetchone()
    return result[0] + 1


def _identify_field_level(columns: pd.MultiIndex, ticker: str):
    """
    Given yfinance's MultiIndex columns, work out which level holds the
    OHLCV field names (Open/High/Low/Close/Adj Close/Volume) rather than
    assuming it is always level 0 - yfinance's level order has changed
    across versions and call shapes.

    Returns the flattened column list. Raises IngestionFailure with the
    actual returned columns if neither level matches - fails clearly
    rather than silently mismapping data into the wrong fields.
    """
    expected = set(EXPECTED_COLUMNS)
    level0 = list(columns.get_level_values(0))
    level1 = list(columns.get_level_values(1)) if columns.nlevels > 1 else []

    if expected.issubset(set(level0)):
        return level0
    if level1 and expected.issubset(set(level1)):
        return level1

    raise IngestionFailure(
        f"{ticker}: could not identify OHLCV field level in MultiIndex "
        f"columns returned by yfinance. Level 0 values: {sorted(set(level0))}. "
        f"Level 1 values: {sorted(set(level1))}. Expected fields: {EXPECTED_COLUMNS}."
    )


def _fetch_raw(ticker: str, start_date: str, end_date: str) -> pd.DataFrame:
    """
    Calls yfinance directly. Raises IngestionFailure on any unusable
    response OR if the call itself raises an exception (network error,
    rate limit, library error, etc.) - the caller is never exposed to a
    raw, un-wrapped yfinance/network exception.
    """
    try:
        df = yfinance.download(
            ticker,
            start=start_date,
            end=end_date,
            auto_adjust=YFINANCE_AUTO_ADJUST,   # explicit, per Phase 2 decision - do not rely on library default
            progress=False,
        )
    except IngestionFailure:
        raise
    except Exception as e:
        # Any exception from the yfinance call itself (network, rate
        # limit, internal library error) is converted here so the
        # caller's fail-loud/price_fetches-logging contract is uniform
        # regardless of *why* the fetch failed.
        raise IngestionFailure(f"{ticker}: yfinance raised an exception during download: {e!r}") from e

    if df is None or not isinstance(df, pd.DataFrame):
        raise IngestionFailure(f"{ticker}: source returned no usable DataFrame")

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = _identify_field_level(df.columns, ticker)

    if df.empty:
        raise IngestionFailure(f"{ticker}: source returned zero rows")

    missing_cols = [c for c in EXPECTED_COLUMNS if c not in df.columns]
    if missing_cols:
        raise IngestionFailure(
            f"{ticker}: source response missing expected columns {missing_cols}. "
            f"Actual columns: {list(df.columns)}."
        )

    return df


def _record_fetch_attempt(con, fetch_id, ticker, entity_type, start_date, end_date,
                           requested_at, status, row_count, error_message):
    con.execute(
        """
        INSERT INTO price_fetches
            (fetch_id, ticker, entity_type, source, source_library_version,
             requested_start_date, requested_end_date, requested_at, fetched_at,
             status, row_count, error_message)
        VALUES (?, ?, ?, 'yfinance', ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            fetch_id, ticker, entity_type, getattr(yfinance, "__version__", "unknown"),
            start_date, end_date, requested_at, datetime.now(),
            status, row_count, error_message,
        ],
    )


def _write_raw_rows(con, fetch_id, ticker, df: pd.DataFrame):
    rows = []
    for idx, row in df.iterrows():
        rows.append((
            fetch_id, ticker, idx.date(),
            _safe_float(row.get("Open")), _safe_float(row.get("High")),
            _safe_float(row.get("Low")), _safe_float(row.get("Close")),
            _safe_float(row.get("Adj Close")), _safe_int(row.get("Volume")),
        ))
    con.executemany(
        """
        INSERT INTO raw_price_rows
            (fetch_id, ticker, trade_date, open, high, low, close, adj_close, volume)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )


def _safe_float(v):
    return None if pd.isna(v) else float(v)


def _safe_int(v):
    return None if pd.isna(v) else int(v)


def _relevant_fields(table: str):
    return _COMPARE_FIELDS_SECURITY if table == "prices_daily" else _COMPARE_FIELDS_INDEX


def _fetch_existing_normalized(con, table, id_col, entity_id, trade_dates):
    """
    Pre-fetches existing normalized rows for the trade_dates in this
    batch, keyed by trade_date, so revision detection can be done in
    Python without one SELECT per row. Must be called inside the same
    transaction as the writes that follow it.
    """
    if not trade_dates:
        return {}
    fields = _relevant_fields(table)
    placeholders = ", ".join(["?"] * len(trade_dates))
    rows = con.execute(
        f"SELECT trade_date, {', '.join(fields)} FROM {table} "
        f"WHERE {id_col} = ? AND trade_date IN ({placeholders})",
        [entity_id, *trade_dates],
    ).fetchall()
    existing = {}
    for r in rows:
        trade_date = r[0]
        existing[trade_date] = dict(zip(fields, r[1:]))
    return existing


def _values_differ(old: dict, new: dict, fields) -> list:
    """Returns the list of field names whose values differ (float-tolerant)."""
    changed = []
    for f in fields:
        old_v, new_v = old.get(f), new.get(f)
        if old_v is None and new_v is None:
            continue
        if old_v is None or new_v is None:
            changed.append(f)
            continue
        if f == "volume":
            if int(old_v) != int(new_v):
                changed.append(f)
        else:
            if abs(float(old_v) - float(new_v)) > _FLOAT_TOLERANCE:
                changed.append(f)
    return changed


def _insert_normalized_row(con, table, id_col, entity_id, r):
    if table == "prices_daily":
        con.execute(
            f"""
            INSERT INTO {table}
                ({id_col}, trade_date, availability_date, open, high, low, close, adj_close, volume, source, ingested_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'yfinance', ?)
            """,
            [entity_id, r["trade_date"], r["trade_date"], r["open"], r["high"],
             r["low"], r["close"], r["adj_close"], r["volume"], datetime.now()],
        )
    else:  # index_daily - no adj_close column
        con.execute(
            f"""
            INSERT INTO {table}
                ({id_col}, trade_date, availability_date, open, high, low, close, volume, source, ingested_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'yfinance', ?)
            """,
            [entity_id, r["trade_date"], r["trade_date"], r["open"], r["high"],
             r["low"], r["close"], r["volume"], datetime.now()],
        )


def _update_normalized_row(con, table, id_col, entity_id, r, fields):
    set_clause = ", ".join(f"{f} = ?" for f in fields)
    values = [r[f] for f in fields] + [datetime.now(), entity_id, r["trade_date"]]
    con.execute(
        f"UPDATE {table} SET {set_clause}, ingested_at = ? WHERE {id_col} = ? AND trade_date = ?",
        values,
    )


def _upsert_normalized(con, table, id_col, entity_id, valid_rows):
    """
    Classifies each valid row as insert / unchanged-skip / revised-update
    against what is already stored, then writes only what actually needs
    to change. MUST be called inside an explicit transaction opened by
    the caller (_ingest_one) - this function does not commit or roll
    back itself. Returns (inserted, updated, unchanged, revision_events).
    """
    fields = _relevant_fields(table)
    trade_dates = [r["trade_date"] for r in valid_rows]
    existing = _fetch_existing_normalized(con, table, id_col, entity_id, trade_dates)

    inserted, updated, unchanged = 0, 0, 0
    revision_events = []

    for r in valid_rows:
        td = r["trade_date"]
        new_values = {f: r.get(f) for f in fields}

        if td not in existing:
            _insert_normalized_row(con, table, id_col, entity_id, r)
            inserted += 1
            continue

        changed_fields = _values_differ(existing[td], new_values, fields)
        if not changed_fields:
            unchanged += 1
            continue

        _update_normalized_row(con, table, id_col, entity_id, r, fields)
        updated += 1
        revision_events.append({
            "trade_date": td,
            "changed_fields": changed_fields,
            "old_values": {f: existing[td].get(f) for f in changed_fields},
            "new_values": {f: new_values.get(f) for f in changed_fields},
        })

    return inserted, updated, unchanged, revision_events


def _write_warning(con, warning_type, ticker, trade_date, detail):
    wid = _next_warning_id(con)
    con.execute(
        "INSERT INTO data_quality_warnings (warning_id, warning_type, ticker, trade_date, detail, detected_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [wid, warning_type, ticker, trade_date, detail, datetime.now()],
    )


def _lookup_listed_date(con, ticker):
    row = con.execute("SELECT listed_date FROM dim_securities WHERE ticker = ?", [ticker]).fetchone()
    return row[0] if row else None


def _ingest_one(ticker: str, entity_type: str, table: str, id_col: str, entity_id: int) -> dict:
    """
    Runs the full flow for one ticker: fetch -> log attempt -> raw rows ->
    validate -> [TRANSACTION: upsert normalized -> log revisions/warnings
    -> update coverage] -> commit or rollback. Returns a summary dict.

    Raises IngestionFailure if the fetch itself is unusable (no DB writes
    of price data at all). Raises NormalizationFailure if the fetch
    succeeded but something failed while updating the normalized tables
    (the transaction is rolled back first, so no partial normalized data
    remains in prices_daily/index_daily).
    """
    con = get_connection()
    requested_at = datetime.now()
    start_date = PRICE_HISTORY_START_DATE
    end_date = date.today().isoformat()
    fetch_id = _next_fetch_id(con)

    try:
        df = _fetch_raw(ticker, start_date, end_date)
    except IngestionFailure as e:
        _record_fetch_attempt(con, fetch_id, ticker, entity_type, start_date, end_date,
                               requested_at, "failed", 0, str(e))
        raise

    # Fetch succeeded: log the attempt and write the raw, unprocessed
    # rows eagerly (outside the normalization transaction - see module
    # docstring for why these two facts are kept separate).
    _record_fetch_attempt(con, fetch_id, ticker, entity_type, start_date, end_date,
                           requested_at, "success", len(df), None)
    _write_raw_rows(con, fetch_id, ticker, df)

    raw_rows = con.execute(
        "SELECT trade_date, open, high, low, close, adj_close, volume "
        "FROM raw_price_rows WHERE fetch_id = ?", [fetch_id]
    ).fetchdf()

    listed_date = _lookup_listed_date(con, ticker) if entity_type == "security" else None
    valid_rows, rejected, warnings = validate_price_rows(raw_rows, ticker, entity_type, listed_date)

    # --- Normalization transaction: all-or-nothing from here on --------
    con.execute("BEGIN TRANSACTION")
    try:
        inserted, updated, unchanged, revision_events = _upsert_normalized(
            con, table, id_col, entity_id, valid_rows
        )

        for w in warnings:
            _write_warning(con, w["warning_type"], ticker, w["trade_date"], w["detail"])

        for rev in revision_events:
            detail = (
                f"{ticker} {rev['trade_date']}: revised fields {rev['changed_fields']} "
                f"(old={rev['old_values']}, new={rev['new_values']})"
            )
            _write_warning(con, "price_revision_detected", ticker, rev["trade_date"], detail)

        coverage = con.execute(
            f"SELECT MIN(trade_date), MAX(trade_date), COUNT(*) FROM {table} WHERE {id_col} = ?",
            [entity_id],
        ).fetchone()
        con.execute(
            """
            INSERT INTO data_availability_log (table_name, entity_type, entity_id, coverage_start, coverage_end, last_updated, row_count)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (table_name, entity_type, entity_id) DO UPDATE SET
                coverage_start = excluded.coverage_start, coverage_end = excluded.coverage_end,
                last_updated = excluded.last_updated, row_count = excluded.row_count
            """,
            [table, entity_type, entity_id, coverage[0], coverage[1], datetime.now(), coverage[2]],
        )

        con.execute("COMMIT")
    except Exception as e:
        con.execute("ROLLBACK")
        raise NormalizationFailure(
            f"{ticker}: normalization failed after a successful fetch (fetch_id={fetch_id}); "
            f"transaction rolled back, no partial data left in {table}: {e!r}"
        ) from e

    return {
        "ticker": ticker,
        "rows_received": len(df),
        "rows_rejected": len(rejected),
        "rows_inserted": inserted,
        "rows_updated_revised": updated,
        "rows_unchanged": unchanged,
        "warnings": len(warnings),
        "revisions": len(revision_events),
        "coverage_start": coverage[0],
        "coverage_end": coverage[1],
        "rejected_detail": rejected,
    }


def fetch_security_prices(ticker: str, security_id: int) -> dict:
    """Fetch and store daily OHLCV for one security into prices_daily."""
    return _ingest_one(ticker, "security", "prices_daily", "security_id", security_id)


def fetch_index_prices(ticker: str, index_id: int) -> dict:
    """Fetch and store daily OHLCV for one index into index_daily."""
    return _ingest_one(ticker, "index", "index_daily", "index_id", index_id)
