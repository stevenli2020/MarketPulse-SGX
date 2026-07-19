"""
Interest-rate / macroeconomic data collection (SORA, US Fed funds rate,
SGD/USD FX).

NOT YET IMPLEMENTED. This stub exists to fix the intended interface and
target table (raw_macro_series) before writing any actual collection
logic - see PROJECT_SPEC.md Section 11, Build Order step 2.

When implemented, this module will populate both obs_date (the period a
value describes) and as_of_date (the date it was actually knowable) for
every row - see PROJECT_SPEC.md Section 8 on why this distinction matters.
"""


def fetch_macro_series(series_id: str) -> None:
    """Fetch and store one macro series. Not yet implemented."""
    raise NotImplementedError("Macro data ingestion is not implemented yet.")
