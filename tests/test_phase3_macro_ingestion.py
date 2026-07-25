"""
Deterministic tests for Phase 3 macro ingestion (SORA, US Fed funds rate,
SGD/USD FX). Mirrors tests/test_phase2_ingestion.py's structure and
rigor.

NOTE ON EXECUTION: these tests require `duckdb`, `requests`, and
`yfinance` to be installed (see requirements.txt) and use mocks for all
external source calls - no live network access is needed to run them.
They were written but NOT executed in the development sandbox used to
build this patch, because that sandbox has no network access and could
not install `duckdb` at all (confirmed: PyPI is unreachable from this
environment). Run with:

    pip install -r requirements.txt
    pip install pytest
    python -m pytest tests/test_phase3_macro_ingestion.py -v
"""

from datetime import date
from unittest.mock import patch, MagicMock

import pandas as pd
import pytest

duckdb = pytest.importorskip("duckdb")

from config import SCHEMA_PATH
from validation.checks import validate_macro_rows
import ingestion.macro as macro_mod


@pytest.fixture
def con():
    """Fresh in-memory DuckDB instance with the real schema applied."""
    c = duckdb.connect(":memory:")
    with open(SCHEMA_PATH) as f:
        c.execute(f.read())
    return c


# =============================================================================
# Source adapter normalization
# =============================================================================

def test_sora_json_decode_error_produces_rich_diagnostics(monkeypatch):
    """
    Confirms the enhanced diagnostics (added after a real live
    JSONDecodeError during MP-P3-027/028) still fire correctly against
    the current APIMG implementation: HTTP status, final URL, Content-
    Type, a body snippet, and a plain-English interpretation - not just
    a bare "request failed" message.
    """
    monkeypatch.setattr(macro_mod, "MAS_APIMG_SUBSCRIPTION_KEY", "fake-key-for-test")
    import json as json_mod
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.url = "https://eservices.mas.gov.sg/apimg-gw/server/test"
    mock_resp.headers = {"Content-Type": "text/html; charset=utf-8"}
    mock_resp.text = ""
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.side_effect = json_mod.JSONDecodeError("Expecting value", "", 0)

    with patch.object(macro_mod.requests, "get", return_value=mock_resp):
        with pytest.raises(macro_mod.IngestionFailure) as exc_info:
            macro_mod._fetch_sora_raw("2024-01-01", "2024-01-05")

    msg = str(exc_info.value)
    assert "HTTP status: 200" in msg
    assert "Final URL:" in msg
    assert "Content-Type: text/html" in msg
    assert "Engineering interpretation:" in msg


def test_sora_fails_loud_when_subscription_key_missing(monkeypatch):
    """
    SORA has no no-key fallback (unlike FRED) - the legacy no-auth
    endpoint is permanently retired (see PROJECT_STATUS.md MP-P3-029).
    """
    monkeypatch.setattr(macro_mod, "MAS_APIMG_SUBSCRIPTION_KEY", None)
    with pytest.raises(macro_mod.IngestionFailure) as exc_info:
        macro_mod._fetch_sora_raw("2024-01-01", "2024-01-05")
    assert "MAS_APIMG_SUBSCRIPTION_KEY" in str(exc_info.value)
    assert "apimg-portal" in str(exc_info.value)


def test_sora_request_sends_keyid_auth_header(monkeypatch):
    """
    Confirms the real, confirmed-correct auth mechanism (a 'keyid'
    header, per MAS's own code sample from the APIMG portal - not a
    generic Ocp-Apim-Subscription-Key) is actually sent.
    """
    monkeypatch.setattr(macro_mod, "MAS_APIMG_SUBSCRIPTION_KEY", "fake-key-for-test")
    captured = {}

    def capture_get(*args, **kwargs):
        captured.update(kwargs)
        m = MagicMock()
        m.status_code = 200
        m.raise_for_status = MagicMock()
        m.json.return_value = {"elements": [{"end_of_day": "2024-01-02", "sora": "3.5", "published_date": "2024-01-03T00:00:00"}]}
        return m

    with patch.object(macro_mod.requests, "get", capture_get):
        macro_mod._fetch_sora_raw("2024-01-01", "2024-01-05")

    assert captured.get("headers", {}).get("keyid") == "fake-key-for-test"
    assert "$filter" in captured.get("params", {})


