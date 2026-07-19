"""
Deterministic tests for the Phase 2 hardening patch:
  - transaction rollback / no-partial-data guarantee
  - idempotent re-fetch (no false revision)
  - revision detection and logging
  - cross-instrument check skip on one-sided data
  - yfinance exception wrapping

NOTE ON EXECUTION: these tests require `duckdb` and `yfinance` to be
installed (see requirements.txt) and were written but NOT executed in
the development sandbox used to build this patch, because that sandbox
has no network access and could not install either package. Run with:

    pip install -r requirements.txt
    pip install pytest
    python -m pytest tests/test_phase2_ingestion.py -v

The pure-logic pieces these tests exercise (field comparison, MultiIndex
field-level identification, listed_date rejection) were separately
verified in the sandbox using a stand-in module for yfinance - see the
implementation report for exactly what was and wasn't run.
"""

from datetime import date
from unittest.mock import patch

import pandas as pd
import pytest

duckdb = pytest.importorskip("duckdb")

from config import SCHEMA_PATH
from validation.checks import validate_price_rows, check_cross_instrument_date_consistency
import ingestion.prices as prices_mod


@pytest.fixture
def con():
    """Fresh in-memory DuckDB instance with the real schema applied."""
    c = duckdb.connect(":memory:")
    with open(SCHEMA_PATH) as f:
        c.execute(f.read())
    c.execute(
        "INSERT INTO dim_securities (security_id, ticker, name, exchange, listed_date) "
        "VALUES (1, 'D05.SI', 'DBS Group Holdings', 'SGX', NULL)"
    )
    c.execute("INSERT INTO dim_indices (index_id, ticker, name) VALUES (1, '^STI', 'Straits Times Index')")
    return c


def _sample_df(dates_and_closes):
    """Builds a raw_price_rows-shaped DataFrame for direct use with _upsert_normalized."""
    rows = []
    for d, close in dates_and_closes:
        rows.append({
            "trade_date": d, "open": close - 0.1, "high": close + 0.2,
            "low": close - 0.2, "close": close, "adj_close": close, "volume": 1_000_000,
        })
    return rows


# --- Transaction rollback / no-partial-data --------------------------------

def test_transaction_rollback_leaves_no_partial_data(con, monkeypatch):
    """
    Forces a failure partway through the per-row upsert loop and asserts
    that NO rows from this batch were left committed in prices_daily -
    the exact CRITICAL scenario flagged in the code review.
    """
    rows = _sample_df([(date(2026, 1, 5), 40.0), (date(2026, 1, 6), 40.5), (date(2026, 1, 7), 41.0)])

    call_count = {"n": 0}
    real_insert = prices_mod._insert_normalized_row

    def poison_after_first(con, table, id_col, entity_id, r):
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise RuntimeError("simulated failure on the second row")
        return real_insert(con, table, id_col, entity_id, r)

    monkeypatch.setattr(prices_mod, "_insert_normalized_row", poison_after_first)

    con.execute("BEGIN TRANSACTION")
    with pytest.raises(RuntimeError):
        prices_mod._upsert_normalized(con, "prices_daily", "security_id", 1, rows)
    con.execute("ROLLBACK")

    count = con.execute("SELECT COUNT(*) FROM prices_daily").fetchone()[0]
    assert count == 0, "rollback must leave zero rows, not the one row inserted before the failure"


def test_ingest_one_raises_normalization_failure_and_rolls_back(con, monkeypatch):
    """
    Full-path version: the fetch succeeds (mocked), but normalization is
    forced to fail. Confirms _ingest_one raises NormalizationFailure and
    prices_daily ends up with zero rows despite raw_price_rows having
    the full fetched set (the fetch itself genuinely succeeded).
    """
    fake_df = pd.DataFrame({
        "Open": [40.0, 40.5], "High": [40.2, 40.7], "Low": [39.8, 40.3],
        "Close": [40.1, 40.6], "Adj Close": [40.1, 40.6], "Volume": [1_000_000, 1_100_000],
    }, index=pd.to_datetime([date(2026, 1, 5), date(2026, 1, 6)]))

    monkeypatch.setattr(prices_mod, "get_connection", lambda: con)
    monkeypatch.setattr(prices_mod.yfinance, "download", lambda *a, **k: fake_df)
    monkeypatch.setattr(
        prices_mod, "_insert_normalized_row",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("forced failure"))
    )

    with pytest.raises(prices_mod.NormalizationFailure):
        prices_mod.fetch_security_prices("D05.SI", 1)

    assert con.execute("SELECT COUNT(*) FROM prices_daily").fetchone()[0] == 0
    # The fetch itself genuinely succeeded and must still be recorded as such.
    status = con.execute("SELECT status FROM price_fetches ORDER BY fetch_id DESC LIMIT 1").fetchone()[0]
    assert status == "success"


