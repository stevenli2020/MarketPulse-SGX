"""
Interest-rate / FX macro data collection (SORA, US Fed funds rate,
SGD/USD FX) - Phase 3.

SCOPE: this module fetches, normalizes, validates, and stores macro time
series into raw_macro_series. It does NOT consume this data for
features, labels, or models - that is explicitly Phase 4+ and is not
designed here.

Reuses the Phase 2 architectural decisions rather than inventing new
ones: IngestionFailure/NormalizationFailure (imported from
ingestion.prices, not redefined), explicit-transaction normalized
writes, fail-loud on unusable source responses, idempotent-vs-revision
upsert classification, and the data_quality_warnings mechanism.

Writes directly into the existing raw_macro_series table - no new
"macro_fetches"/"raw_macro_rows" preservation tables are introduced
(deliberate project decision, not an oversight).

Common internal representation every source adapter converges to,
before being written to raw_macro_series:
    {"series_id": str, "obs_date": date, "value": float,
     "as_of_date": date, "source": str}

POINT-IN-TIME CONVENTIONS (see PROJECT_STATUS.md Phase 3 entry for the
full rationale):
  - SORA: as_of_date = obs_date + 1 Singapore business day (weekday-only
    approximation via pandas BDay - does not account for SG public
    holidays; no holiday-calendar dependency added, per instruction).
    This matches MAS's own stated publication practice (SORA for a given
    business day is published the next business day).
  - US_FED_FUNDS_RATE (EFFR): if FRED_API_KEY is set, uses FRED's
    real vintage/realtime metadata (realtime_start) as as_of_date - the
    actual first-published date for that observation. If unset (the
    no-key public CSV path), the CSV endpoint carries no vintage
    metadata, so the documented fallback as_of_date = obs_date + 1
    business day is used instead - never silently treated as
    as_of_date == obs_date.
  - SGD_USD_FX: as_of_date = obs_date = trade_date, matching the
    existing Phase 2 equity/index convention (a trading day's data is
    knowable after that session's close).
"""

import io
from datetime import date, datetime, timedelta

import pandas as pd
import requests
import yfinance

from config import (
    FRED_API_KEY, MACRO_HISTORY_START_DATE, MACRO_SOURCE_CONFIG,
)
from db.connection import get_connection
from ingestion.prices import IngestionFailure, NormalizationFailure
from validation.checks import validate_macro_rows

_FLOAT_TOLERANCE = 1e-9


# =============================================================================
# Business-day arithmetic (SORA / FRED fallback as_of_date)
# =============================================================================

def _plus_one_business_day(d: date) -> date:
    """
    d + 1 business day, weekday-only (Mon-Fri). Does NOT account for
    Singapore (or US) public holidays - a disclosed, deliberate
    limitation, matching the instruction not to add an external
    calendar/holiday package. Uses pandas' built-in BDay offset only.
    """
    return (pd.Timestamp(d) + pd.tseries.offsets.BDay(1)).date()


# =============================================================================
# SORA (MAS official API)
# =============================================================================

def _identify_sora_value_field(sample_record: dict, series_id: str) -> str:
    """
    The exact field name for the raw daily SORA rate in MAS's API
    response is not independently confirmed (see config.py comment on
    MACRO_SOURCE_CONFIG["SORA"]). Tries each candidate in order; fails
    loud with the actual returned field names if none match, rather than
    silently reading the wrong column - the same defensive pattern used
    for yfinance's MultiIndex column identification in Phase 2.
    """
    candidates = MACRO_SOURCE_CONFIG["SORA"]["value_field_candidates"]
    for c in candidates:
        if c in sample_record:
            return c
    raise IngestionFailure(
        f"{series_id}: could not identify the SORA value field in the MAS API "
        f"response. Tried {candidates}. Actual fields returned: {sorted(sample_record.keys())}. "
        f"config.py's MACRO_SOURCE_CONFIG['SORA']['value_field_candidates'] needs updating."
    )