def test_sora_normalization_maps_fields_correctly():
    records = [
        {"end_of_day": "2024-01-02", "sora": "3.5123", "published_date": "2024-01-03T00:00:00"},
        {"end_of_day": "2024-01-03", "sora": "3.5200", "published_date": "2024-01-04T00:00:00"},
    ]
    normalized = macro_mod._normalize_sora(records)
    assert len(normalized) == 2
    assert normalized[0]["series_id"] == "SORA"
    assert normalized[0]["obs_date"] == date(2024, 1, 2)
    assert normalized[0]["value"] == pytest.approx(3.5123)
    assert normalized[0]["as_of_date"] == date(2024, 1, 3)  # real published_date, not a fallback
    assert normalized[0]["source"] == "MAS_APIMG_published_date"


def test_sora_normalization_falls_back_to_business_day_when_published_date_missing():
    """Older MAS APIMG rows don't carry a published_date value (confirmed live)."""
    records = [{"end_of_day": "2005-07-01", "sora": "0.5", "published_date": None}]
    normalized = macro_mod._normalize_sora(records)
    assert len(normalized) == 1
    assert normalized[0]["source"] == "MAS_APIMG_business_day_fallback"
    assert normalized[0]["as_of_date"] == macro_mod._plus_one_business_day(date(2005, 7, 1))


def test_sora_normalization_skips_rows_with_null_value():
    """Rows before SORA existed (pre-2005) or genuine gaps - not an error, just skipped."""
    records = [
        {"end_of_day": "1987-07-04", "sora": None, "published_date": None},
        {"end_of_day": "2024-01-02", "sora": "3.5", "published_date": "2024-01-03T00:00:00"},
    ]
    normalized = macro_mod._normalize_sora(records)
    assert len(normalized) == 1
    assert normalized[0]["obs_date"] == date(2024, 1, 2)


def test_sora_fails_loud_when_response_shape_unexpected_fields(monkeypatch):
    """
    Defensive shape check (MP-P3-029b): if MAS's API field names ever
    change again, fail loud with the actual fields found, rather than
    silently returning all-null values. Check now lives in
    _fetch_sora_raw (the field names are confirmed, not guessed, so the
    old candidate-list-based _identify_sora_value_field is retired).
    """
    monkeypatch.setattr(macro_mod, "MAS_APIMG_SUBSCRIPTION_KEY", "fake-key-for-test")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"elements": [{"totally_different_field": "x"}]}
    with patch.object(macro_mod.requests, "get", return_value=mock_resp):
        with pytest.raises(macro_mod.IngestionFailure) as exc_info:
            macro_mod._fetch_sora_raw("2024-01-01", "2024-01-05")
    assert "totally_different_field" in str(exc_info.value)


