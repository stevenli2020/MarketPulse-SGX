# MarketPulse SGX — Session Context (as of 2026-07-25)

## 1. Core Objective
Build a transparent, explainable, research-grade historical market analysis system for DBS Group Holdings (SGX: D05.SI). Eventually estimate probability and direction of DBS price over 5 and 10 trading day horizons. Research/decision-support tool only — no trades, no exact price prediction, no deep learning.

## 2. Key Decisions & Technical Constraints

### Stack
Python · DuckDB 1.0.0 · pandas 2.2.2 · numpy 1.26.4 · yfinance 1.5.1 · requests 2.32.3 · scikit-learn · XGBoost · SHAP · VectorBT · Streamlit · pytest 8.3.2

### Privacy masking (apply to all .md documentation)
- Steven → Sprite
- Claude → Cola
- ChatGPT → Macha

### Project location
`/mnt/d/Projects/MarketPulse-SGX/github` (WSL)
Virtual env: `/mnt/d/Projects/MarketPulse-SGX/github/.venv` (Python 3.12.11)
GitHub: `https://github.com/stevenli2020/MarketPulse-SGX.git` (main branch, currently at `ad50dc5`)

### MCP Connector
Live WSL MCP connector is operational. Use `write_project_file`, `execute_python_script`, `run_verification_module`, `run_git_command` directly. `execute_python_script` runs as `python <path>` (not `-m`), so scripts need the `sys.path` shim:
```python
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
```
`run_verification_module` uses `-m` so no shim needed for modules.

### Core architecture rules (never violate)
- Observation date ≠ availability/as_of date — always separate, always stored
- Features may use data through close of T; labels start from T+1 trading day
- No look-ahead bias; no global scaling; no random train/test splits
- Fail-loud on unusable source data (never treat empty/error as "no new data")
- Explicit DuckDB transactions on all normalized writes; rollback on failure
- Idempotent upserts: insert/unchanged-skip/revised-update with revision logging
- No hardcoded secrets; all API keys via `os.environ` only

### Deferred / out of scope for V1
- Banking sector peers (OCBC/UOB): deferred to V1.1
- News/sentiment: deferred to V2 research experiment
- Deep learning / RL / LSTM / Transformers: explicitly excluded

## 3. Current Progress

### Phase 0 — Spec ✅
PROJECT_SPEC.md and PROJECT_STATUS.md created. Full 17-section spec including features (40), targets, backtesting methodology, leakage audit checklist.

### Phase 1 — Skeleton ✅
14-file project structure created and committed. All non-price modules are deliberate `NotImplementedError` stubs.

### Phase 2 — Price/index ingestion ✅ FULLY VERIFIED
- D05.SI: 6,720 rows · 2000-01-03 to 2026-07-17 · 0 rejected · 83 zero-volume warnings
- ^STI: 9,137 rows · 1990-01-02 to 2026-07-17 · 0 rejected
- Tables: `prices_daily`, `index_daily` (renamed from raw_*), `price_fetches`, `raw_price_rows`, `data_quality_warnings`
- Hardening: explicit transactions, revision detection, yfinance exception wrapping, robust MultiIndex handling, listing-date validation, cross-instrument check scoped to overlap window only
- pytest: `tests/test_phase2_ingestion.py` — 8/8 passed (real WSL run confirmed)

### Phase 3 — Macro ingestion ✅ FULLY VERIFIED (all stages PASS)
Three macro series stored in `raw_macro_series (series_id, obs_date, value, as_of_date, source, ingested_at)` PK: `(series_id, obs_date, as_of_date)`

**SORA** (series_id=`SORA`)
- Source: MAS APIMG gateway (NOT the retired legacy CKAN endpoint)
- URL: `https://eservices.mas.gov.sg/apimg-gw/server/monthly_statistical_bulletin_non610mssql/domestic_interest_rates_daily/views/domestic_interest_rates_daily`
- Auth: HTTP header `keyid: <value>` — env var `MAS_APIMG_SUBSCRIPTION_KEY`
- Filter: Denodo `$filter=end_of_day >= 'YYYY-MM-DD' and end_of_day <= 'YYYY-MM-DD'`
- Fields: `end_of_day` (obs_date), `sora` (value), `published_date` (real as_of_date when non-null, else T+1 business day fallback)
- Data: 5,289 rows · 2005-07-01 to 2026-07-23
- Key insight: MAS data contains within-batch duplicate (obs_date, as_of_date) pairs — handled by dedup logic (last-in-batch wins, conflicts flagged as `within_batch_conflict`)

**US Fed Funds Rate** (series_id=`US_FED_FUNDS_RATE`)
- Source: FRED, series EFFR
- With `FRED_API_KEY`: vintage JSON API (`realtime_start=1776-07-04` for real publication dates)
- Without key (default): public CSV `https://fred.stlouisfed.org/graph/fredgraph.csv?id=EFFR`, as_of_date = obs_date + 1 business day
- Data: 6,542 rows · 2000-07-03 to 2026-07-23

**SGD/USD FX** (series_id=`SGD_USD_FX`)
- Source: yfinance 1.5.1, ticker `USDSGD=X` (not SGDUSD=X), `auto_adjust=False`
- as_of_date = obs_date = trade_date (same Phase 2 market-data convention)
- Data: 5,889 rows · 2003-12-01 to 2026-07-25