def _describe_response_for_diagnostics(resp) -> str:
    """
    Builds a human-readable diagnostic block from an HTTP response, for
    when a request "succeeds" at the transport level (no exception, no
    4xx/5xx) but the body isn't the JSON we expected - exactly the
    reported JSONDecodeError: "Expecting value: line 1 column 1 (char 0)",
    which happens when .json() is called on an empty or non-JSON body.
    A bare exception message doesn't say WHY; this does.
    """
    content_type = resp.headers.get("Content-Type", "<not set>")
    body_snippet = resp.text[:500] if resp.text else "<empty body>"
    looks_like_html = resp.text.strip().lower().startswith(("<!doctype", "<html"))
    looks_like_json = resp.text.strip().startswith(("{", "["))

    if not resp.text or not resp.text.strip():
        interpretation = (
            "Response body is completely empty. Common causes for MAS's eServices "
            "API specifically: the request was blocked before reaching the actual "
            "API handler (e.g. a WAF/security layer rejecting requests without a "
            "browser-like User-Agent header - see the User-Agent now sent by this "
            "client), rate limiting, or the resource_id no longer exists."
        )
    elif looks_like_html:
        interpretation = (
            "Response body is HTML, not JSON - almost certainly an error page, "
            "login/block page, or a redirect target, not the datastore API response. "
            "This points to a wrong URL/resource_id or a request being intercepted "
            "before reaching the API, not a transient network issue."
        )
    elif looks_like_json:
        interpretation = (
            "Response body appears to start like JSON but failed to parse - likely "
            "truncated (a network/timeout issue mid-response) or malformed."
        )
    else:
        interpretation = (
            "Response body does not look like JSON or HTML - inspect the raw body "
            "snippet below directly."
        )

    return (
        f"HTTP status: {resp.status_code}\n"
        f"Final URL: {resp.url}\n"
        f"Content-Type: {content_type}\n"
        f"Response headers: {dict(resp.headers)}\n"
        f"Body looks like JSON: {looks_like_json} | Body looks like HTML: {looks_like_html}\n"
        f"First 500 chars of body: {body_snippet!r}\n"
        f"Engineering interpretation: {interpretation}"
    )


# Government/institutional endpoints (MAS's eServices platform included)
# commonly reject or silently short-circuit requests that don't carry a
# browser-like User-Agent, returning an empty or non-JSON body rather
# than a clean error status - exactly the reported symptom. This is a
# defensive, standard hardening step for calling such APIs, not a
# fabricated fix - added after finding a working third-party example of
# this exact API that explicitly sets one. Does not change the request
# semantics (same URL, same params) otherwise.
_SORA_REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json",
}


def _fetch_sora_raw(start_date: str, end_date: str) -> list:
    """
    Fetches raw SORA records from the official MAS datastore API,
    paginating via offset (the API caps at 100 rows per call).
    Raises IngestionFailure on any unusable response or request
    exception - never returns partial results silently, never retries
    indefinitely (each call is attempted exactly once), never
    substitutes fallback data.

    DIAGNOSTIC ENHANCEMENT (2026-07-19, after a live JSONDecodeError):
    a bare ".json() failed" message doesn't say why. On any failure to
    parse the response as JSON, the full diagnostic block from
    _describe_response_for_diagnostics() is included in the
    IngestionFailure message - status code, headers, Content-Type, a
    body snippet, the final URL, JSON-vs-HTML detection, and a plain-
    English interpretation - so the actual cause is visible from the
    error message itself, not just "it failed".
    """
    cfg = MACRO_SOURCE_CONFIG["SORA"]
    all_records = []
    offset = 0
    limit = 100

    while True:
        params = {
            "resource_id": cfg["resource_id"],
            "between[{}]".format(cfg["date_field"]): f"{start_date},{end_date}",
            "limit": limit,
            "offset": offset,
            "sort": f"{cfg['date_field']} asc",
        }
        try:
            resp = requests.get(cfg["base_url"], params=params, headers=_SORA_REQUEST_HEADERS, timeout=30)
        except IngestionFailure:
            raise
        except Exception as e:
            # A genuine transport-level exception (DNS failure, connection
            # refused, timeout) - not a "successful" response with a bad
            # body, which is handled separately below with richer context.
            raise IngestionFailure(f"SORA: MAS API request failed (transport-level exception): {e!r}") from e

        try:
            resp.raise_for_status()
        except Exception as e:
            raise IngestionFailure(
                f"SORA: MAS API returned an HTTP error status.\n{_describe_response_for_diagnostics(resp)}\n"
                f"Underlying exception: {e!r}"
            ) from e

        try:
            payload = resp.json()
        except Exception as e:
            raise IngestionFailure(
                f"SORA: MAS API response could not be parsed as JSON.\n{_describe_response_for_diagnostics(resp)}\n"
                f"Underlying exception: {e!r}"
            ) from e

        result = payload.get("result")
        if result is None or "records" not in result:
            raise IngestionFailure(
                f"SORA: unexpected MAS API response shape - missing result.records. "
                f"Top-level keys: {sorted(payload.keys())}."
            )

        records = result["records"]
        all_records.extend(records)

        total = result.get("total", len(all_records))
        offset += limit
        if offset >= int(total) or not records:
            break

    if not all_records:
        raise IngestionFailure("SORA: MAS API returned zero records for the requested range")

    return all_records