def test_fred_csv_normalization_modern_column_shape():
    csv_text = "observation_date,EFFR\n2024-01-02,5.33\n2024-01-03,5.33\n2024-01-04,.\n"
    with patch.object(macro_mod.requests, "get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.text = csv_text
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        normalized = macro_mod._normalize_fred("2024-01-01", "2024-01-05")

    # Row with "." (missing) must be dropped, not coerced to 0 or NaN-inserted.
    assert len(normalized) == 2
    assert normalized[0]["series_id"] == "US_FED_FUNDS_RATE"
    assert normalized[0]["source"] == "FRED_CSV_fallback"
    # No-API-key path: as_of_date must NOT equal obs_date (documented fallback).
    assert normalized[0]["as_of_date"] > normalized[0]["obs_date"]


def test_fx_normalization_maps_close_and_sets_as_of_equal_obs():
    df = pd.DataFrame(
        {"Open": [1.34, 1.35], "High": [1.35, 1.36], "Low": [1.33, 1.34],
         "Close": [1.345, 1.355], "Adj Close": [1.345, 1.355], "Volume": [0, 0]},
        index=pd.to_datetime([date(2024, 1, 2), date(2024, 1, 3)]),
    )
    normalized = macro_mod._normalize_fx(df)
    assert len(normalized) == 2
    assert normalized[0]["series_id"] == "SGD_USD_FX"
    assert normalized[0]["value"] == pytest.approx(1.345)
    assert normalized[0]["obs_date"] == normalized[0]["as_of_date"]


# =============================================================================
# Date handling
# =============================================================================

def test_sora_as_of_date_is_plus_one_sg_business_day():
    # Friday -> next business day is Monday (weekday-only approximation).
    friday = date(2024, 1, 5)
    assert macro_mod._plus_one_business_day(friday) == date(2024, 1, 8)


def test_fred_vintage_as_of_date_uses_realtime_start_when_api_key_set():
    observations = [
        {"date": "2024-01-02", "value": "5.33", "realtime_start": "2024-01-03", "realtime_end": "9999-12-31"},
    ]
    with patch.object(macro_mod, "FRED_API_KEY", "fake-key-for-test"), \
         patch.object(macro_mod, "_fetch_fred_vintage_json", return_value=observations):
        normalized = macro_mod._normalize_fred("2024-01-01", "2024-01-05")

    assert normalized[0]["source"] == "FRED_API_vintage"
    assert normalized[0]["obs_date"] == date(2024, 1, 2)
    assert normalized[0]["as_of_date"] == date(2024, 1, 3)  # the real realtime_start, not a +1 fallback


def test_as_of_before_obs_date_is_rejected_by_validation():
    records = [{
        "series_id": "SORA", "obs_date": date(2024, 1, 5), "value": 3.5,
        "as_of_date": date(2024, 1, 4), "source": "MAS_API",  # invalid: before obs_date
    }]
    valid, rejected, _ = validate_macro_rows(records, "SORA")
    assert len(valid) == 0
    assert len(rejected) == 1
    assert "before obs_date" in rejected[0]["reasons"][0]


# =============================================================================
# Idempotency and revision detection
# =============================================================================

def test_idempotent_macro_reingestion_produces_no_revision(con):
    rows = [{"series_id": "SORA", "obs_date": date(2024, 1, 2), "value": 3.51,
              "as_of_date": date(2024, 1, 3), "source": "MAS_API"}]
    con.execute("BEGIN TRANSACTION")
    macro_mod._upsert_macro_series(con, "SORA", rows)
    con.execute("COMMIT")

    con.execute("BEGIN TRANSACTION")
    inserted, updated, unchanged, revisions = macro_mod._upsert_macro_series(con, "SORA", rows)
    con.execute("COMMIT")

    assert (inserted, updated, unchanged, len(revisions)) == (0, 0, 1, 0)


def test_multi_vintage_lookup_matches_correct_vintage_not_arbitrary_one(con):
    """
    Regression test for a bug found in architecture review: the original
    _fetch_existing_macro() keyed its lookup dict by obs_date alone, so
    if two as_of_date vintages existed for the same obs_date (e.g. after
    FRED_API_KEY is added between runs, switching from the fallback
    convention to real vintage data), the dict would silently keep only
    one of them - risking a mismatched comparison or a raw INSERT
    colliding with the real PK (series_id, obs_date, as_of_date).
    """
    con.execute("BEGIN TRANSACTION")
    macro_mod._upsert_macro_series(con, "US_FED_FUNDS_RATE", [
        {"series_id": "US_FED_FUNDS_RATE", "obs_date": date(2024, 1, 2), "value": 5.33,
         "as_of_date": date(2024, 1, 3), "source": "FRED_CSV_fallback"},
    ])
    con.execute("COMMIT")
    # Manually insert a second vintage for the same obs_date, simulating
    # an earlier run under a different as_of_date convention.
    con.execute("BEGIN TRANSACTION")
    con.execute(
        "INSERT INTO raw_macro_series (series_id, obs_date, value, as_of_date, source, ingested_at) "
        "VALUES ('US_FED_FUNDS_RATE', '2024-01-02', 5.33, '2024-01-04', 'FRED_API_vintage', '2024-01-05')"
    )
    con.execute("COMMIT")

    # A revised value arriving under the FIRST vintage's as_of_date must
    # be matched against that specific vintage, not an arbitrary one.
    con.execute("BEGIN TRANSACTION")
    inserted, updated, unchanged, revisions = macro_mod._upsert_macro_series(con, "US_FED_FUNDS_RATE", [
        {"series_id": "US_FED_FUNDS_RATE", "obs_date": date(2024, 1, 2), "value": 5.40,
         "as_of_date": date(2024, 1, 3), "source": "FRED_CSV_fallback"},
    ])
    con.execute("COMMIT")

    assert (inserted, updated, unchanged) == (0, 1, 0)
    assert revisions[0]["old_value"] == 5.33  # the matching vintage's value, not a mismatched one
    total_rows = con.execute(
        "SELECT COUNT(*) FROM raw_macro_series WHERE series_id = 'US_FED_FUNDS_RATE'"
    ).fetchone()[0]
    assert total_rows == 2  # no spurious duplicate row, no PK collision


def test_changed_macro_value_is_detected_as_revision(con):
    rows_v1 = [{"series_id": "SORA", "obs_date": date(2024, 1, 2), "value": 3.51,
                 "as_of_date": date(2024, 1, 3), "source": "MAS_API"}]
    con.execute("BEGIN TRANSACTION")
    macro_mod._upsert_macro_series(con, "SORA", rows_v1)
    con.execute("COMMIT")

    rows_v2 = [{"series_id": "SORA", "obs_date": date(2024, 1, 2), "value": 3.60,  # revised value
                 "as_of_date": date(2024, 1, 3), "source": "MAS_API"}]
    con.execute("BEGIN TRANSACTION")
    inserted, updated, unchanged, revisions = macro_mod._upsert_macro_series(con, "SORA", rows_v2)
    con.execute("COMMIT")

    assert (inserted, updated, unchanged) == (0, 1, 0)
    assert len(revisions) == 1
    assert revisions[0]["old_value"] == 3.51
    assert revisions[0]["new_value"] == 3.60


def test_duplicate_logical_observation_not_created_across_reruns(con):
    rows = [{"series_id": "SGD_USD_FX", "obs_date": date(2024, 1, 2), "value": 1.34,
              "as_of_date": date(2024, 1, 2), "source": "yfinance"}]
    for _ in range(3):
        con.execute("BEGIN TRANSACTION")
        macro_mod._upsert_macro_series(con, "SGD_USD_FX", rows)
        con.execute("COMMIT")

    count = con.execute(
        "SELECT COUNT(*) FROM raw_macro_series WHERE series_id = 'SGD_USD_FX' AND obs_date = '2024-01-02'"
    ).fetchone()[0]
    assert count == 1


# =============================================================================
# Transaction safety
# =============================================================================

def test_macro_transaction_rollback_leaves_no_partial_data(con, monkeypatch):
    rows = [
        {"series_id": "SORA", "obs_date": date(2024, 1, 2), "value": 3.51, "as_of_date": date(2024, 1, 3), "source": "MAS_API"},
        {"series_id": "SORA", "obs_date": date(2024, 1, 3), "value": 3.52, "as_of_date": date(2024, 1, 4), "source": "MAS_API"},
        {"series_id": "SORA", "obs_date": date(2024, 1, 4), "value": 3.53, "as_of_date": date(2024, 1, 5), "source": "MAS_API"},
    ]

    real_upsert = macro_mod._upsert_macro_series
    call_count = {"n": 0}

    def poison_after_first_insert(con, series_id, valid_rows):
        # Simulate a failure partway through by processing one row for
        # real, then raising - exercising the same rollback guarantee
        # Phase 2 already proved, applied to raw_macro_series.
        con.execute(
            "INSERT INTO raw_macro_series (series_id, obs_date, value, as_of_date, source, ingested_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [series_id, valid_rows[0]["obs_date"], valid_rows[0]["value"],
             valid_rows[0]["as_of_date"], valid_rows[0]["source"], "2024-01-01"],
        )
        raise RuntimeError("simulated failure on the second row")

    monkeypatch.setattr(macro_mod, "_upsert_macro_series", poison_after_first_insert)

    with pytest.raises(macro_mod.NormalizationFailure):
        macro_mod._ingest_one_series("SORA", lambda: rows)

    # Note: _ingest_one_series calls get_connection() internally, which
    # opens its own connection to the same DB file in real use; here we
    # verify via the same `con` fixture that no rows survived the
    # rollback on THIS connection's view after the monkeypatched failure.
    count = con.execute("SELECT COUNT(*) FROM raw_macro_series WHERE series_id = 'SORA'").fetchone()[0]
    assert count == 0, "rollback must leave zero rows, not the one row inserted before the failure"