# --- Idempotent re-fetch vs. revision detection -----------------------------

def test_idempotent_refetch_produces_no_revision(con):
    rows = _sample_df([(date(2026, 1, 5), 40.0)])
    con.execute("BEGIN TRANSACTION")
    prices_mod._upsert_normalized(con, "prices_daily", "security_id", 1, rows)
    con.execute("COMMIT")

    con.execute("BEGIN TRANSACTION")
    inserted, updated, unchanged, revisions = prices_mod._upsert_normalized(
        con, "prices_daily", "security_id", 1, rows
    )
    con.execute("COMMIT")

    assert (inserted, updated, unchanged, len(revisions)) == (0, 0, 1, 0)


def test_revision_is_detected_and_reported(con):
    rows_v1 = _sample_df([(date(2026, 1, 5), 40.0)])
    con.execute("BEGIN TRANSACTION")
    prices_mod._upsert_normalized(con, "prices_daily", "security_id", 1, rows_v1)
    con.execute("COMMIT")

    rows_v2 = _sample_df([(date(2026, 1, 5), 41.50)])  # adj_close/close revised
    con.execute("BEGIN TRANSACTION")
    inserted, updated, unchanged, revisions = prices_mod._upsert_normalized(
        con, "prices_daily", "security_id", 1, rows_v2
    )
    con.execute("COMMIT")

    assert (inserted, updated, unchanged) == (0, 1, 0)
    assert len(revisions) == 1
    assert "close" in revisions[0]["changed_fields"]


# --- Cross-instrument check skip --------------------------------------------

def test_cross_instrument_check_skipped_when_one_side_empty(con):
    con.execute("BEGIN TRANSACTION")
    prices_mod._upsert_normalized(
        con, "index_daily", "index_id", 1, _sample_df([(date(2026, 1, 5), 3300.0)])
    )
    con.execute("COMMIT")
    # prices_daily left empty on purpose

    result = check_cross_instrument_date_consistency(con)
    assert result["skipped"] is True
    assert result["anomalies"] == []


def test_cross_instrument_check_runs_when_both_populated(con):
    con.execute("BEGIN TRANSACTION")
    prices_mod._upsert_normalized(con, "prices_daily", "security_id", 1,
                                   _sample_df([(date(2026, 1, 5), 40.0)]))
    prices_mod._upsert_normalized(con, "index_daily", "index_id", 1,
                                   _sample_df([(date(2026, 1, 5), 3300.0), (date(2026, 1, 6), 3310.0)]))
    con.execute("COMMIT")

    result = check_cross_instrument_date_consistency(con)
    assert result["skipped"] is False
    assert len(result["anomalies"]) == 1  # 2026-01-06 present in index only


# --- yfinance exception handling --------------------------------------------

def test_yfinance_exception_is_wrapped_as_ingestion_failure(con, monkeypatch):
    monkeypatch.setattr(prices_mod, "get_connection", lambda: con)

    def raise_network_error(*a, **k):
        raise ConnectionError("simulated network failure")

    monkeypatch.setattr(prices_mod.yfinance, "download", raise_network_error)

    with pytest.raises(prices_mod.IngestionFailure):
        prices_mod.fetch_security_prices("D05.SI", 1)

    row = con.execute(
        "SELECT status, error_message FROM price_fetches ORDER BY fetch_id DESC LIMIT 1"
    ).fetchone()
    assert row[0] == "failed"
    assert "simulated network failure" in row[1]
    assert con.execute("SELECT COUNT(*) FROM raw_price_rows").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM prices_daily").fetchone()[0] == 0


# --- Listing-date validation -------------------------------------------------

def test_listed_date_rejects_rows_before_listing():
    df = pd.DataFrame([
        {"trade_date": date(1995, 1, 1), "open": 10.0, "high": 10.2, "low": 9.8, "close": 10.1,
         "adj_close": 10.1, "volume": 500_000},
        {"trade_date": date(2020, 1, 1), "open": 10.0, "high": 10.2, "low": 9.8, "close": 10.1,
         "adj_close": 10.1, "volume": 500_000},
    ])
    valid, rejected, _ = validate_price_rows(df, "D05.SI", "security", listed_date=date(1999, 1, 5))
    assert len(valid) == 1
    assert len(rejected) == 1
    assert "listing date" in rejected[0]["reasons"][0]