def _normalize_sora(records: list) -> list:
    cfg = MACRO_SOURCE_CONFIG["SORA"]
    value_field = _identify_sora_value_field(records[0], "SORA")

    normalized = []
    for rec in records:
        raw_date = rec.get(cfg["date_field"])
        raw_value = rec.get(value_field)
        if raw_date is None or raw_value in (None, "", "NA", "-"):
            continue  # skipped here, not silently coerced; validate_macro_rows
                      # would reject it anyway - filtering here avoids a
                      # parse error on non-numeric placeholder values.
        obs_date = pd.to_datetime(raw_date).date()
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            continue
        as_of_date = _plus_one_business_day(obs_date)
        normalized.append({
            "series_id": "SORA", "obs_date": obs_date, "value": value,
            "as_of_date": as_of_date, "source": "MAS_API",
        })
    return normalized


# =============================================================================
# US Fed Funds Rate (FRED EFFR)
# =============================================================================

def _fetch_fred_vintage_json(series_id: str, start_date: str, end_date: str) -> list:
    """
    Used only when FRED_API_KEY is set. Requests the FULL vintage history
    (realtime_start=1776-07-04, the FRED convention for "all vintages")
    so each observation's own realtime_start reflects the date THAT
    value was first published - genuine release-date metadata, not an
    approximation.
    """
    cfg = MACRO_SOURCE_CONFIG["US_FED_FUNDS_RATE"]
    params = {
        "series_id": cfg["series_id"],
        "api_key": FRED_API_KEY,
        "file_type": "json",
        "observation_start": start_date,
        "observation_end": end_date,
        "realtime_start": "1776-07-04",
        "realtime_end": "9999-12-31",
    }
    try:
        resp = requests.get(cfg["api_url"], params=params, timeout=30)
        resp.raise_for_status()
        payload = resp.json()
    except Exception as e:
        raise IngestionFailure(f"US_FED_FUNDS_RATE: FRED API request failed: {e!r}") from e

    observations = payload.get("observations")
    if observations is None:
        raise IngestionFailure(
            f"US_FED_FUNDS_RATE: unexpected FRED API response shape - missing "
            f"'observations'. Top-level keys: {sorted(payload.keys())}."
        )
    if not observations:
        raise IngestionFailure("US_FED_FUNDS_RATE: FRED API returned zero observations")

    return observations