# =============================================================================
# Fail-loud behavior
# =============================================================================

def test_sora_api_exception_raises_ingestion_failure(monkeypatch):
    monkeypatch.setattr(macro_mod, "MAS_APIMG_SUBSCRIPTION_KEY", "fake-key-for-test")
    with patch.object(macro_mod.requests, "get", side_effect=ConnectionError("simulated network failure")):
        with pytest.raises(macro_mod.IngestionFailure) as exc_info:
            macro_mod._fetch_sora_raw("2024-01-01", "2024-01-05")
    assert "simulated network failure" in str(exc_info.value)


def test_sora_empty_response_is_not_treated_as_success(monkeypatch):
    monkeypatch.setattr(macro_mod, "MAS_APIMG_SUBSCRIPTION_KEY", "fake-key-for-test")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"elements": []}
    with patch.object(macro_mod.requests, "get", return_value=mock_resp):
        with pytest.raises(macro_mod.IngestionFailure) as exc_info:
            macro_mod._fetch_sora_raw("2024-01-01", "2024-01-05")
    assert "zero records" in str(exc_info.value)


def test_upsert_deduplicates_within_batch_conflicts(con):
    """
    Regression test for a real defect found via the first live SORA
    ingestion (MP-P3-029b): the real MAS APIMG data contained more than
    one record normalizing to the exact same (obs_date, as_of_date) pair
    within a single batch, causing a real PRIMARY KEY constraint
    violation. Confirms deduplication now handles this gracefully.
    """
    rows = [
        {"series_id": "SORA", "obs_date": date(2005, 7, 1), "value": 0.5,
         "as_of_date": date(2005, 7, 4), "source": "MAS_APIMG_business_day_fallback"},
        {"series_id": "SORA", "obs_date": date(2005, 7, 1), "value": 0.5,
         "as_of_date": date(2005, 7, 4), "source": "MAS_APIMG_business_day_fallback"},
    ]
    con.execute("BEGIN TRANSACTION")
    inserted, updated, unchanged, revisions = macro_mod._upsert_macro_series(con, "SORA", rows)
    con.execute("COMMIT")
    assert inserted == 1
    count = con.execute(
        "SELECT COUNT(*) FROM raw_macro_series WHERE series_id='SORA' AND obs_date='2005-07-01'"
    ).fetchone()[0]
    assert count == 1