**Phase 3.5 verification final results (with MAS_APIMG_SUBSCRIPTION_KEY exported):**
- Rollback: PASS · Logging/exit-code: PASS · DB integrity: PASS · Idempotency: PASS · Live ingestion: PASS
- All 35 unit tests pass (27 Phase 3 + 8 Phase 2)

### Key env vars required
```bash
export MAS_APIMG_SUBSCRIPTION_KEY=c6337dce-ef16-49d5-b9ad-6af41bedb894
# FRED_API_KEY optional (enables real vintage publication dates vs T+1 fallback)
```

### Database tables currently populated
`dim_securities`, `dim_indices`, `prices_daily`, `index_daily`, `price_fetches`, `raw_price_rows`, `data_quality_warnings`, `raw_macro_series`

### Database tables existing but empty (correct — future phases)
`raw_fundamentals`, `feature_store`, `labels`, `situation_matches`, `model_runs`, `predictions`, `backtest_results`

### Key files
```
config.py                            — all settings + MACRO_SOURCE_CONFIG
db/schema.sql                        — 16 tables
ingestion/prices.py                  — Phase 2 complete
ingestion/macro.py                   — Phase 3 complete (SORA/FRED/FX)
validation/checks.py                 — validates both price and macro rows
scripts/run_ingestion.py             — orchestrates prices + macro
tests/test_phase2_ingestion.py       — 8 tests
tests/test_phase3_macro_ingestion.py — 27 tests
verification/                        — Phase 3.5 verification suite
pytest.ini                           — pythonpath=. testpaths=tests
SESSION_CONTEXT.md                   — this file
```

## 4. Next Steps

**Phase 4 — Feature Engineering & Labeling (NOT YET STARTED)**

Per PROJECT_SPEC.md Section 11 (Build Order), Phase 4 involves:
- `features/feature_engineering.py` — implement 36 features (categories A–E, no fundamentals yet):
  - A: price/trend (7) — ret_1d, ret_5d, ret_10d, ret_20d, ret_60d, sma10_ratio, dist_52wk_high
  - B: volatility (5) — vol_10d, vol_20d, vol_60d, atr_14, vol_of_vol
  - C: volume (4) — vol_ratio_10d, vol_ratio_50d, obv_slope_20d, dollar_vol_20d_avg
  - D: STI/market (5) — sti_ret_5d, sti_ret_10d, rel_strength_5d, rel_strength_20d, beta_60d
  - E: interest rates (6) — sora_level, sora_change_20d, fed_funds_rate, fed_funds_change_60d, rate_spread_sg_us, rate_trend_flag
  - FX (2) — sgd_usd_ret_20d, sgd_usd_vol_20d
  - Market regime (3) — vol_regime_flag, sti_trend_regime_flag, yield_curve_regime_flag
- `labeling/labels.py` — direction_5d and direction_10d binary labels
- `tests/test_leakage.py` — implement the currently-skipped leakage prevention tests
- **Critical constraint**: features computed "as of" date T must only use data with
  `availability_date <= T`. Label code and feature code must never import each other.

**Before Phase 4 begins, confirm:**
1. Run `python -m verification.run_all_verifications` with key exported — expect OVERALL PASS
2. Sprite formally approves Phase 3 sign-off

## 5. Important References

### Scratchpad/temporary script convention
Prefix with `_` or `_TEMP_` — removed after use, never committed.
`mcp_server.py` is Sprite's MCP connector — never touch or commit it.

### Commit history (key milestones)
- `61a9408` — baseline before MCP work began
- `658e8d2` — MCP smoke test added
- `3d10044` — __pycache__ + marketpulse.duckdb untracked from git
- `29dc33d` — MP-P3-029: SORA endpoint investigation evidence
- `f96e87d` — MP-P3-029b: real SORA APIMG integration + dedup fix + 35 tests
- `ad50dc5` — MP-P3-029c: reconciliation false-alarm fix (CURRENT HEAD)

### Phase 3.5 verification command
```bash
export MAS_APIMG_SUBSCRIPTION_KEY=c6337dce-ef16-49d5-b9ad-6af41bedb894
python -m verification.run_all_verifications
```

### Make the key persistent (run once)
```bash
echo 'export MAS_APIMG_SUBSCRIPTION_KEY=c6337dce-ef16-49d5-b9ad-6af41bedb894' >> ~/.bashrc
source ~/.bashrc
```

### Point-in-time convention (enforced everywhere)
- Prices: `availability_date = trade_date` (knowable after session close)
- SORA: `as_of_date = published_date` if non-null, else `obs_date + 1 BDay`
- FRED (with key): `as_of_date = realtime_start` (real vintage publication date)
- FRED (no key): `as_of_date = obs_date + 1 BDay` (documented fallback, never collapsed to obs_date)
- FX: `as_of_date = obs_date = trade_date`
- Macro validation hard-rejects any row where `as_of_date < obs_date`

### How to start a new session
Paste the contents of this file into a new chat, prefixed with:
> "You are Cola, the lead developer on the MarketPulse SGX project. Here is the full session context:"

Then ask Cola to confirm the context and run `python -m verification.run_all_verifications`
before starting Phase 4.