def _fetch_fred_csv(start_date: str, end_date: str) -> pd.DataFrame:
    """
    No-API-key fallback: FRED's public fredgraph.csv endpoint. Carries
    no vintage/release metadata - see module docstring for the resulting
    as_of_date fallback convention.
    """
    cfg = MACRO_SOURCE_CONFIG["US_FED_FUNDS_RATE"]
    try:
        resp = requests.get(cfg["csv_url"], params={"id": cfg["series_id"]}, timeout=30)
        resp.raise_for_status()
    except Exception as e:
        raise IngestionFailure(f"US_FED_FUNDS_RATE: FRED CSV request failed: {e!r}") from e

    try:
        df = pd.read_csv(io.StringIO(resp.text), na_values=["."])
    except Exception as e:
        raise IngestionFailure(f"US_FED_FUNDS_RATE: could not parse FRED CSV response: {e!r}") from e

    if df.empty:
        raise IngestionFailure("US_FED_FUNDS_RATE: FRED CSV returned zero rows")

    # Modern fredgraph.csv columns: "observation_date", "<series_id>".
    # Fall back to the legacy "DATE"/"VALUE" shape defensively rather
    # than assuming - fails loud with actual columns if neither matches.
    date_col = None
    value_col = None
    if "observation_date" in df.columns and cfg["series_id"] in df.columns:
        date_col, value_col = "observation_date", cfg["series_id"]
    elif "DATE" in df.columns and "VALUE" in df.columns:
        date_col, value_col = "DATE", "VALUE"
    else:
        raise IngestionFailure(
            f"US_FED_FUNDS_RATE: unrecognized FRED CSV column shape. "
            f"Actual columns: {list(df.columns)}."
        )

    df["_obs_date"] = pd.to_datetime(df[date_col]).dt.date
    df["_value"] = pd.to_numeric(df[value_col], errors="coerce")

    start = pd.to_datetime(start_date).date()
    end = pd.to_datetime(end_date).date()
    df = df[(df["_obs_date"] >= start) & (df["_obs_date"] <= end)]

    return df


def _normalize_fred(start_date: str, end_date: str) -> list:
    normalized = []

    if FRED_API_KEY:
        observations = _fetch_fred_vintage_json("EFFR", start_date, end_date)
        for obs in observations:
            if obs.get("value") in (None, ".", ""):
                continue
            try:
                value = float(obs["value"])
                obs_date = pd.to_datetime(obs["date"]).date()
                as_of_date = pd.to_datetime(obs["realtime_start"]).date()
            except (KeyError, TypeError, ValueError):
                continue
            normalized.append({
                "series_id": "US_FED_FUNDS_RATE", "obs_date": obs_date, "value": value,
                "as_of_date": as_of_date, "source": "FRED_API_vintage",
            })
    else:
        df = _fetch_fred_csv(start_date, end_date)
        for _, row in df.iterrows():
            if pd.isna(row["_value"]):
                continue
            obs_date = row["_obs_date"]
            normalized.append({
                "series_id": "US_FED_FUNDS_RATE", "obs_date": obs_date, "value": float(row["_value"]),
                # Documented fallback - the CSV path has no vintage
                # metadata, so as_of_date is NOT set equal to obs_date.
                "as_of_date": _plus_one_business_day(obs_date), "source": "FRED_CSV_fallback",
            })

    return normalized


# =============================================================================
# SGD/USD FX (yfinance)
# =============================================================================

def _fetch_fx_raw(ticker: str, start_date: str, end_date: str) -> pd.DataFrame:
    try:
        df = yfinance.download(ticker, start=start_date, end=end_date,
                                auto_adjust=False, progress=False)
    except Exception as e:
        raise IngestionFailure(f"SGD_USD_FX: yfinance raised an exception during download: {e!r}") from e

    if df is None or not isinstance(df, pd.DataFrame):
        raise IngestionFailure("SGD_USD_FX: source returned no usable DataFrame")

    if isinstance(df.columns, pd.MultiIndex):
        # Same defensive level-identification as ingestion/prices.py -
        # not assuming level 0 is always the field name.
        level0 = set(df.columns.get_level_values(0))
        level1 = set(df.columns.get_level_values(1)) if df.columns.nlevels > 1 else set()
        if "Close" in level0:
            df.columns = df.columns.get_level_values(0)
        elif "Close" in level1:
            df.columns = df.columns.get_level_values(1)
        else:
            raise IngestionFailure(
                f"SGD_USD_FX: could not identify 'Close' field in MultiIndex columns. "
                f"Level 0: {sorted(level0)}. Level 1: {sorted(level1)}."
            )

    if df.empty:
        raise IngestionFailure("SGD_USD_FX: source returned zero rows")

    if "Close" not in df.columns:
        raise IngestionFailure(f"SGD_USD_FX: 'Close' column missing. Actual columns: {list(df.columns)}.")

    return df


def _normalize_fx(df: pd.DataFrame) -> list:
    normalized = []
    for idx, row in df.iterrows():
        close = row.get("Close")
        if pd.isna(close):
            continue
        trade_date = idx.date()
        normalized.append({
            "series_id": "SGD_USD_FX", "obs_date": trade_date, "value": float(close),
            # Phase 2 market-data convention: knowable after session close,
            # obs_date == as_of_date - see module docstring.
            "as_of_date": trade_date, "source": "yfinance",
        })
    return normalized


