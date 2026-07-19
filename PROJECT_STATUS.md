# MarketPulse SGX — Project Status

**Last updated:** 2026-07-19
**Phase:** 2 — Real price/index ingestion. **Hardening patch applied following a full code review. Live execution against yfinance/DuckDB still blocked by this sandbox's network allowlist — see "Execution environment limitation" below. Not yet run against real data.**

---

## Decisions log

| Date | Decision | Status |
|---|---|---|
| 2026-07-18 | Banking-sector peer features (OCBC/UOB) | **DEFERRED** — recorded as possible V1.1 extension, not in V1 |
| 2026-07-18 | News/sentiment data | **EXCLUDED from V1** — recorded as candidate V2 research experiment; no news-related tables/code/architecture in V1 |
| 2026-07-18 | Phase 2 scope | Approved and implemented: D05.SI + ^STI OHLCV ingestion, DuckDB storage, raw preservation, normalized storage, duplicate handling, validation, cross-instrument date consistency check, basic data-quality reporting. |
| 2026-07-18 | Phase 2 revisions (6 changes) | Approved and implemented: explicit `auto_adjust=False`; renamed `raw_prices_daily`→`prices_daily`, `raw_index_daily`→`index_daily`; split fetch audit into `price_fetches` + `raw_price_rows`; reframed gap check as "cross-instrument date consistency / anomaly detection"; added explicit `availability_date` point-in-time convention; fail-loud on empty/invalid source response. |
| 2026-07-19 | Code review | Full review against 10 points; found 2 CRITICAL (no transaction/rollback → possible partial normalized data), 4 IMPORTANT (yfinance exception handling gap, unverified MultiIndex level assumption, revision-logging not implemented, cross-instrument check could run on one-sided data), 1 MINOR (listing-date check missing). |
| 2026-07-19 | Hardening patch | Approved and implemented: all 2 CRITICAL and 4 IMPORTANT findings fixed, plus the 1 MINOR finding restored. Details below. |

---

## Files changed (Phase 2 hardening patch, 2026-07-19)

- `ingestion/prices.py` — rewritten: (1) the normalized-table update portion of `_ingest_one` (upsert → revision logging → coverage log update) is now wrapped in an explicit `BEGIN TRANSACTION` / `COMMIT` / `ROLLBACK`, so a failure partway through cannot leave partial rows in `prices_daily`/`index_daily`; a new `NormalizationFailure` exception (subclass of `IngestionFailure`) is raised on rollback; (2) `_upsert_normalized` now pre-fetches existing rows and classifies each incoming row as insert / unchanged-skip / revised-update, instead of an unconditional `ON CONFLICT DO UPDATE`; revised rows are logged as `price_revision_detected` warnings with the old/new values; (3) the `yfinance.download()` call itself is now wrapped in try/except so any exception it raises (network error, rate limit, etc.) is converted to `IngestionFailure` and recorded in `price_fetches`, rather than propagating unhandled; (4) MultiIndex column flattening now inspects both levels for the OHLCV field names instead of assuming level 0, and raises `IngestionFailure` with the actual returned columns if neither level matches; (5) `_ingest_one` now looks up `dim_securities.listed_date` and passes it into validation.
- `validation/checks.py` — `validate_price_rows()` gained a `listed_date` parameter; rows before it are rejected (the check is a no-op if `listed_date` is `None`). `check_cross_instrument_date_consistency()` now checks both tables' row counts first and returns `{"skipped": True, "reason": ...}` instead of comparing if either is empty.
- `scripts/run_ingestion.py` — tracks per-ticker ingestion success this run; the consistency check is only invoked if both D05.SI and ^STI succeeded, and prints a clear skip message otherwise; also prints the new inserted/revised/unchanged counts per ticker; `_ensure_dims` now passes `listed_date` through.
- `config.py` — `SECURITIES` entries gained a `listed_date` field, currently `None` for D05.SI (see "listing-date" note below — deliberately not populated with an unverified date).
- `tests/test_phase2_ingestion.py` — **new file**, added specifically because points 2 and 3 of this patch explicitly required deterministic tests. 9 tests covering: transaction rollback (both a targeted `_upsert_normalized` test and a full `_ingest_one` path test), idempotent re-fetch, revision detection, cross-instrument skip (both empty-side and both-populated cases), and yfinance exception wrapping. Not run in this sandbox (requires `duckdb`); written to run once `pip install -r requirements.txt` succeeds elsewhere.
- `PROJECT_STATUS.md` — this file.

**On the new test file:** this wasn't part of the original 15-file Phase 1 skeleton or the files list agreed for Phase 2 itself, but adding it was explicitly instructed by this patch ("add or run deterministic tests for... "), so it's not a silent scope addition — flagging it here for visibility regardless.

**On the listing-date value:** the mechanism is fully restored and wired up (config → `dim_securities.listed_date` → `validate_price_rows`), but `listed_date` for D05.SI is left as `None` rather than populated with a specific IPO/listing date I can't verify from memory with confidence. Populating it with an unverified "fact" seemed worse than leaving it explicitly unset for a project built around not asserting things it can't back up. The check activates automatically once a confirmed date is filled in.

## Tests run

