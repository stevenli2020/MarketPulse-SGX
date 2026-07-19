"""
Data quality checks for price data - Phase 2.

Two responsibilities:
  1. Row-level validation of raw_price_rows before they are allowed into
     the normalized prices_daily / index_daily tables.
  2. Cross-instrument date consistency checking between D05.SI and ^STI
     (explicitly NOT called "gap detection" - see note below).

IMPORTANT NAMING NOTE: the D05.SI-vs-^STI date comparison implemented
here is deliberately called "cross-instrument date consistency /
anomaly detection", not "gap detection". If one instrument has an
observation on a date the other lacks, that produces a WARNING for
manual review - it is never automatically classified as missing data.
A genuine data gap and a legitimate reason for one instrument (but not
the other) to lack a trading day (e.g. a stock-specific halt) look
identical to this simple check; it flags candidates, it does not
diagnose them.
"""

from datetime import date

import pandas as pd


def validate_price_rows(df: pd.DataFrame, ticker: str, entity_type: str, listed_date=None):
    """
    Row-level validation of raw price rows for one ticker/fetch.

    listed_date: optional date. If provided (currently only meaningful
    for entity_type='security', looked up from dim_securities.listed_date
    - see config.py for why it defaults to None rather than an unverified
    placeholder), any row with trade_date earlier than listed_date is
    rejected. If None, this check is simply skipped - it activates
    automatically once a confirmed listed_date is available.

    Returns (valid_rows, rejected_rows, warnings):
      - valid_rows: list of dicts, safe to upsert into the normalized table
      - rejected_rows: list of dicts describing rows excluded and why
      - warnings: list of dicts (not rejections) for data_quality_warnings
    """
    valid_rows = []
    rejected_rows = []
    warnings = []

    today = date.today()

    for _, row in df.iterrows():
        trade_date = row["trade_date"]
        if hasattr(trade_date, "date"):
            trade_date = trade_date.date()

        reasons = []

        # --- Hard rejection checks -----------------------------------
        for field in ("open", "high", "low", "close"):
            val = row.get(field)
            if val is None or pd.isna(val) or val < 0:
                reasons.append(f"{field} is null or negative")

        close = row.get("close")
        if close is None or pd.isna(close) or close <= 0:
            reasons.append("close is not > 0")

        o, h, l, c = row.get("open"), row.get("high"), row.get("low"), row.get("close")
        if not any(pd.isna(x) for x in (o, h, l, c)):
            if not (h >= o and h >= c and h >= l and l <= o and l <= c):
                reasons.append("OHLC sanity check failed (high/low do not bound open/close)")

        if trade_date > today:
            reasons.append("trade_date is in the future")

        if listed_date is not None and trade_date < listed_date:
            reasons.append(f"trade_date is before the configured listing date ({listed_date})")

        if reasons:
            rejected_rows.append({"trade_date": trade_date, "reasons": reasons})
            continue

        # --- Soft warning checks (row still proceeds) -----------------
        volume = row.get("volume")
        if volume is None or pd.isna(volume) or volume == 0:
            if entity_type == "security":
                warnings.append({
                    "warning_type": "zero_or_null_volume",
                    "trade_date": trade_date,
                    "detail": f"{ticker}: zero/null volume on a security - unusual, worth reviewing",
                })
            # Not warned for indices - zero/null volume is normal there.

        adj_close = row.get("adj_close")
        if adj_close is None or pd.isna(adj_close):
            warnings.append({
                "warning_type": "missing_adj_close",
                "trade_date": trade_date,
                "detail": f"{ticker}: adj_close missing on an otherwise valid trading day",
            })

        valid_rows.append({
            "trade_date": trade_date,
            "open": row.get("open"), "high": row.get("high"),
            "low": row.get("low"), "close": row.get("close"),
            "adj_close": row.get("adj_close"), "volume": row.get("volume"),
        })

    return valid_rows, rejected_rows, warnings