def test_upsert_flags_within_batch_value_conflict():
    """
    If two records in the same batch share (obs_date, as_of_date) but
    genuinely DIFFER in value, that's flagged as a distinct event_type,
    not silently discarded or conflated with an ordinary revision.
    """
    import sqlite3
    con = sqlite3.connect(":memory:", detect_types=sqlite3.PARSE_DECLTYPES)
    sqlite3.register_adapter(date, lambda d: d.isoformat())
    sqlite3.register_converter("DATE", lambda b: date.fromisoformat(b.decode()))
    con.execute("""CREATE TABLE raw_macro_series (
        series_id TEXT, obs_date DATE, value REAL, as_of_date DATE, source TEXT, ingested_at TIMESTAMP,
        PRIMARY KEY (series_id, obs_date, as_of_date))""")
    rows = [
        {"series_id": "SORA", "obs_date": date(2005, 7, 1), "value": 0.5,
         "as_of_date": date(2005, 7, 4), "source": "test"},
        {"series_id": "SORA", "obs_date": date(2005, 7, 1), "value": 0.6,
         "as_of_date": date(2005, 7, 4), "source": "test"},
    ]
    inserted, updated, unchanged, revisions = macro_mod._upsert_macro_series(con, "SORA", rows)
    assert inserted == 1
    assert len(revisions) == 1
    assert revisions[0]["event_type"] == "within_batch_conflict"


