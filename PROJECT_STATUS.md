# MarketPulse SGX — Project Status

**Last updated:** 2026-07-19
**Phase:** 2 — Real price/index ingestion. **Successfully run against real data in Steven's WSL environment with real DuckDB and yfinance 1.5.1 (see the yfinance version compatibility audit below — 0.2.40 failed, 1.5.1 succeeded). One post-run data-quality correction applied and verified (cross-instrument overlap window). One outstanding item identified as a genuine scope gap, not a bug (ex-dividend/corporate-action event data) — recommendation below. Phase 2 substantively complete; awaiting sign-off before Phase 3.**

---

## First real ingestion run — actual results (2026-07-19, run by Steven in WSL, real DuckDB + real yfinance)

| Metric | D05.SI (`prices_daily`) | ^STI (`index_daily`) |
|---|---|---|
| Rows | 6,720 | 9,137 |
| Date range | 2000-01-03 to 2026-07-17 | 1990-01-02 to 2026-07-17 |
| Rejected rows | 0 | 0 |
| Warnings | 83 (zero/null-volume) | — |

Both instruments loaded successfully. **This confirms DuckDB and yfinance both work correctly end-to-end in the actual project environment.** Earlier sections of this file's history (below) describe extensive testing done in Claude's own development sandbox, which has no network access and could not install DuckDB or yfinance at all — that limitation was specific to the sandbox used to *write* the code, never a statement about the real project environment. Any earlier wording in this file that could be read as implying DuckDB "hasn't run" refers only to that authoring sandbox and has been corrected below.

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

I don't have access to Steven's real DuckDB file to check this myself. To actually verify the close-vs-adj_close mapping behaves as expected, I found a real, well-documented DBS ex-dividend date via web search (cross-confirmed by two independent sources: moomoo.com and tipranks.com) — **2024-08-15, dividend S$0.54/share**. Steven, run this against your real database:

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
1. An entry pinned `1.4.1`, reasoning from PyPI release notes ("latest stable, no breaking changes") without confirmed evidence of what actually worked in Steven's environment.
2. A subsequent entry incorrectly reverted to `0.2.40`, based on a mistaken claim that `0.2.40` was the version that had actually succeeded.
3. **Steven has now directly corrected the record with the actual tested sequence, which this entry reflects.**

**Confirmed sequence, as reported by Steven:**
- `yfinance==0.2.40` (the original pin) was tested directly against **both D05.SI and AAPL** and **failed both times** with `JSONDecodeError`/empty DataFrame.
- `yfinance` was then manually upgraded to **`1.5.1`** in the active `.venv`.
- The first successful live ingestion — D05.SI: 6,720 rows, 2000-01-03 to 2026-07-17; ^STI: 9,137 rows, 1990-01-02 to 2026-07-17, 0 rejected — occurred under **`1.5.1`**.

**What I could and couldn't verify myself:**
- **Actual installed version in `/mnt/d/Projects/MarketPulse-SGX/github/.venv`:** I cannot access Steven's WSL filesystem or execute anything in that `.venv` — I have no tool that reaches it. I am not guessing a number; this is taken entirely from Steven's direct report.
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

**Verification:** since this function's SQL (`COUNT`, `MIN`/`MAX`, `LEFT JOIN`, `WHERE ... BETWEEN ? AND ?`) is plain ANSI SQL, the actual unmodified function was run in Claude's sandbox against an in-memory SQLite stand-in (not a reimplementation), using a fixture shaped like the real D05.SI/^STI situation. All 7 assertions passed: pre-overlap and post-overlap dates correctly excluded, a genuine in-window anomaly correctly still caught, the "review, not confirmed missing data" wording preserved, and the pre-existing empty-side skip behavior unaffected. `tests/test_phase2_ingestion.py` was updated with a permanent test (`test_cross_instrument_check_ignores_pre_overlap_and_post_overlap_dates`) covering the same scenario for when real `pytest`/`duckdb` are available.

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
| 2026-07-19 | Ex-dividend/corporate-action field mapping review | **Not implemented in Phase 2** (genuine scope gap, not a bug). Deferred to Build Order step 8. No code changed. SQL query for Steven to self-verify close-vs-adj_close behavior provided above. |
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
- `pytest`-based execution of `tests/test_phase2_ingestion.py` and `tests/test_leakage.py` has not happened in Claude's development sandbox (no network to install `pytest`/`duckdb`) — but Steven's WSL environment now has both installed and working, so this should be run there next.

## Known open risks

Unchanged from PROJECT_SPEC.md Section 17, plus: the close-vs-adj_close mapping is still only structurally verified (separate columns, sourced from separate yfinance fields) and not yet empirically confirmed against Steven's real data — the SQL query above is the next step to close that gap. No corporate-action event data exists yet, so any future feature or label that would benefit from knowing "was this a dividend day" specifically (as opposed to just using adjusted returns) isn't currently possible — acceptable for now, since Build Order step 8 covers it.

## Next recommended action

Two independent, non-blocking next steps, either order:
1. Steven runs the SQL query above against the real DuckDB file to close the close/adj_close verification gap.
2. Once that's done and reviewed, Phase 3 (macro ingestion — SORA, Fed funds rate) can begin. **Not started yet, per instruction.**
