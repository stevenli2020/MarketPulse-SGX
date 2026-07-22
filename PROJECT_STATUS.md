# MarketPulse SGX — Project Status

**Last updated:** 2026-07-19
**Phase:** 3 — Macro ingestion (SORA, US Fed funds rate, SGD/USD FX). **Implemented, syntax-checked, and logic-verified in Cola's development sandbox via mocked/stand-in tests. Macha's architecture/code audit found 2 issues requiring remediation (multi-vintage lookup keying bug, missing exit-code propagation) — both confirmed real and fixed; see "Response to Macha's architecture/code audit" below. NOT yet run live in Sprite's WSL environment; `pytest tests/test_phase3_macro_ingestion.py -v` still needs to be run there before the live macro ingestion step.** Phase 2 remains closed and unmodified by this work. Awaiting Sprite's WSL test results before the live run.

---

## Phase 3 implementation record (2026-07-19)

### Files modified
- **`config.py`** — added `MACRO_HISTORY_START_DATE`, `FRED_API_KEY` (read from environment, never hardcoded), and `MACRO_SOURCE_CONFIG` (per-series source URLs/identifiers, one place per this project's existing configuration style).
- **`ingestion/macro.py`** — full implementation, replacing the stub. Source adapters for SORA (MAS API), US_FED_FUNDS_RATE (FRED, vintage-aware or CSV fallback), SGD_USD_FX (yfinance); common normalization to `{series_id, obs_date, value, as_of_date, source}`; transaction-wrapped upsert into `raw_macro_series` with idempotent/revision classification; fail-loud on any unusable source response. Imports `IngestionFailure`/`NormalizationFailure` from `ingestion.prices` rather than redefining them.
- **`validation/checks.py`** — added `validate_macro_rows()`; extended `generate_data_quality_report()` to include macro coverage (computed directly from `raw_macro_series`, not `data_availability_log` — see Data model impact below).
- **`scripts/run_ingestion.py`** — extended to run macro ingestion for each series in `config.MACRO_SERIES` after the existing price/index/consistency-check flow, with per-series failure reporting that can never silently read as success.
- **`requirements.txt`** — added `requests==2.32.3` (explicit; was already a transitive `yfinance` dependency, now used directly for MAS/FRED HTTP calls).

### Files added
- **`tests/test_phase3_macro_ingestion.py`** — 19 deterministic tests, mocking all external source calls, mirroring `tests/test_phase2_ingestion.py`'s structure.

### Files explicitly NOT touched
`db/schema.sql` — no schema change. `raw_macro_series` (`series_id`, `obs_date`, `value`, `as_of_date`, `source`, `ingested_at`, PK on all three of `series_id`/`obs_date`/`as_of_date`) was already sufficient; nothing here required altering it. `features/feature_engineering.py`, `labeling/labels.py` — confirmed untouched (unchanged file timestamps). `ingestion/prices.py`, Phase 2's tests — confirmed unmodified.

### Exact source used per series
| Series | Source | Endpoint |
|---|---|---|
| `SORA` | MAS official datastore API | `https://eservices.mas.gov.sg/api/action/datastore/search.json?resource_id=9a0bf149-308c-4bd2-832d-76c8e6cb47ed` — **resource_id found via a third-party technical walkthrough of this official API, not MAS's own docs directly, and NOT independently verified against the live endpoint (no network access in this sandbox). Needs confirmation on first real run.** The value field name is similarly unconfirmed; the code tries `["sora", "sora_rate", "overnight_sora"]` in order and fails loud with the actual returned field names if none match. |
| `US_FED_FUNDS_RATE` | FRED | If `FRED_API_KEY` set: `https://api.stlouisfed.org/fred/series/observations?series_id=EFFR` with the full-vintage trick (`realtime_start=1776-07-04`) for genuine release-date metadata. If unset: the public, no-key CSV endpoint `https://fred.stlouisfed.org/graph/fredgraph.csv?id=EFFR`, exactly as specified. |
| `SGD_USD_FX` | yfinance `1.5.1` | Ticker `USDSGD=X`, exactly as specified — not substituted with `SGDUSD=X`. |

### `obs_date` / `as_of_date` convention per series
- **SORA:** `as_of_date = obs_date + 1 Singapore business day`, via `pandas.tseries.offsets.BDay(1)` — weekday-only, does not account for SG public holidays (disclosed limitation; no holiday-calendar package added, per instruction). This matches MAS's own stated practice (confirmed via web search: *"SORA for a given business day will be published by 9.00am on the next business day"*).
- **US_FED_FUNDS_RATE:** if `FRED_API_KEY` is set, `as_of_date` is the real `realtime_start` from FRED's vintage data — the actual first-published date. If not set, falls back explicitly to `obs_date + 1 business day` (never silently collapsed to `obs_date`).
- **SGD_USD_FX:** `as_of_date = obs_date = trade_date`, matching the existing Phase 2 equity/index convention exactly.

### Look-ahead prevention at the storage layer
`validate_macro_rows()` hard-rejects any row where `as_of_date < obs_date` before it can reach `raw_macro_series` — a value cannot be knowable before the period it describes. This is enforced at validation time, not just documented. Downstream Phase 4 code (not designed here) can enforce `WHERE as_of_date <= cutoff_date` uniformly across `raw_macro_series`, `prices_daily`, and `index_daily`, since all three now carry the same point-in-time-filtering column pattern.

### Tests run and results
**Not executed via `pytest`** — `duckdb`, `yfinance`, and `pytest` remain uninstallable in this sandbox (confirmed: PyPI unreachable, same constraint as every prior phase). Cannot claim what wasn't run.

**What was actually executed**, using the real `ingestion.macro`/`validation.checks` modules (not reimplementations), with `yfinance`/`duckdb` stood in only enough to satisfy imports, and the real `requests` library with mocked HTTP responses:
- 14 direct-execution checks: SORA/FRED/FX normalization correctness, SORA's unknown-field fail-loud diagnostic, the T+1 business-day calculation, FRED's vintage-vs-fallback `as_of_date` distinction, `validate_macro_rows`'s rejection/acceptance behavior (including confirming negative rates are accepted, not rejected), and fail-loud behavior for a request exception, an empty response, and an unrecognized CSV shape. **All 14 passed.**
- 4 additional checks against `_upsert_macro_series` using an in-memory SQLite stand-in (this function's SQL is portable, like Phase 2's cross-instrument check): idempotent re-ingestion, genuine revision detection, no duplicate rows across repeated runs, and transaction rollback leaving zero partial rows. **All 4 passed** — after first hitting and correctly diagnosing a SQLite-stand-in artifact (SQLite has no native DATE type, so dates round-trip as strings unlike DuckDB's native type; fixed by registering a DATE converter in the test harness only, not in the actual code).

**Existing Phase 2 tests:** re-ran the full Phase 2 compile check across every file (10 files, includes `ingestion/prices.py`, `validation/checks.py`, `scripts/run_ingestion.py`) — all still compile cleanly. `tests/test_phase2_ingestion.py` itself was not re-executed here for the same duckdb-unavailability reason as before; no evidence of any Phase 2 regression from a read-through diff (the only shared file touched, `validation/checks.py`, had `validate_macro_rows` and a report extension added, with `validate_price_rows`/`check_cross_instrument_date_consistency` left byte-for-byte in place above/below the new function).

### Remaining uncertainty
1. **SORA `resource_id` and value-field name are unverified against the live MAS API.** This is the single biggest open risk in this implementation — flagged clearly, not glossed over. First real run will either confirm it or fail loud with the actual field names, which is the intended behavior either way.
2. **FRED CSV/API exact response shape** — implemented from well-documented, verified patterns (confirmed modern `observation_date`/`<series_id>` column shape via search), but not executed against the live endpoint.
3. **Whether `USDSGD=X` returns data cleanly via yfinance `1.5.1`** — same category of uncertainty Phase 2 had for `D05.SI`/`^STI` before its first real run; the code reuses the exact proven pattern.
4. **19 written tests in `tests/test_phase3_macro_ingestion.py` have not been run via real pytest** — ready to run once Sprite has `duckdb`/`pytest` installed (already true in the WSL `.venv` per Phase 2).

### Confirmation
**Phase 4 Feature Engineering was not started or designed.** `features/feature_engineering.py` and `labeling/labels.py` are confirmed untouched (unchanged file timestamps, unchanged content).

## Phase 3.5 — Live Verification Package (2026-07-19)

**Objective:** verification tooling only, no new functionality, no Phase 4 work. Package delivered; **not yet run live** — same sandbox constraint as every prior phase (no network, no `duckdb`/`yfinance` installable here). Awaiting Sprite's WSL execution and the completed report.

### Defect discovered and fixed while building the integrity checks
While designing the "invalid dates" integrity check, found that `validate_macro_rows()` had **no check rejecting a future `obs_date`** — an asymmetry with `validate_price_rows()`, which already has one. No legitimate source should ever return future data, but this closed a real defense-in-depth gap rather than leaving macro data as the one exception. Fixed with the minimal equivalent check; verified with 3 direct checks (future date rejected, valid historical row still accepted, existing `as_of_date < obs_date` check unaffected) and a new permanent regression test, `test_validate_macro_rows_rejects_future_obs_date` (`tests/test_phase3_macro_ingestion.py`, now 20 test functions). No other ingestion/validation logic was touched.

### Files added
- `verification/verify_live_ingestion.py` — runs real ingestion against all 3 configured macro sources, reports PASS/FAIL per source against connection/download/normalization/validation/storage checkpoints (read off `fetch_macro_series`'s own return value, not by instrumenting internals).
- `verification/verify_db_integrity.sql` — raw SQL integrity queries (row counts, duplicate PK, NULLs, invalid dates, revision integrity, value bounds), each documented with its expected "healthy" result. Explicitly documents that "orphan records" is not applicable to `raw_macro_series` (no FK relationships).
- `verification/verify_db_integrity.py` — executes the same checks and reports PASS/FAIL programmatically.
- `verification/verify_idempotency.py` — runs live ingestion twice back to back, compares row counts and revision counts; flags any Run 2 revisions as "review required" rather than auto-failing, since a genuine upstream revision in the gap between runs is possible and this script can't distinguish that from a logic error with certainty.
- `verification/verify_rollback.py` — forces a failure via monkeypatching against an isolated, temporary DuckDB file (never touches the real project database), confirms zero partial rows remain. **Verified working in Cola's sandbox** using the fake-module/SQLite-substitution technique established in earlier phases — the underlying `_ingest_one_series` → `NormalizationFailure` → rollback mechanism was confirmed correct directly, not just assumed.
- `verification/verify_logging_and_exit_code.py` — confirms failure messages are specific/readable and that `scripts/run_ingestion.py::main()` correctly propagates failure to its return value (and therefore the process exit code). **Actually executed end to end in Cola's sandbox** (fully mocked, no live sources or real DB needed) — both checks passed, and this directly re-confirmed the exit-code fix from the earlier architecture audit still works.
- `verification/run_all_verifications.py` — single entry point running all five stages in a safe order (isolated checks first, live-data checks after), printing a final summary.
- `PHASE3_5_VERIFICATION_REPORT_TEMPLATE.md` — structured report template for Sprite to complete after running the package in WSL.

### What was actually verified in Cola's sandbox vs. what needs a live WSL run
| Script | Sandbox status |
|---|---|
| `verify_rollback.py` | Underlying mechanism directly confirmed correct (SQLite stand-in for the isolated temp-DB logic) |
| `verify_logging_and_exit_code.py` | **Executed end to end for real** (fully mocked, no live dependencies needed) — passed |
| `verify_live_ingestion.py` | Not executable here — needs live network access to MAS/FRED/Yahoo |
| `verify_db_integrity.py` / `.sql` | Not executable here — needs a populated real DuckDB file |
| `verify_idempotency.py` | Not executable here — needs live network access, run twice |

### Architecture boundaries maintained
No feature engineering, labeling, model training, prediction logic, backtesting, or schema redesign was added. `features/feature_engineering.py` and `labeling/labels.py` remain untouched (unchanged timestamps). Only verification tooling and the one disclosed validation-layer defect fix were added.

---



Two issues were identified as requiring remediation before the live test run. Both were independently verified against the actual code (not accepted on claim alone) before fixing.

### Issue 1 (REVISION REQUIRED → FIXED): multi-vintage lookup keying bug in `ingestion/macro.py`

**Confirmed real.** `_fetch_existing_macro()` built its lookup dictionary keyed by `obs_date` alone: `{r[0]: {...} for r in rows}`. If more than one `as_of_date` vintage existed for the same `obs_date` (a real, if edge-case, scenario — e.g. `FRED_API_KEY` being added to the environment between runs, switching an observation from the fallback `as_of_date` convention to real FRED vintage data), the dict comprehension would silently keep only one of them, in unspecified order. `_upsert_macro_series` would then compare an incoming row against whichever vintage happened to survive, potentially misclassifying a genuine revision, or attempting a raw `INSERT` that collides with the real primary key `(series_id, obs_date, as_of_date)` — the code's own docstring incorrectly claimed this was protected by an `ON CONFLICT` clause that was never actually present.

**Fix:** `_fetch_existing_macro()` now keys its lookup by the full `(obs_date, as_of_date)` tuple, exactly matching the real PK. `_upsert_macro_series()` was simplified accordingly — a lookup miss now unambiguously means "no row exists for this exact vintage," so an `INSERT` can never collide with an existing PK. A new, distinct event type (`new_vintage_for_existing_obs_date`) was added to separately flag the case where a genuinely new vintage arrives for an `obs_date` that already has other vintages on record, rather than conflating it with an ordinary value revision.

**Verified:** re-ran the full existing idempotency/revision test suite against the fix (all still pass), plus a new targeted test reproducing the exact bug scenario (two vintages pre-existing for one `obs_date`, a revision arriving that must match the correct one) — confirmed the fix resolves it: the revision is matched against the right vintage, no duplicate row or PK collision results. This scenario is now a permanent regression test (`test_multi_vintage_lookup_matches_correct_vintage_not_arbitrary_one`) in `tests/test_phase3_macro_ingestion.py`, bringing the file to 19 test functions.

### Issue 2 (REVISION REQUIRED → FIXED): pipeline failure not propagated to exit code in `scripts/run_ingestion.py`

**Confirmed real, and pre-existing since Phase 2** (not a new Phase 3 regression, though Phase 3 continued the pattern rather than catching it): `security_run_ok`/`index_run_ok` were tracked but never used to set a process exit code, and macro ingestion wasn't tracked at all. A failure printed an error line but the script always exited 0, indistinguishable from success to any calling automation.

**Fix:** added `macro_run_ok` tracking; `main()` now returns `(results, overall_ok)`; the `if __name__ == "__main__":` block calls `sys.exit(0 if ok else 1)`. Kept the exit call out of `main()` itself so it remains a plain, importable/testable function.

### FRED vintage semantics — explicit documentation (Macha items 5/14)

To state this precisely, as requested: when `FRED_API_KEY` is set, `as_of_date` for `US_FED_FUNDS_RATE` is the **FRED Vintage Knowledge Date** — the `realtime_start` value returned by FRED's ALFRED vintage system for that specific observation, representing the actual date that value was first published/known, not an approximation. This is only available on the API-key path; the no-key CSV fallback path has no such metadata and uses the documented `obs_date + 1 business day` fallback instead, as already stated above.

---



| Metric | D05.SI (`prices_daily`) | ^STI (`index_daily`) |
|---|---|---|
| Rows | 6,720 | 9,137 |
| Date range | 2000-01-03 to 2026-07-17 | 1990-01-02 to 2026-07-17 |
| Rejected rows | 0 | 0 |
| Warnings | 83 (zero/null-volume) | — |

Both instruments loaded successfully. **This confirms DuckDB and yfinance both work correctly end-to-end in the actual project environment.** Earlier sections of this file's history (below) describe extensive testing done in Cola's own development sandbox, which has no network access and could not install DuckDB or yfinance at all — that limitation was specific to the sandbox used to *write* the code, never a statement about the real project environment. Any earlier wording in this file that could be read as implying DuckDB "hasn't run" refers only to that authoring sandbox and has been corrected below.

---

## MP-P3-028 — SORA endpoint investigation, DuckDB SQL fix (2026-07-19)

**Trigger:** Sprite's first full WSL verification run. Results: rollback, logging/exit-code, FRED, and Yahoo Finance FX all **PASS**. DuckDB integrity and SORA **FAILED**; idempotency blocked as a consequence of the SORA failure (expected — no SORA data to re-run against).

### SORA investigation: `JSONDecodeError: Expecting value: line 1 column 1 (char 0)`

**Root cause:** this error means `.json()` was called on an empty or non-JSON response body. The prior implementation had no diagnostics beyond the bare exception — no way to tell *why* from the error alone.

**What I could and couldn't determine:** I don't have network access to MAS's live endpoint from this sandbox, so I could not reproduce the failure directly or confirm the exact cause with certainty. What I did find via research: a second, independently-sourced technical walkthrough of this same MAS API uses a **different** `resource_id` (`5f2b18a8-0883-4769-a635-879c63d3caac`) than the one currently configured (`9a0bf149-308c-4bd2-832d-76c8e6cb47ed`), and — notably — that working example explicitly sets a browser-like `User-Agent` header. Government/institutional APIs silently rejecting requests without one (returning an empty body rather than a clean error) is a well-documented pattern, and it matches the reported symptom exactly.

**What I changed, and what I deliberately did not:**
1. **Added a browser-like `User-Agent` header** to the SORA request (`ingestion/macro.py`, `_SORA_REQUEST_HEADERS`). Well-evidenced, safe, standard hardening — not a guess.
2. **Did NOT swap the `resource_id`** to the second candidate. I can't verify which (if either) is correct for daily SORA specifically without a live call, and substituting one unverified guess for another isn't a fix — it's moving the uncertainty around. Both IDs are now documented in `config.py` with the reasoning, so Sprite can try the alternate quickly if the first still fails.
3. **Added rich failure diagnostics** (`_describe_response_for_diagnostics()` in `ingestion/macro.py`): on any JSON-parse or HTTP-error failure, the `IngestionFailure` message now includes HTTP status code, full response headers, `Content-Type`, the final requested URL, a JSON-vs-HTML heuristic, the first 500 characters of the response body, and a plain-English interpretation of the likely cause. **Verified working** by reproducing the exact reported error (empty body, `Content-Type: text/html`) against a mocked response and confirming the diagnostic output is correct and complete — see `test_sora_json_decode_error_produces_rich_diagnostics` (new permanent regression test).
4. The ingestion layer still fails loud: no exception suppression, no silent infinite retry (each call attempts once), no fallback data substitution. Confirmed via `test_sora_request_sends_browser_like_user_agent` and the existing fail-loud tests, all still passing.

**If SORA still fails after this fix:** the next failure's error message will itself contain enough information (status code, actual body content, headers) to diagnose definitively, rather than requiring another round of guessing. If the response body indicates the current `resource_id` doesn't exist, try the alternate ID documented in `config.py`.

### DuckDB integrity verification: `Binder Error: column CURRENT_DATE must appear in the GROUP BY clause or be used in an aggregate function`

**Root cause:** the "coverage within plausible bounds" check (`verification/verify_db_integrity.py` / `.sql`) used bare `CURRENT_DATE` inside a `HAVING` clause following `GROUP BY series_id`. This triggers a known DuckDB binder quirk where the niladic `CURRENT_DATE` keyword (no parentheses) gets parsed as a column reference in that clause position, rather than resolved as the current-date value.

**Fix:** replaced `CURRENT_DATE` with `today()` — DuckDB's unambiguous function-call equivalent — in both the SQL file and the Python runner. No check was removed, no coverage was reduced; only the syntax used to express "today" changed. The other `CURRENT_DATE` usage in the same file (`WHERE obs_date > CURRENT_DATE`, no `GROUP BY`) was left as-is, since it isn't in the affected clause position and isn't broken — changing it too would have been an unrelated, unjustified edit.

### Regression check (Task 4)
Re-verified after both fixes, all passing: SORA/FRED/FX normalization unaffected, `as_of_date < obs_date` validation unaffected, the future-`obs_date` fix from the prior turn unaffected, transport-exception fail-loud behavior unaffected, empty-records fail-loud behavior unaffected, duplicate prevention, revision handling, and transaction rollback all unaffected (re-verified via the SQLite-substitution technique established in earlier phases). `tests/test_phase3_macro_ingestion.py` now has 22 test functions (+2 for this turn's SORA diagnostic fix).

### What remains unverified
Everything requiring live network access or a real DuckDB file — same constraint as every prior phase. This fix is my best-evidence response to a real reported failure, not a confirmed resolution. **Production sign-off cannot be granted from this turn alone** — it requires Sprite re-running the full verification suite in WSL and the SORA/DuckDB checks actually passing there.

---

## Outstanding verification: ex-dividend / corporate-action field mapping

**Status: not implemented in Phase 2 — this is a genuine scope gap, not a bug, and no code was changed as a result of this review (per instruction: only fix if a genuine bug is found).**

### What I checked
Inspected `ingestion/prices.py`, `db/schema.sql`, and `validation/checks.py` directly for any handling of dividends, stock splits, or corporate-action events.

### What I found
- `EXPECTED_COLUMNS = ["Open", "High", "Low", "Close", "Adj Close", "Volume"]` — the six fields Phase 2 fetches and validates. `Dividends` and `Stock Splits` (the fields yfinance can optionally return) are not in this list.
- The `yfinance.download(...)` call in `_fetch_raw()` does not pass `actions=True` (or equivalent), so yfinance never returns discrete dividend/split event data to this code in the first place — there's nothing to map, because nothing is requested.
- No table in `db/schema.sql` stores corporate-action events. `raw_fundamentals` exists, but it's shaped for period-based valuation *ratios* (`metric_name` like `'EPS'`, `'ROE'`, `'DIVIDEND_YIELD'` per quarter) — not discrete per-share cash events tied to a specific ex-dividend date. It's also unpopulated regardless (correctly deferred to Build Order step 8, per PROJECT_SPEC.md).

**Conclusion: Phase 2 does not fetch or store ex-dividend/corporate-action event data. I am not going to invent a verification result for something that was never implemented.**

### What Phase 2 *does* do, and what that does and doesn't tell us
The code does store both `close` (actual traded price) and `adj_close` (yfinance's dividend/split-adjusted series) as separate columns — this was the original goal from the initial Phase 2 review round ("preserve the distinction between actual historical OHLC prices and adjusted close/total-return-oriented price"), and that distinction *is* structurally preserved. What it does **not** give is a discrete, dated *event* ("DBS paid a S$0.54 dividend, ex-date 2024-08-15") — only the net cumulative effect embedded in the adj_close series.

I don't have access to Sprite's real DuckDB file to check this myself. To actually verify the close-vs-adj_close mapping behaves as expected, I found a real, well-documented DBS ex-dividend date via web search (cross-confirmed by two independent sources: moomoo.com and tipranks.com) — **2024-08-15, dividend S$0.54/share**. Sprite, run this against your real database:

```sql
SELECT trade_date, close, adj_close
FROM prices_daily
WHERE trade_date BETWEEN '2024-08-08' AND '2024-08-22'
ORDER BY trade_date;
```

What to look for: `adj_close` should be **lower than** `close` for dates *before* 2024-08-15 (retroactively adjusted down to account for the future dividend), and the two should converge to being equal (or very close) for dates *on or after* 2024-08-15, up until the next dividend event. This is a more reliable check than looking for an exact dollar-for-dollar price drop on the ex-date itself, since same-day price moves also reflect ordinary market activity, not just the dividend.

### Should this be implemented now, or deferred?

**Recommend: defer to the later fundamentals/event data layer (Build Order step 8 / Phase 7), not built now.** Reasoning:
1. Phase 2's approved scope was explicitly OHLCV only — adding a corporate-actions table now would be exactly the kind of architecture change this review round was told not to make.
2. This isn't blocking anything currently planned: `adj_close` already supports return-based feature calculations (momentum, volatility) planned for Category A/B features in PROJECT_SPEC.md Section 7, since those are designed to use adjusted returns.
3. When it is built, it naturally belongs alongside the fundamentals work, not the price pipeline — both involve DBS-specific, point-in-time-sensitive event data with the same "was this actually knowable yet" leakage concern as `raw_fundamentals`.
4. **Design note for that future phase, recorded now so it isn't lost:** a `corporate_actions` table should track both `declaration_date` and `ex_date` separately, and should very likely use `declaration_date` (when the dividend/split is publicly announced) as the `availability_date` — not `ex_date`. Declaration date is typically well before the ex-date, so using ex_date as the availability convention would risk understating how early this information was actually knowable, which is the opposite direction of a leakage risk but still a modeling-accuracy issue worth getting right when that phase starts.

---

## yfinance version compatibility audit (2026-07-19, final — corrects a mistaken intermediate entry)

**This entry is the authoritative record.** This file has gone through two incorrect states on this question before landing here, and both corrections are recorded for transparency rather than erased:
1. An entry pinned `1.4.1`, reasoning from PyPI release notes ("latest stable, no breaking changes") without confirmed evidence of what actually worked in Sprite's environment.
2. A subsequent entry incorrectly reverted to `0.2.40`, based on a mistaken claim that `0.2.40` was the version that had actually succeeded.
3. **Sprite has now directly corrected the record with the actual tested sequence, which this entry reflects.**

**Confirmed sequence, as reported by Sprite:**
- `yfinance==0.2.40` (the original pin) was tested directly against **both D05.SI and AAPL** and **failed both times** with `JSONDecodeError`/empty DataFrame.
- `yfinance` was then manually upgraded to **`1.5.1`** in the active `.venv`.
- The first successful live ingestion — D05.SI: 6,720 rows, 2000-01-03 to 2026-07-17; ^STI: 9,137 rows, 1990-01-02 to 2026-07-17, 0 rejected — occurred under **`1.5.1`**.

**What I could and couldn't verify myself:**
- **Actual installed version in `/mnt/d/Projects/MarketPulse-SGX/github/.venv`:** I cannot access Sprite's WSL filesystem or execute anything in that `.venv` — I have no tool that reaches it. I am not guessing a number; this is taken entirely from Sprite's direct report.
- **Independent check on `1.5.1` itself:** I confirmed via a fresh PyPI/GitHub release check that `yfinance 1.5.1` is a real release (June 28, 2026, currently the latest) — this at least rules out the number being a typo for a nonexistent version.
- **API compatibility:** `ingestion/prices.py`'s only yfinance call, `yfinance.download(ticker, start=, end=, auto_adjust=, progress=)`, uses the same four keyword arguments regardless of which of these versions is installed — nothing in this project's code is version-conditional, so no code change is implied by this pin change.

**`requirements.txt` version at the start of this audit:** `0.2.40` (from the incorrect intermediate correction) — mismatched against both the demonstrated-failing version's own failure and the demonstrated-working version.

**Recommendation: Option A — pin `yfinance==1.5.1`.** This is based purely on reproducible evidence (it is the specific version under which real ingestion actually succeeded), not on it being newer or on release-note claims — consistent with the instruction not to upgrade/downgrade for those reasons alone. Do **not** revert to `0.2.40`: it has now been directly tested twice (D05.SI and AAPL) and failed both times.

**Change made:** `requirements.txt` corrected from `0.2.40` to `1.5.1`. No other files changed.

**No ingestion logic was changed** — confirmed via `py_compile` on all Phase 2 Python files, unmodified.

---

## Post-run correction: cross-instrument consistency check was too broad

The real data exposed a real problem: `check_cross_instrument_date_consistency()` compared full history on both sides, and because ^STI's history (from 1990) starts 10 years before D05.SI's (from 2000, when it was listed), every pre-2000 ^STI date was flagged as a false "D05.SI missing" anomaly. Likely SG public holidays (e.g. 2000-05-01, 2000-08-09, 2000-12-25, 2001-01-01) also showed up as anomalies within that mismatched range — confirming this check was never a reliable generic holiday-gap detector (consistent with the module's own docstring, which already disclaimed this).

**Fix applied (2026-07-19):** the check now restricts its comparison to the overlapping date range only — `max(first date of either instrument)` through `min(last date of either instrument)`. Dates outside that window are not evaluated at all (not reclassified as "fine" — they were never a fair comparison in the first place). No SG holiday calendar was added.

**Verification:** since this function's SQL (`COUNT`, `MIN`/`MAX`, `LEFT JOIN`, `WHERE ... BETWEEN ? AND ?`) is plain ANSI SQL, the actual unmodified function was run in Cola's sandbox against an in-memory SQLite stand-in (not a reimplementation), using a fixture shaped like the real D05.SI/^STI situation. All 7 assertions passed: pre-overlap and post-overlap dates correctly excluded, a genuine in-window anomaly correctly still caught, the "review, not confirmed missing data" wording preserved, and the pre-existing empty-side skip behavior unaffected. `tests/test_phase2_ingestion.py` was updated with a permanent test (`test_cross_instrument_check_ignores_pre_overlap_and_post_overlap_dates`) covering the same scenario for when real `pytest`/`duckdb` are available.

---

## Decisions log

| Date | Decision | Status |
|---|---|---|
| 2026-07-18 | Banking-sector peer features (OCBC/UOB) | **DEFERRED** — recorded as possible V1.1 extension, not in V1 |
| 2026-07-18 | News/sentiment data | **EXCLUDED from V1** — recorded as candidate V2 research experiment; no news-related tables/code/architecture in V1 |
| 2026-07-18 | Phase 2 scope | Approved and implemented: D05.SI + ^STI OHLCV ingestion, DuckDB storage, raw preservation, normalized storage, duplicate handling, validation, cross-instrument date consistency check, basic data-quality reporting. |
| 2026-07-18 | Phase 2 revisions (6 changes) | Approved and implemented: explicit `auto_adjust=False`; renamed `raw_prices_daily`→`prices_daily`, `raw_index_daily`→`index_daily`; split fetch audit into `price_fetches` + `raw_price_rows`; reframed gap check as "cross-instrument date consistency / anomaly detection"; added explicit `availability_date` point-in-time convention; fail-loud on empty/invalid source response. |
| 2026-07-19 | Code review | Full review against 10 points; found 2 CRITICAL (no transaction/rollback), 4 IMPORTANT, 1 MINOR. |
| 2026-07-19 | Hardening patch | Approved and implemented: all CRITICAL/IMPORTANT findings fixed, MINOR finding restored. |
| 2026-07-19 | First real ingestion run (WSL, real DuckDB + real yfinance) | **Successful** — see results table above. |
| 2026-07-19 | Cross-instrument overlap-window correction | Approved, implemented, and verified against the real-data failure mode. |
| 2026-07-19 | Ex-dividend/corporate-action field mapping review | **Not implemented in Phase 2** (genuine scope gap, not a bug). Deferred to Build Order step 8. No code changed. SQL query for Sprite to self-verify close-vs-adj_close behavior provided above. |
| 2026-07-19 | yfinance version compatibility audit | **Finalized.** Confirmed sequence: `0.2.40` tested and failed against both D05.SI and AAPL; `1.5.1` installed and produced the first successful live ingestion. `requirements.txt` corrected to `yfinance==1.5.1`. Two earlier entries in this file (upgrade to `1.4.1`, then an incorrect revert to `0.2.40`) were both wrong and are superseded by this one. |

---

## Files changed across Phase 2 (cumulative)

**Core implementation:** `db/schema.sql`, `config.py`, `ingestion/prices.py`, `validation/checks.py`, `scripts/run_ingestion.py`
**Hardening patch (2026-07-19):** transaction/rollback wrapping, revision detection, yfinance exception handling, robust MultiIndex handling, restored listing-date check — all in `ingestion/prices.py`, `validation/checks.py`, `config.py`
**Overlap-window correction (2026-07-19, post-real-run):** `validation/checks.py`, `scripts/run_ingestion.py`
**Tests:** `tests/test_phase2_ingestion.py` (new — added because the hardening patch explicitly required deterministic tests; not part of the original 15-file skeleton, flagged rather than silently added)
**This review (ex-dividend gap):** no code changed — documentation only (this file)

## Database tables created or modified (cumulative, Phase 2)

- **Created:** `price_fetches`, `raw_price_rows`, `data_quality_warnings`
- **Renamed + modified:** `raw_prices_daily` → `prices_daily` (+`availability_date`), `raw_index_daily` → `index_daily` (+`availability_date`)
- **Modified:** `data_availability_log` (generalized to `entity_type`/`entity_id`)
- **Unchanged:** `dim_securities`, `dim_indices`, `raw_macro_series`, `raw_fundamentals`, `feature_store`, `labels`, `situation_matches`, `model_runs`, `predictions`, `backtest_results`
- **Not created (deliberately deferred):** a corporate-actions/dividend-events table — see the outstanding-verification section above

## What is currently implemented

- Full Phase 2 ingestion and validation, confirmed working against real data (6,720 + 9,137 rows, 0 rejections).
- Transaction-safe normalized-table updates, revision detection and audit logging, yfinance exception handling, robust MultiIndex handling, listing-date validation mechanism (value unset pending a verified date).
- Cross-instrument consistency check, corrected to the overlap window after the real-data run exposed the full-history comparison's flaw.

## What is not implemented (unchanged scope, some newly confirmed by this review)

- Ex-dividend/corporate-action event data — confirmed not implemented, deferred to Build Order step 8 (see above).
- Macro, fundamentals, features, labeling, situation matching, ML, news/sentiment, Streamlit UI — all still out of scope for Phase 2, unchanged from Phase 1.
- `pytest`-based execution of `tests/test_phase2_ingestion.py` and `tests/test_leakage.py` has not happened in Cola's development sandbox (no network to install `pytest`/`duckdb`) — but Sprite's WSL environment now has both installed and working, so this should be run there next.

## Known open risks

Unchanged from PROJECT_SPEC.md Section 17, plus: the close-vs-adj_close mapping is still only structurally verified (separate columns, sourced from separate yfinance fields) and not yet empirically confirmed against Sprite's real data — the SQL query above is the next step to close that gap. No corporate-action event data exists yet, so any future feature or label that would benefit from knowing "was this a dividend day" specifically (as opposed to just using adjusted returns) isn't currently possible — acceptable for now, since Build Order step 8 covers it.

## Next recommended action

Phase 3 (macro ingestion) has now been implemented — see the Phase 3 implementation record and Macha audit-response sections above. Two independent, non-blocking next steps remain:
1. Sprite runs the ex-dividend/corporate-action SQL query (Phase 2 section above) against the real DuckDB file, to close that verification gap.
2. Sprite runs `python -m pytest tests/test_phase3_macro_ingestion.py -v` in the WSL `.venv`, per Macha's instruction, and reports the results back before the live macro ingestion run proceeds.