# =============================================================================
# Common: validate -> transaction-wrapped upsert -> report
# =============================================================================

def _next_warning_id(con) -> int:
    result = con.execute("SELECT COALESCE(MAX(warning_id), 0) FROM data_quality_warnings").fetchone()
    return result[0] + 1


def _write_macro_warning(con, warning_type, series_id, obs_date, detail):
    wid = _next_warning_id(con)
    con.execute(
        "INSERT INTO data_quality_warnings (warning_id, warning_type, ticker, trade_date, detail, detected_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [wid, warning_type, series_id, obs_date, detail, datetime.now()],
    )


def _fetch_existing_macro(con, series_id: str, obs_dates: list) -> dict:
    """
    Pre-fetches existing rows for this series/batch, keyed by the FULL
    (obs_date, as_of_date) vintage tuple - matching the real PK
    (series_id, obs_date, as_of_date) exactly.

    FIX (Macha audit, Issue 1): a prior version of this function keyed
    the lookup dict by obs_date alone. If more than one as_of_date
    vintage existed for the same obs_date (e.g. a FRED_API_KEY being
    added between runs, switching the vintage convention for observations
    already stored under the fallback convention), the dict comprehension
    would silently keep only one of them - whichever the SQL happened to
    return last, in unspecified order - and _upsert_macro_series would
    then compare against the wrong vintage. Keying by the full tuple
    makes each vintage individually visible, with no ambiguity.
    """
    if not obs_dates:
        return {}
    placeholders = ", ".join(["?"] * len(obs_dates))
    rows = con.execute(
        f"SELECT obs_date, as_of_date, value FROM raw_macro_series "
        f"WHERE series_id = ? AND obs_date IN ({placeholders})",
        [series_id, *obs_dates],
    ).fetchall()

    existing_by_vintage = {}       # (obs_date, as_of_date) -> value
    existing_as_of_by_obs = {}     # obs_date -> set of as_of_dates on record
    for obs_date, as_of_date, value in rows:
        existing_by_vintage[(obs_date, as_of_date)] = value
        existing_as_of_by_obs.setdefault(obs_date, set()).add(as_of_date)

    return {"by_vintage": existing_by_vintage, "as_of_by_obs": existing_as_of_by_obs}


def _upsert_macro_series(con, series_id: str, valid_rows: list):
    """
    Classifies each row against what is already stored for the EXACT
    (obs_date, as_of_date) vintage, mirroring the spirit of
    ingestion/prices.py::_upsert_normalized but corrected (see
    _fetch_existing_macro's docstring) to key on the full vintage tuple
    rather than obs_date alone. MUST be called inside an explicit
    transaction opened by the caller. No ON CONFLICT clause is used -
    correctness instead relies on this function's own classification
    being right, which the vintage-tuple keying now guarantees: a
    lookup miss means no row exists for that exact (obs_date, as_of_date)
    pair, so INSERT can never collide with an existing PK.

    Returns (inserted, updated, unchanged, revision_events).
    """
    obs_dates = [r["obs_date"] for r in valid_rows]
    existing = _fetch_existing_macro(con, series_id, obs_dates)
    existing_by_vintage = existing["by_vintage"] if existing else {}
    existing_as_of_by_obs = existing["as_of_by_obs"] if existing else {}

    inserted, updated, unchanged = 0, 0, 0
    revision_events = []

    for r in valid_rows:
        od = r["obs_date"]
        as_of = r["as_of_date"]
        prior_value = existing_by_vintage.get((od, as_of))

        if prior_value is None:
            # No row exists for this exact (obs_date, as_of_date) vintage.
            # If OTHER as_of_date vintages exist for this same obs_date,
            # that's a genuine, informative signal (e.g. a vintage-
            # convention change) - logged as its own event type, distinct
            # from a value revision, rather than conflated with one.
            other_vintages = existing_as_of_by_obs.get(od, set()) - {as_of}
            con.execute(
                "INSERT INTO raw_macro_series (series_id, obs_date, value, as_of_date, source, ingested_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [series_id, od, r["value"], as_of, r["source"], datetime.now()],
            )
            inserted += 1
            if other_vintages:
                revision_events.append({
                    "obs_date": od,
                    "event_type": "new_vintage_for_existing_obs_date",
                    "old_value": f"existing as_of_date(s)={sorted(other_vintages)}",
                    "new_value": f"as_of_date={as_of}, value={r['value']}",
                })
            continue

        same_value = abs(float(prior_value) - float(r["value"])) <= _FLOAT_TOLERANCE
        if same_value:
            unchanged += 1
            continue

        con.execute(
            "UPDATE raw_macro_series SET value = ?, ingested_at = ? "
            "WHERE series_id = ? AND obs_date = ? AND as_of_date = ?",
            [r["value"], datetime.now(), series_id, od, as_of],
        )
        updated += 1
        revision_events.append({
            "obs_date": od, "event_type": "value_revision",
            "old_value": prior_value, "new_value": r["value"],
        })

    return inserted, updated, unchanged, revision_events