def check_cross_instrument_date_consistency(con) -> dict:
    """
    Compares trade dates present in prices_daily (D05.SI) against
    index_daily (^STI). Any date present in one but not the other is
    returned as an anomaly candidate for manual review - NOT an
    automatic "missing data" classification (see module docstring).

    HARDENING (Phase 2 patch): if either table has zero rows, the
    comparison is skipped entirely rather than run - comparing a
    populated table against an empty one would flag every single date
    in the populated table as an "anomaly", which is noise, not a
    finding. This is a defensive check at the function level regardless
    of what the caller already knows about this run's success/failure.

    Returns a dict: {"skipped": bool, "reason": str or None, "anomalies": list}
    """
    security_count = con.execute("SELECT COUNT(*) FROM prices_daily").fetchone()[0]
    index_count = con.execute("SELECT COUNT(*) FROM index_daily").fetchone()[0]

    if security_count == 0 or index_count == 0:
        return {
            "skipped": True,
            "reason": (
                f"prices_daily has {security_count} rows, index_daily has {index_count} rows - "
                "consistency check requires both to be non-empty, skipped to avoid a flood of "
                "false anomalies from one-sided data."
            ),
            "anomalies": [],
        }

    security_only = con.execute(
        """
        SELECT p.trade_date FROM prices_daily p
        LEFT JOIN index_daily i ON p.trade_date = i.trade_date
        WHERE i.trade_date IS NULL
        ORDER BY p.trade_date
        """
    ).fetchall()

    index_only = con.execute(
        """
        SELECT i.trade_date FROM index_daily i
        LEFT JOIN prices_daily p ON i.trade_date = p.trade_date
        WHERE p.trade_date IS NULL
        ORDER BY i.trade_date
        """
    ).fetchall()

    anomalies = []
    for (d,) in security_only:
        anomalies.append({
            "warning_type": "cross_instrument_date_anomaly",
            "trade_date": d,
            "detail": "D05.SI has an observation on this date; ^STI does not - review, not confirmed missing data",
        })
    for (d,) in index_only:
        anomalies.append({
            "warning_type": "cross_instrument_date_anomaly",
            "trade_date": d,
            "detail": "^STI has an observation on this date; D05.SI does not - review, not confirmed missing data",
        })
    return {"skipped": False, "reason": None, "anomalies": anomalies}


def generate_data_quality_report(con) -> str:
    """
    Produces a plain-text summary of current price data coverage and
    outstanding warnings. Printed by scripts/run_ingestion.py - this is
    the "basic data-quality reporting" deliverable for Phase 2, not a UI.
    """
    lines = ["=== MarketPulse SGX - Data Quality Report ===\n"]

    coverage = con.execute(
        "SELECT table_name, entity_id, coverage_start, coverage_end, row_count, last_updated "
        "FROM data_availability_log ORDER BY table_name, entity_id"
    ).fetchall()
    lines.append("Coverage:")
    for row in coverage:
        lines.append(f"  {row[0]} (entity_id={row[1]}): {row[2]} to {row[3]}, {row[4]} rows, last updated {row[5]}")

    warning_counts = con.execute(
        "SELECT warning_type, COUNT(*) FROM data_quality_warnings GROUP BY warning_type"
    ).fetchall()
    lines.append("\nOutstanding warnings:")
    if warning_counts:
        for wtype, count in warning_counts:
            lines.append(f"  {wtype}: {count}")
    else:
        lines.append("  none")

    fetch_failures = con.execute(
        "SELECT ticker, requested_at, error_message FROM price_fetches WHERE status = 'failed' ORDER BY requested_at DESC"
    ).fetchall()
    lines.append("\nFailed fetch attempts (most recent first):")
    if fetch_failures:
        for ticker, ts, err in fetch_failures:
            lines.append(f"  {ticker} @ {ts}: {err}")
    else:
        lines.append("  none")

    return "\n".join(lines)
