"""
DBS fundamentals data collection (EPS, dividend yield, ROE, book value).

NOT YET IMPLEMENTED, and deliberately deferred - see PROJECT_SPEC.md
Section 11, Build Order step 8. This is the weakest data source in the
project (no clean free point-in-time API for historical SG fundamentals),
and it should only be built once the core price/volume/macro pipeline is
proven.

This stub exists only to fix the target table (raw_fundamentals) and the
expected input shape now, so the point-in-time schema is decided upfront:
every fundamentals row must carry both period_end_date (the period a
figure describes) and report_release_date (the date it was actually
published), per PROJECT_SPEC.md Section 8.

Expected future input: a manually curated CSV with columns
[period_end_date, report_release_date, metric_name, value].
"""


def load_fundamentals_from_csv(csv_path: str) -> None:
    """Load a manually curated fundamentals CSV. Not yet implemented."""
    raise NotImplementedError("Fundamentals ingestion is not implemented yet.")
