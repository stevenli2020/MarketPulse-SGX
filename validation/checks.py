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


def validate_macro_rows(records: list, series_id: str):
    """
    Row-level validation for one macro series' normalized records
    (Phase 3). Mirrors validate_price_rows's shape and split between
    hard rejections and soft warnings.

    Returns (valid_rows, rejected_rows, warnings) - same shape as
    validate_price_rows, so callers and the data-quality report can
    treat both uniformly.
    """
    valid_rows = []
    rejected_rows = []
    warnings = []

    # Deliberately broad, not tight - the instruction is explicit not to
    # invent overly restrictive business rules that could reject
    # legitimate historical data. These bounds exist only to catch
    # obvious parsing/unit errors (e.g. a rate of 99999), not to express
    # an opinion about what a "normal" rate or FX level is.
    bounds = {
        "SORA": (-10.0, 100.0),              # a rate, in percent
        "US_FED_FUNDS_RATE": (-10.0, 100.0),  # a rate, in percent
        "SGD_USD_FX": (0.01, 100.0),          # a price ratio
    }
    lo, hi = bounds.get(series_id, (-1e9, 1e9))

    for rec in records:
        reasons = []

        obs_date = rec.get("obs_date")
        as_of_date = rec.get("as_of_date")
        value = rec.get("value")

        if value is None or pd.isna(value):
            reasons.append("value is null")
        if obs_date is None:
            reasons.append("obs_date is missing")
        if as_of_date is None:
            reasons.append("as_of_date is missing")

        if obs_date is not None and as_of_date is not None:
            if as_of_date < obs_date:
                reasons.append(
                    f"as_of_date ({as_of_date}) is before obs_date ({obs_date}) - "
                    "a value cannot be knowable before the period it describes"
                )

        if value is not None and not pd.isna(value):
            if value < lo or value > hi:
                reasons.append(f"value {value} outside sanity bounds [{lo}, {hi}] for {series_id}")

        if reasons:
            rejected_rows.append({"obs_date": obs_date, "reasons": reasons})
            continue

        if as_of_date is not None and obs_date is not None and as_of_date == obs_date and series_id != "SGD_USD_FX":
            # For rate series (SORA, Fed funds), as_of_date == obs_date
            # would mean "knowable on the same day it describes" - not
            # impossible, but unusual enough for a macro release to be
            # worth a warning rather than silent acceptance.
            warnings.append({
                "warning_type": "as_of_equals_obs_date",
                "obs_date": obs_date,
                "detail": f"{series_id} {obs_date}: as_of_date equals obs_date - unusual for a rate release, worth reviewing",
            })

        valid_rows.append(rec)

    return valid_rows, rejected_rows, warnings


def check_cross_instrument_date_consistency(con) -> dict:
    """
    Compares trade dates present in prices_daily (D05.SI) against
    index_daily (^STI), RESTRICTED TO THE OVERLAPPING DATE RANGE of the
    two instruments. Any date present in one but not the other, within
    that overlap, is returned as an anomaly candidate for manual review -
    NOT an automatic "missing data" classification (see module
    docstring).

    OVERLAP RESTRICTION (added after the first real-data run, see
    PROJECT_STATUS.md): ^STI's history starts in 1990, D05.SI's starts
    in 2000 (it wasn't listed yet). Comparing full history therefore
    flagged every pre-2000 ^STI date as a false "D05.SI missing"
    anomaly, and also surfaced likely SG public holidays as anomalies -
    this check was never intended as a holiday-calendar detector (see
    module docstring) and no such calendar is introduced here. The fix
    is to only compare dates within
    [max(first date of either instrument), min(last date of either
    instrument)] - the period where both instruments could plausibly
    have traded. Dates outside this window are not evaluated at all,
    not silently reclassified as "not anomalies" - they were never a
    fair comparison to begin with.

    HARDENING (Phase 2 patch): if either table has zero rows, the
    comparison is skipped entirely rather than run - comparing a
    populated table against an empty one would flag every single date
    in the populated table as an "anomaly", which is noise, not a
    finding. This is a defensive check at the function level regardless
    of what the caller already knows about this run's success/failure.

    Returns a dict: {"skipped": bool, "reason": str or None,
    "overlap_start": date or None, "overlap_end": date or None,
    "anomalies": list}
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
            "overlap_start": None,
            "overlap_end": None,
            "anomalies": [],
        }

    security_range = con.execute("SELECT MIN(trade_date), MAX(trade_date) FROM prices_daily").fetchone()
    index_range = con.execute("SELECT MIN(trade_date), MAX(trade_date) FROM index_daily").fetchone()
    overlap_start = max(security_range[0], index_range[0])
    overlap_end = min(security_range[1], index_range[1])

    if overlap_start > overlap_end:
        return {
            "skipped": True,
            "reason": (
                f"D05.SI range ({security_range[0]} to {security_range[1]}) and ^STI range "
                f"({index_range[0]} to {index_range[1]}) do not overlap - consistency check skipped."
            ),
            "overlap_start": None,
            "overlap_end": None,
            "anomalies": [],
        }

    security_only = con.execute(
        """
        SELECT p.trade_date FROM prices_daily p
        LEFT JOIN index_daily i ON p.trade_date = i.trade_date
        WHERE i.trade_date IS NULL AND p.trade_date BETWEEN ? AND ?
        ORDER BY p.trade_date
        """,
        [overlap_start, overlap_end],
    ).fetchall()

    index_only = con.execute(
        """
        SELECT i.trade_date FROM index_daily i
        LEFT JOIN prices_daily p ON i.trade_date = p.trade_date
        WHERE p.trade_date IS NULL AND i.trade_date BETWEEN ? AND ?
        ORDER BY i.trade_date
        """,
        [overlap_start, overlap_end],
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
    return {"skipped": False, "reason": None, "overlap_start": overlap_start, "overlap_end": overlap_end, "anomalies": anomalies}


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

    # Macro coverage is computed directly from raw_macro_series rather
    # than data_availability_log (Phase 3): that table's entity_id column
    # is INTEGER, which doesn't fit a macro series_id like "SORA" without
    # a schema change - avoided per instruction to change schema only for
    # a genuine defect. This achieves the same reporting goal without one.
    macro_coverage = con.execute(
        "SELECT series_id, MIN(obs_date), MAX(obs_date), COUNT(*) "
        "FROM raw_macro_series GROUP BY series_id ORDER BY series_id"
    ).fetchall()
    lines.append("\nMacro series coverage:")
    if macro_coverage:
        for series_id, start, end, count in macro_coverage:
            lines.append(f"  {series_id}: {start} to {end}, {count} rows")
    else:
        lines.append("  none")

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