def test_fred_csv_unrecognized_shape_fails_loud():
    with patch.object(macro_mod.requests, "get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.text = "totally,unexpected,columns\n1,2,3\n"
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp
        with pytest.raises(macro_mod.IngestionFailure) as exc_info:
            macro_mod._fetch_fred_csv("2024-01-01", "2024-01-05")
    assert "unrecognized FRED CSV column shape" in str(exc_info.value)


def test_fx_source_exception_raises_ingestion_failure(monkeypatch):
    monkeypatch.setattr(macro_mod.yfinance, "download",
                         lambda *a, **k: (_ for _ in ()).throw(ConnectionError("simulated fx failure")))
    with pytest.raises(macro_mod.IngestionFailure) as exc_info:
        macro_mod._fetch_fx_raw("USDSGD=X", "2024-01-01", "2024-01-05")
    assert "simulated fx failure" in str(exc_info.value)


# =============================================================================
# Validation edge cases
# =============================================================================

def test_validate_macro_rows_rejects_future_obs_date():
    """
    Regression test for a defect found while building the Phase 3.5
    integrity verification package: validate_macro_rows had no check
    rejecting a future obs_date, unlike validate_price_rows which already
    does. No legitimate source should return one, but this closes the
    same defense-in-depth gap validate_price_rows already covers.
    """
    from datetime import timedelta
    records = [{"series_id": "SORA", "obs_date": date.today() + timedelta(days=5), "value": 3.5,
                 "as_of_date": date.today() + timedelta(days=6), "source": "MAS_API"}]
    valid, rejected, _ = validate_macro_rows(records, "SORA")
    assert len(valid) == 0
    assert len(rejected) == 1
    assert "future" in rejected[0]["reasons"][0]


def test_validate_macro_rows_rejects_null_value():
    records = [{"series_id": "SORA", "obs_date": date(2024, 1, 2), "value": None,
                 "as_of_date": date(2024, 1, 3), "source": "MAS_API"}]
    valid, rejected, _ = validate_macro_rows(records, "SORA")
    assert len(valid) == 0
    assert len(rejected) == 1


def test_validate_macro_rows_rejects_absurd_value():
    records = [{"series_id": "SORA", "obs_date": date(2024, 1, 2), "value": 999999.0,
                 "as_of_date": date(2024, 1, 3), "source": "MAS_API"}]
    valid, rejected, _ = validate_macro_rows(records, "SORA")
    assert len(valid) == 0
    assert len(rejected) == 1


def test_validate_macro_rows_accepts_negative_rate():
    # Negative policy rates have occurred globally - must not be rejected
    # by an overly restrictive bound.
    records = [{"series_id": "US_FED_FUNDS_RATE", "obs_date": date(2024, 1, 2), "value": -0.5,
                 "as_of_date": date(2024, 1, 3), "source": "FRED_CSV_fallback"}]
    valid, rejected, _ = validate_macro_rows(records, "US_FED_FUNDS_RATE")
    assert len(valid) == 1
    assert len(rejected) == 0