| Test | Run in this sandbox? | Result |
|---|---|---|
| `python -m py_compile` on all 7 modified/created Python files | Yes | All compile cleanly |
| `validate_price_rows()` — hard rejections, soft warnings (6 synthetic rows, from initial Phase 2 build) | Yes | 3 valid / 3 rejected / 2 warnings, all correct |
| `validate_price_rows()` — listing-date rejection | Yes (via a stand-in for the missing `yfinance`/`duckdb` packages so the real module could be imported — see note below) | Pass: pre-listing row rejected, post-listing row accepted |
| `_identify_field_level()` — normal order, swapped order, no-match-fails-clearly-with-diagnostics | Yes (same stand-in approach) | All 3 cases correct |
| `_fetch_raw()` — yfinance exception → `IngestionFailure` | Yes (same stand-in approach) | Pass |
| `_fetch_raw()` — empty DataFrame → `IngestionFailure` (not "no new data") | Yes (same stand-in approach) | Pass |
| `_values_differ()` — identical values, changed value, float-noise tolerance | Yes (pure Python, no dependencies needed) | All 3 cases correct |
| `tests/test_phase2_ingestion.py` (all 9 tests, including the two transaction-rollback tests) | **No** — requires real `duckdb`, unavailable in this sandbox | Not executed; written and ready |
| Full `pytest` run | **No** — `pytest` itself unavailable in this sandbox | Not executed |

**Important honesty note on the "Yes" rows above using a stand-in:** since `ingestion/prices.py` does `import yfinance` and `db/connection.py` does `import duckdb` at module level, and neither package is installed in this sandbox, I could not import the real modules at all without first registering minimal placeholder modules in `sys.modules` for `yfinance` and `duckdb` (just enough to satisfy the import statement — a fake `download` attribute and a fake `connect`/`DuckDBPyConnection`). This let me exercise the actual, real project code (not reimplementations of it) for anything that doesn't need to actually call a real DuckDB or yfinance function. The two transaction-rollback tests and the revision/idempotency tests need a real DuckDB connection to run against, so those remain unexecuted here — this stand-in approach does not extend to them. This workaround is not part of the project and was not saved anywhere.

## Remaining CRITICAL or IMPORTANT issues after this patch

**None identified** in this review pass. All 2 CRITICAL and 4 IMPORTANT findings from the 2026-07-19 code review are addressed:
- Transaction/rollback (CRITICAL) — fixed, and specifically verified by two targeted rollback tests (though not yet run live — see above).
- Partial normalized data (CRITICAL) — same fix; the classify-then-write approach in `_upsert_normalized` plus the wrapping transaction jointly close this.
- yfinance exception handling (IMPORTANT) — fixed, verified in-sandbox.
- MultiIndex column robustness (IMPORTANT) — fixed, verified in-sandbox against both level orders and a no-match case.
- Revision handling (IMPORTANT) — implemented per the originally approved design (insert / idempotent-skip / revised-update-with-audit-log), verified in-sandbox for the comparison logic.
- Cross-instrument check on incomplete data (IMPORTANT) — fixed at two levels (caller-side success tracking in `run_ingestion.py`, and a defensive empty-table check inside `check_cross_instrument_date_consistency()` itself).
- Listing-date validation (MINOR) — mechanism restored; value intentionally left unset pending a verified date.

**What this patch does *not* newly verify:** anything requiring a real DuckDB connection or a real yfinance response remains unconfirmed until Steven's live run — this patch closes gaps found in code review, it does not substitute for that live run.

## Execution environment limitation (unchanged from before this patch)

This sandbox's outbound network is allowlisted and returns `x-deny-reason: host_not_allowed` for both `pypi.org` and Yahoo Finance endpoints — `duckdb`, `yfinance`, and `pytest` cannot be installed or reached here. This is an environment constraint, not a code defect. No fabricated numbers are reported anywhere in this document.

**Recommended next action:** run `pip install -r requirements.txt && pip install pytest && python -m pytest tests/ -v && python -m scripts.run_ingestion` in an environment with normal internet access. I can help debug the output if you paste it back.

---

## Database tables created or modified

- **Created:** `price_fetches`, `raw_price_rows`, `data_quality_warnings`
- **Renamed + modified:** `raw_prices_daily` → `prices_daily` (added `availability_date`), `raw_index_daily` → `index_daily` (added `availability_date`)
- **Modified:** `data_availability_log` (generalized to `entity_type`/`entity_id`)
- **Unchanged:** `dim_securities`, `dim_indices`, `raw_macro_series`, `raw_fundamentals`, `feature_store`, `labels`, `situation_matches`, `model_runs`, `predictions`, `backtest_results`

## What is currently implemented

- Full Phase 2 ingestion and validation code, per the approved plan and its 6 revisions.
- Offline-verified validation logic (synthetic data) and structural schema check (SQLite approximation).

## What is not implemented / not verified

- No live data has been ingested. Actual date ranges, row counts, warning counts, and cross-instrument anomalies for D05.SI and ^STI are **unknown until this is run in an environment with network access**.
- yfinance field-mapping (Close vs Adj Close vs a known DBS ex-dividend date) is **unconfirmed** — the mapping documented in the Phase 2 plan is the intended design, not yet verified against real data.
- `pytest`-based test execution is unavailable in this sandbox; `tests/test_leakage.py` remains skipped placeholders regardless, since it targets `features/feature_engineering.py` and `labeling/labels.py`, which are still stubs (Phase 4, not started).
- Macro, fundamentals, features, situation matching, ML, news/sentiment, Streamlit UI — all still out of scope, unchanged from before.

## Known open risks

Unchanged from prior phases (PROJECT_SPEC.md Section 17), plus one Phase-2-specific addition: the yfinance field-mapping assumption (Close = actual traded price, Adj Close = dividend/split-adjusted) is documented but not yet empirically confirmed against a real DBS corporate action — this should be the first thing checked once a live run is possible, before this data is trusted for anything downstream.

## Next recommended action

Run Phase 2 in a normal (non-sandboxed) Python environment to get real results, per the "Recommended next action" note above. **Do not start Phase 3** until that live run has been reviewed together — this matches the instruction for this phase.