def _ingest_one_series(series_id: str, normalize_fn) -> dict:
    """
    Shared orchestration for all three macro series: normalize -> validate
    -> [TRANSACTION: upsert -> revision/warning logging] -> commit or
    rollback. Mirrors ingestion/prices.py::_ingest_one's structure.

    normalize_fn is a zero-arg callable that performs the fetch AND
    normalization for one series, returning the common-shape record list,
    or raises IngestionFailure. Fetch failures are NOT written to
    raw_macro_series at all (no separate raw-preservation table exists
    for macro, per instruction - so there is nothing to write on failure
    beyond a data_quality_warnings entry, unlike Phase 2's price_fetches).
    """
    con = get_connection()

    try:
        records = normalize_fn()
    except IngestionFailure as e:
        _write_macro_warning(con, "macro_fetch_failed", series_id, None, str(e))
        raise

    valid_rows, rejected, warnings = validate_macro_rows(records, series_id)

    con.execute("BEGIN TRANSACTION")
    try:
        inserted, updated, unchanged, revision_events = _upsert_macro_series(con, series_id, valid_rows)

        for w in warnings:
            _write_macro_warning(con, w["warning_type"], series_id, w["obs_date"], w["detail"])

        for rev in revision_events:
            detail = f"{series_id} {rev['obs_date']}: {rev['old_value']} -> {rev['new_value']}"
            warning_type = "macro_revision_detected" if rev["event_type"] == "value_revision" else "macro_new_vintage_for_existing_obs_date"
            _write_macro_warning(con, warning_type, series_id, rev["obs_date"], detail)

        con.execute("COMMIT")
    except Exception as e:
        con.execute("ROLLBACK")
        raise NormalizationFailure(
            f"{series_id}: normalization failed after a successful fetch; "
            f"transaction rolled back, no partial data left in raw_macro_series: {e!r}"
        ) from e

    coverage = con.execute(
        "SELECT MIN(obs_date), MAX(obs_date), COUNT(*) FROM raw_macro_series WHERE series_id = ?",
        [series_id],
    ).fetchone()

    return {
        "series_id": series_id,
        "rows_received": len(records),
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


def fetch_macro_series(series_id: str) -> dict:
    """
    Fetch and store one macro series into raw_macro_series. Dispatches
    to the correct source based on series_id. Raises IngestionFailure
    on any unusable source response, NormalizationFailure if the fetch
    succeeded but the transactional write failed (rolled back first).
    """
    start_date = MACRO_HISTORY_START_DATE
    end_date = date.today().isoformat()

    if series_id == "SORA":
        return _ingest_one_series("SORA", lambda: _normalize_sora(_fetch_sora_raw(start_date, end_date)))
    elif series_id == "US_FED_FUNDS_RATE":
        return _ingest_one_series("US_FED_FUNDS_RATE", lambda: _normalize_fred(start_date, end_date))
    elif series_id == "SGD_USD_FX":
        ticker = MACRO_SOURCE_CONFIG["SGD_USD_FX"]["ticker"]
        return _ingest_one_series("SGD_USD_FX", lambda: _normalize_fx(_fetch_fx_raw(ticker, start_date, end_date)))
    else:
        raise ValueError(f"Unknown macro series_id: {series_id!r}")
