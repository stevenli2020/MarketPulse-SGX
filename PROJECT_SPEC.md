# MarketPulse SGX — Project Specification (v2)

**Status:** Planning / specification — supersedes the v1 spec with a more rigorous, more detailed version. No architecture already agreed in Phase 0/1 has been silently changed; two genuinely new items surfaced by this revision (a banking-sector peer feature group, and news/sentiment) are flagged explicitly in Sections 7 and 5 rather than folded in quietly.
**Owner:** Steven
**Lead architect/developer:** Claude
**Last updated:** 2026-07-18

---

## 1. Project Objective

MarketPulse SGX is a transparent, explainable historical-market-analysis tool. For DBS Group Holdings (SGX: D05.SI), it estimates the **probability and expected direction** of a positive or negative return over the next 5 and 10 trading days, based on historical price, volume, index, macroeconomic, and fundamental data.

The output is framed as a historical statistical statement, e.g.: *"Based on 47 historically similar market situations, DBS moved higher over the following 10 trading days in 64% of cases."* This phrasing matters — it's a claim about historical base rates conditional on similar features, not a claim about the future. The system is a research and decision-support tool. It does not manage money.

The architecture is designed so a second SGX stock can be added later without schema changes — but **v1 ships DBS only.**

---

## 2. Non-Goals

MarketPulse SGX will explicitly **not**:

- Execute trades, place orders, or connect to any brokerage API.
- Predict an exact future share price or price target.
- Claim or imply guaranteed returns, or use language that overstates certainty.
- Use deep learning, reinforcement learning, LSTM, or Transformer models in v1.
- Treat backtested performance as proof of future performance.
- Hide its reasoning behind any prediction — every output must be traceable to specific features and/or specific historical analog days.
- Attempt multi-stock coverage in v1 (schema supports it; data and models do not yet).

---

## 3. User Workflow (Steven, end to end)

1. Open the Streamlit app. Land on a single page showing DBS's current status: last close, recent trend, and whether enough data exists to generate a prediction (some early history may be excluded due to feature warm-up windows — see Section 6).
2. Select a horizon: 5 or 10 trading days.
3. See the model's output: a probability of a positive return (e.g. "58% up / 42% down"), explicitly **not** a price target.
4. See *why*: the top features driving that probability (SHAP-based, in plain language), plus a list of the most similar historical trading days and what happened after each of them (situation matching, Section 9).
5. See the model's honesty section: its walk-forward backtested accuracy, calibration, and how it compares to the naive baselines (Section 11) — displayed every time, not just on request, so a bad result is never hidden behind a good-looking headline number.
6. Optionally browse a "data health" page: what data exists, its date range, and any known gaps or quality caveats.
7. Steven does not place, size, or track any position through this tool. Any investment decision remains entirely his own, made outside the system.

---

## 4. V1 Functional Requirements

V1 must support, exactly:

1. Ingest and store daily D05.SI OHLCV, ^STI OHLCV, SORA, US Fed funds rate, and SGD/USD FX.
2. Ingest and store DBS quarterly fundamentals (manually curated) with correct publication dates.
3. Compute the v1 feature set (Section 7) in a point-in-time-safe manner for any historical date.
4. Compute 5-day and 10-day forward direction labels for any historical date where the outcome is already known.
5. Run historical situation matching (Section 9) for any date, using only data available as of that date.
6. Train and walk-forward validate Logistic Regression, Random Forest, and XGBoost classifiers (Section 10) for both horizons.
7. Report backtested performance against baselines, with calibration and regime breakdown (Section 11).
8. Produce, for the current date, a probability + direction estimate for both horizons, with SHAP-based explanation.
9. Display all of the above in a Streamlit UI (Section 3).
10. Log data coverage/freshness so gaps and staleness are visible, not silent.

V1 explicitly does **not** need to support: multiple securities, sentiment/news data, an automated retraining schedule, or user accounts/authentication (single local user).

---

## 5. Data Requirements

| Category | What / why | Preferred source | Historical availability | Known limitations |
|---|---|---|---|---|
| **DBS prices** (D05.SI OHLCV) | Core price/volume series; the foundation of every price/volatility/volume feature | Yahoo Finance (`yfinance`) | Generally available back to the late 1990s/2000s, daily granularity | Adjusted-close methodology can differ across sources/time (dividend/split adjustments); occasional missing days need gap-checking |
| **STI index** (^STI) | Market-wide context; lets DBS's move be separated into "market-driven" vs "stock-specific" | Yahoo Finance | Similar depth to DBS | Index composition has changed over decades — a 2005 STI move and a 2024 STI move aren't fully like-for-like; treat pre-2010 STI relationships with more caution |
| **Singapore interest rates (SORA)** | Local policy/funding-cost backdrop; DBS is a bank, so rate regime directly affects its earnings outlook | MAS published data | SORA itself is relatively recent (replaced SIBOR ~2021); earlier history needs a proxy (e.g. SIBOR) with a documented, disclosed splice point | No single clean free API; likely light scraping or manual CSV download; splicing two rate series is a genuine methodological seam, not a clean continuous history |
| **US interest rates (Fed funds rate)** | Global capital-flow and risk-appetite backdrop; SG markets correlate meaningfully with US rate cycles | FRED API | Long, clean, well-documented history including release-date metadata | Best-behaved macro source in this list — rarely revised after publication, good release-date data available |
| **SGD/USD FX** | Captures currency-driven effects on a bank with regional/international balance sheet exposure | Yahoo Finance | Long history available | Straightforward series; low risk relative to other macro data |
| **DBS fundamentals** (EPS, dividend yield, ROE, book value) | Slow-moving valuation/earnings-quality context | Manually curated from DBS quarterly/annual reports | Realistically clean for maybe the last 10–15 years; earlier data is harder to source reliably for free | **Weakest data pillar in this project.** No reliable free point-in-time API for historical SG company fundamentals with accurate release dates. Expect a small, manually built table, expanded incrementally, and disclosed as imperfect rather than treated as authoritative. |
| **News / sentiment** | *Evaluated below — not included in v1* | — | — | — |

### Critical evaluation: should news/sentiment be in v1?

**No — deliberately excluded from v1**, for four concrete reasons:

1. **No clean free point-in-time source.** Free news archives and sentiment feeds rarely give you a reliable "this is exactly what was published, at exactly this timestamp" record — and reconstructing that reliably is a nontrivial data-engineering project on its own, with real leakage risk if published-date metadata is even slightly wrong.
2. **Explainability conflict.** Turning news text into a numeric feature normally requires an NLP model (even a "simple" sentiment scorer is a model with its own biases and failure modes). That's a second black box sitting inside a project whose entire premise is *not* being a black box. It would need its own validation effort before being trustworthy.
3. **Weak prior for a stock at this coverage level.** DBS is already extremely well covered by professional analysts; the market-moving news about DBS is priced in fast. There's no strong reason to expect a simple sentiment score to add much at a 5–10 day horizon, though this is a hypothesis worth testing later, not now.
4. **Scope discipline.** Per Rule 8 (simplest working version first), the project should prove the core price/volume/macro/fundamentals pipeline works and is leakage-free before adding a whole new, harder-to-validate data category.

**Decision (Steven, 2026-07-18): EXCLUDED from V1.** No news-related tables, ingestion code, or architecture will be created in V1. Formally recorded as a candidate **V2 research experiment** — to be evaluated only after the core pipeline has a proven, credible baseline result to compare against, treating sentiment as a candidate feature to test for incremental value, not an assumed improvement.

---

## 6. Proposed Database Design (DuckDB)

| Table | Purpose | Important columns | Primary key / uniqueness |
|---|---|---|---|
| `dim_securities` | One row per tradable stock (DBS today, extensible later) | ticker, name, exchange, listed_date | `security_id` |
| `dim_indices` | One row per market index (STI today) | ticker, name | `index_id` |
| `raw_prices_daily` | Immutable daily OHLCV for each security | security_id, trade_date, ohlc, volume, source | (security_id, trade_date) |
| `raw_index_daily` | Immutable daily OHLCV for each index | index_id, trade_date, ohlc, volume, source | (index_id, trade_date) |
| `raw_macro_series` | Rate/FX series, **with the observation date and the knowable-as-of date stored separately** | series_id, obs_date, value, **as_of_date**, source | (series_id, obs_date, as_of_date) |
| `raw_fundamentals` | DBS fundamentals, **with the period the figure describes and the date it was actually released stored separately** | security_id, **period_end_date**, **report_release_date**, metric_name, value | (security_id, period_end_date, metric_name) |
| `feature_store` | Computed features, one row per security/date/feature | security_id, feature_date, feature_name, feature_value, feature_version | (security_id, feature_date, feature_name, feature_version) |
| `labels` | Forward-looking direction labels, computed independently of features | security_id, observation_date, horizon_days, forward_return, direction_label, **label_computable_date** | (security_id, observation_date, horizon_days) |
| `situation_matches` | Nearest-neighbor historical analog results | security_id, observation_date, analog_date, similarity_score, analog_fwd_return_5d/10d | (security_id, observation_date, analog_date) |
| `model_runs` | One row per trained model/backtest run, for reproducibility | run_id, model_name, train/test date ranges, hyperparams_json, feature_version | run_id |
| `predictions` | Model output per date/horizon, for both backtest and live use | run_id, security_id, observation_date, horizon_days, predicted_prob_up, actual_direction | (run_id, security_id, observation_date, horizon_days) |
| `backtest_results` | Aggregated metrics per run/segment | run_id, segment_label, metric_name, metric_value | (run_id, segment_label, metric_name) |
| `data_availability_log` | Audit trail — what data exists, as of when | table_name, security_id, coverage_start/end, last_updated, row_count | (table_name, security_id) |

**The point-in-time discipline is entirely carried by two column pairs**: `obs_date`/`as_of_date` on macro data, and `period_end_date`/`report_release_date` on fundamentals. Every downstream leakage-prevention rule in this document ultimately traces back to these four columns being populated correctly.

This schema is unchanged from Phase 0/1 and already implemented in `db/schema.sql` — nothing here requires a rebuild.

---

## 7. Feature Design (39 features, ≤40 as required)

All features are computed as of a cutoff date and use only data with an availability date ≤ that cutoff (see Section 12). Risk-of-leakage notes are given per group rather than repeated 39 times, since the risk profile is shared within a group; exceptions are called out.

### DBS price (7) — leakage risk: **negligible** if rolling windows end exactly at the cutoff date
| Feature | Calculation | Interpretation |
|---|---|---|
| `ret_1d` | 1-day return | Immediate momentum/reversal |
| `ret_5d` | 5-day cumulative return | Short-term momentum |
| `ret_10d` | 10-day cumulative return | Matches the shorter prediction horizon |
| `ret_20d` | 20-day cumulative return | ~1-month momentum |
| `ret_60d` | 60-day cumulative return | ~1-quarter momentum, catches slower trends |
| `sma10_ratio` | close ÷ 10-day SMA | Short-term trend position |
| `dist_52wk_high` | close vs trailing 252-day max | Proximity to recent highs (breakout/resistance context) |

### DBS volatility (5) — leakage risk: **negligible**
| Feature | Calculation | Interpretation |
|---|---|---|
| `vol_10d` | stdev of daily returns, 10d | Recent turbulence |
| `vol_20d` | stdev of daily returns, 20d | Medium-term turbulence |
| `vol_60d` | stdev of daily returns, 60d | Slower-moving volatility baseline |
| `atr_14` | average true range, 14d | Intraday-range-based volatility, complements close-to-close stdev |
| `vol_of_vol` | vol_20d ÷ vol_60d | Is volatility itself accelerating or calming? |

### DBS volume (4) — leakage risk: **negligible**
| Feature | Calculation | Interpretation |
|---|---|---|
| `vol_ratio_10d` | volume ÷ 10d avg volume | Is trading interest unusually high/low right now? |
| `vol_ratio_50d` | volume ÷ 50d avg volume | Same, over a longer baseline |
| `obv_slope_20d` | on-balance-volume trend, 20d | Whether volume is confirming or diverging from price trend |
| `dollar_vol_20d_avg` | price × volume, 20d average | Liquidity proxy |

### STI / market (5) — leakage risk: **negligible**
| Feature | Calculation | Interpretation |
|---|---|---|
| `sti_ret_5d` | STI 5-day return | Market-wide backdrop, 5d |
| `sti_ret_10d` | STI 10-day return | Market-wide backdrop, 10d |
| `rel_strength_5d` | DBS return − STI return, 5d | Is DBS outperforming or lagging the market? |
| `rel_strength_20d` | Same, 20d | Slower-moving version |
| `beta_60d` | rolling 60d regression beta of DBS vs STI | How sensitive DBS currently is to market-wide moves |

### Interest rates (6) — leakage risk: **real, and the main reason this project exists as designed** — see Section 12
| Feature | Calculation | Interpretation |
|---|---|---|
| `sora_level` | Latest SORA value as of cutoff | Current local funding-cost backdrop |
| `sora_change_20d` | SORA change over trailing 20 trading days | Direction/speed of local rate moves |
| `fed_funds_rate` | Latest Fed funds rate as of cutoff | Global rate backdrop |
| `fed_funds_change_60d` | Fed funds change over trailing 60 trading days | Direction/speed of US rate moves |
| `rate_spread_sg_us` | SORA − Fed funds | Relative policy stance, SG vs US |
| `rate_trend_flag` | Categorical: hiking / cutting / stable, from rate slope | Simplified regime signal, easy to explain in plain English |

### FX (2) — leakage risk: **negligible**
| Feature | Calculation | Interpretation |
|---|---|---|
| `sgd_usd_ret_20d` | SGD/USD return, 20d | Currency-driven backdrop |
| `sgd_usd_vol_20d` | SGD/USD volatility, 20d | FX turbulence, relevant to a bank with cross-border exposure |

### Fundamentals (4) — leakage risk: **real** — must join on `report_release_date`, not `period_end_date` (Section 12)
| Feature | Calculation | Interpretation |
|---|---|---|
| `latest_pe_ratio` | Most recently *released* P/E as of cutoff | Valuation backdrop |
| `latest_dividend_yield` | Most recently released dividend yield | Valuation/income backdrop |
| `latest_roe` | Most recently released ROE | Profitability backdrop |
| `days_since_last_earnings` | Days since last release, as of cutoff | Recency/staleness signal — also flags when fundamentals data is too old to be meaningful |

### Market regime (3) — leakage risk: **negligible**, derived purely from A/D/E groups above
| Feature | Calculation | Interpretation |
|---|---|---|
| `vol_regime_flag` | High/low, based on vol_20d vs its own trailing median | Coarse "calm vs turbulent market" flag |
| `sti_trend_regime_flag` | Bull/bear, based on STI vs its 200d SMA | Coarse market-direction regime |
| `yield_curve_regime_flag` | Categorical, based on rate_spread_sg_us trend | Coarse policy-divergence regime |

**Total: 7+5+4+5+6+2+4+3 = 36 features.**

### Flagged addition — Banking sector peer group (NOT included in the 36 above; **DEFERRED — decision recorded 2026-07-18**)

The requested "banking sector" grouping (comparing DBS to OCBC (O39.SI) and UOB (U11.SI)) is a reasonable idea, but it introduces **two new tickers not in the original data scope**, which is exactly the kind of scope change Rule 14 says shouldn't happen silently. If approved, it would add 3 features (`ocbc_rel_strength_20d`, `uob_rel_strength_20d`, `banking_sector_dispersion`) bringing the total to 39 — still within the 40 limit — but it also means:
- Two more `raw_prices_daily` ingestion feeds to build and validate.
- Two more sources of data-quality risk (gaps, adjustments) before this feature group can be trusted.

**Decision (Steven, 2026-07-18): DEFERRED. Not part of V1. Recorded as a possible V1.1 extension**, to be revisited once the core single-stock pipeline (DBS + STI) is proven end to end.

---

## 8. Prediction Targets

- **Target 5d:** `direction_5d = 1` if close(t + 5 trading days) > close(t), else 0.
- **Target 10d:** `direction_10d = 1` if close(t + 10 trading days) > close(t), else 0.

**Recommended format: binary classification with a calibrated probability output** (`predict_proba`), not a 3-class (up/flat/down) or regression target. Reasoning:

- A regression target (predicting the exact forward return) is explicitly excluded by the project's own non-goals, and is also the hardest version of this problem — return magnitude is dominated by noise at this horizon.
- A 3-class version (up/flat/down) sounds appealing but requires choosing an arbitrary "flat" threshold, which quietly injects a modeling decision that's hard to justify and easy to overfit. It also shrinks each class's sample size, worsening the already-limited effective sample size problem (Section 17).
- Binary + probability is the simplest version that still matches the desired output style ("64% probability of a positive return") and is the easiest to calibrate, backtest, and explain.

---

## 9. Historical Situation Matching (no AI/ML — a deterministic algorithm)

**Plain-English description:** take today's snapshot of a small set of numeric features. Compare that snapshot, mathematically, to every other historical day's snapshot, using a simple distance formula. Find the days that were most similar. Look at what actually happened to DBS in the 5 and 10 trading days after each of those similar days. Report the historical hit rate.

**Concrete algorithm:**

1. Choose a small, interpretable subset of features for matching — not all 36–39, since more dimensions make "similarity" harder to interpret and more prone to spurious matches. Recommended: `ret_20d`, `vol_20d`, `rel_strength_20d`, `sora_change_20d`, `sti_trend_regime_flag` (5–6 features, covering momentum, volatility, relative strength, and rate backdrop).
2. Normalize each feature (z-score, using only data available as of the query date — mean/stdev computed over history up to that date, not the full dataset, to avoid leakage).
3. Compute the Euclidean distance between today's normalized feature vector and every historical date's normalized feature vector (using only dates where the full forward-looking outcome is already known, i.e. at least 10 trading days in the past).
4. Rank historical dates by distance (closest = most similar); take the top 15–20.
5. For those matched dates, compute the percentage that had a positive 5-day forward return, and separately a positive 10-day forward return. This percentage *is* the plain-English probability statement shown to Steven.
6. Store the matched dates and their outcomes in `situation_matches` so the UI can show the actual historical dates behind any given probability — full traceability, no hidden step.

This is deliberately simple (k-nearest-neighbors by Euclidean distance — no learned weights, no neural embeddings) so every step is auditable by hand if needed. Per Section 17, this method has real limitations (regime change means "similar-looking" days may not behave similarly today) and should be presented as one input, not the final word.

---

## 10. Machine Learning Approach

**Proposed order: Logistic Regression → Random Forest → XGBoost.** Complexity is added only when a simpler model demonstrably fails to capture something the data actually contains — not by default.

1. **Logistic Regression (baseline, mandatory first step).** Fast, fully interpretable (coefficients have a direct sign/magnitude meaning), and a legitimate model in its own right for this kind of problem — financial direction prediction is often close to linear in its usable signal, if any signal exists at all. If Logistic Regression already captures most of the achievable accuracy, that is a valuable, honest finding: it says the problem doesn't reward complexity, and we should not manufacture a more complex model to obscure that.
2. **Random Forest (second step).** Captures nonlinearities and feature interactions without needing careful learning-rate/regularization tuning the way boosting does. Still reasonably interpretable via feature importances and partial dependence. This is the right "is there nonlinear structure worth capturing?" test before reaching for XGBoost.
3. **XGBoost (third step, not the default first choice).** The most powerful of the three, and also the most prone to overfitting a dataset with a small effective sample size (Section 17) unless tightly regularized (shallow max_depth, strong L1/L2 penalties, conservative learning rate) and evaluated only with purged walk-forward CV. XGBoost is only worth using if it beats Random Forest by a margin that survives out-of-sample testing across multiple regimes — otherwise the added opacity isn't earning its keep.

**Explicit critical evaluation, as requested:**
- XGBoost is *not* automatically the "best" choice here just because it's the most sophisticated. On a small, noisy, financial dataset, the extra flexibility is often a liability (more ways to fit noise), not an advantage.
- If all three models perform similarly, prefer Logistic Regression for production use — smaller, faster, more explainable, and there is no reason to prefer a black-box-ier model that isn't earning its complexity in performance.
- If none of the three beats the baselines in Section 11 out-of-sample, the honest conclusion is that this feature set does not contain a usable signal at this horizon — and that must be reported as such (Rule 13), not hidden behind an ML pipeline that "runs."

---

## 11. Backtesting Methodology

**Why random train/test splitting is wrong here:** it lets the model train on data from *after* the period it's being tested on, which is look-ahead bias by definition; and even a chronological split still leaks, because the 5-day and 10-day labels overlap across adjacent days.

**Method: purged, embargoed walk-forward validation.**
- **Training period:** expanding window, starting from the earliest available clean data.
- **Validation period:** a rolling block (e.g. 6 months) immediately following the training window, used for hyperparameter selection.
- **Test period:** the next block after validation, held out and untouched during any tuning decision.
- **Walk-forward:** after each test block, the window rolls forward — train grows, validation and test blocks move forward in time — producing multiple out-of-sample test blocks across history rather than one single split.
- **Overlapping-label handling:** before each test block begins, **purge** any training sample whose label window (t to t+5 or t to t+10) extends into the test period, and **embargo** a short buffer (≥10 trading days) after each test block before the next training window starts. Without this, adjacent-day labels leak information across the train/test boundary even in a correctly time-ordered split.
- **Baselines, always reported alongside model results:** naive persistence, unconditional base rate, buy-and-hold, and a simple SMA-crossover rule. A model is only described as "useful" if it beats these out-of-sample, by a margin plausible enough to survive transaction costs and estimation noise (Rule 13).
- **Metrics:** accuracy, precision/recall per class, ROC-AUC, and — importantly — **Brier score/log loss and a calibration plot**, since the product promises a *probability*, and a well-calibrated 60% needs to actually resolve positively about 60% of the time.
- **Regime breakdown:** results reported separately for at least pre-2020, 2020 COVID crash/recovery, the 2022–23 rate-hiking cycle, and the most recent 2 years — never just one blended "all history" number, which can hide a model that only worked in one regime.

---

## 12. Data Leakage Audit Checklist

**Point-in-time usage convention for daily price data (recorded 2026-07-18, Phase 2):** a daily OHLCV observation for trading date T becomes available only after T's session has closed. `prices_daily`/`index_daily` record this explicitly via `availability_date = trade_date` (schema unchanged from Section 6/Phase 2 design — this is a documentation clarification, not a new column). Concretely, once feature engineering and labeling are built (Phases 4+):
- Features computed "as of" date T may use price data through the close of T.
- Prediction targets/labels for observation date T must begin from the next trading date after T.
- No feature or target may use any price information with `availability_date` later than T when making a prediction as of T.

This is what makes the `availability_date <= cutoff` filtering rule (item 3 below) concrete and unambiguous for price data specifically, and is restated in `config.py` and `db/schema.sql` as inline documentation so it's visible at the point of use, not only in this spec.

No model result is considered credible until every item below is checked and passes:

1. ☐ Every `raw_macro_series` row has a populated, correct `as_of_date`, distinct from `obs_date`.
2. ☐ Every `raw_fundamentals` row has a populated, correct `report_release_date`, distinct from `period_end_date`.
3. ☐ Feature computation code takes an explicit `as_of` cutoff parameter and provably cannot query any row with an availability date after that cutoff (code review, not just intention).
4. ☐ `feature_store` and `labels` are populated by code paths that do not import from one another.
5. ☐ For every label, `label_computable_date == observation_date + horizon_days` (trading days), verified by an automated test.
6. ☐ No global (full-dataset) scaling/normalization anywhere — all scaling fit on the training fold only, applied to validation/test folds.
7. ☐ Walk-forward splits are purged and embargoed (Section 11); verified by an automated test that no training sample's label window overlaps the test period.
8. ☐ Situation matching (Section 9) only compares the query date against historical dates whose forward outcome is already fully resolved (≥10 trading days in the past).
9. ☐ Any result that looks unusually good (e.g. >60% out-of-sample accuracy) triggers a mandatory re-check of items 1–8 before being reported, not a note of congratulations.
10. ☐ Backtest results are reported across multiple regimes (Section 11), not a single cherry-pickable blended number.

---

## 13. System Architecture

Unchanged from Phase 0/1 — seven one-directional layers (ingestion → validation → feature engineering → labeling → modeling → backtesting/eval → explainability/UI), backed by a single embedded DuckDB file. No server, no message queue, no orchestration framework — appropriate for one developer, per Rule 9. See `db/schema.sql` and `db/connection.py`, already implemented.

---

## 14. Project Folder Structure

Unchanged from the already-approved and already-built Phase 1 skeleton (14 files):

```
marketpulse_sgx/
├── README.md
├── requirements.txt
├── .gitignore
├── config.py
├── db/{schema.sql, connection.py}
├── ingestion/{prices.py, macro.py, fundamentals.py}
├── validation/checks.py
├── features/feature_engineering.py
├── labeling/labels.py
├── tests/test_leakage.py
└── scripts/run_ingestion.py
```

No new files needed for the spec revision itself. If the optional peer-bank feature group (Section 7) is approved later, it would extend `config.py`'s ticker list and `ingestion/prices.py` — not require new files.

---

## 15. Development Phases

| Phase | Objective | Built | Not yet built | Acceptance criteria |
|---|---|---|---|---|
| **0 — Spec** ✅ done | Define architecture and risks before code | PROJECT_SPEC.md, PROJECT_STATUS.md | Nothing | Steven sign-off |
| **1 — Skeleton** ✅ done | Establish project structure | Folder structure, `config.py`, `db/schema.sql`, `db/connection.py`, all stub modules | Any real data logic | Schema runs cleanly; entry point runs without error (verified) |
| **2 — Price/index ingestion** ← next | Prove one real, validated data source end to end | Real `ingestion/prices.py`, real `validation/checks.py` for prices | Macro, fundamentals, features, labels | D05.SI and ^STI daily OHLCV loaded into DuckDB, gap-checked, logged to `data_availability_log` |
| **3 — Macro ingestion** | Add rate/FX data with correct `as_of_date` | Real `ingestion/macro.py` | Fundamentals, features, labels | SORA, Fed funds, FX loaded with correct availability dates |
| **4 — Features + labels** | Build the point-in-time-safe core | Real `features/feature_engineering.py`, `labeling/labels.py`, real `tests/test_leakage.py` | Fundamentals features, modeling | Leakage tests pass (Section 12 checklist, items 1–8) |
| **5 — Baseline modeling** | Prove the walk-forward harness before adding complexity | Logistic Regression + purged walk-forward CV + baselines | Random Forest, XGBoost | Harness produces baseline comparison correctly on a trivial/dummy model first |
| **6 — Random Forest & XGBoost** | Test whether added complexity earns its keep | RF and XGBoost models, SHAP | Fundamentals, situation matching, UI | Model comparison honestly reported vs Logistic Regression and baselines |
| **7 — Fundamentals** | Add the weakest, highest-effort data pillar | `ingestion/fundamentals.py`, fundamentals features | Situation matching, UI | Re-test whether fundamentals features add anything measurable |
| **8 — Situation matching** | Add explainability layer | KNN-based matching (Section 9) | UI | Matches and outcomes stored and spot-checkable by hand |
| **9 — Streamlit UI** | Make it usable | Prediction view, explanation view, backtest dashboard, data health page | Peer-bank features, sentiment | Steven can run the full workflow (Section 3) end to end |

Each phase's acceptance criteria must pass before the next phase starts — this is unchanged from the Build Order agreed in Phase 0.

---

## 16. V1 Acceptance Criteria

V1 is complete when **all** of the following are true:

1. D05.SI, ^STI, SORA, Fed funds rate, SGD/USD FX, and a manually curated DBS fundamentals table are loaded and validated in DuckDB.
2. The full Section 12 leakage audit checklist passes, with automated tests for items 5, 6, and 7.
3. Logistic Regression, Random Forest, and XGBoost have each been walk-forward backtested for both horizons, with results compared honestly against the four baselines (Section 11), including calibration and regime breakdown.
4. Situation matching runs and produces auditable, traceable analog lists.
5. The Streamlit app supports the full user workflow in Section 3, including displaying backtested performance every time a prediction is shown (never hidden).
6. `PROJECT_STATUS.md` accurately reflects what works, what doesn't, and — critically — an honest statement of whether any model actually beat the baselines out-of-sample. **V1 can be "complete" and still conclude "no model here is good enough to rely on"** — that is a valid, acceptable V1 outcome, not a failure of the project.

---

## 17. Risks and Limitations (critical, not glossed over)

**Is 30 years of historical data genuinely useful?** Partially, and not simply "more is better." DBS itself, the STI's composition, Singapore's rate regime (SIBOR→SORA), and market microstructure have all changed materially over that span. Blending very old and very recent regimes into one training set risks averaging over structurally different markets. This should be tested explicitly — compare model performance trained on full history vs. a shorter, more recent window (e.g. last 10 years) — rather than assumed. More data is not automatically better data here.

**Is 5–10 day prediction realistically possible?** For a stock as heavily covered as DBS, the honest expectation is a small edge at best — likely close to, and possibly indistinguishable from, a 50–55% baseline once regime variation and transaction costs are accounted for. The project's value in v1 is the rigor of the framework, not a guaranteed working signal.

**Is news sentiment likely to improve the model?** Plausible in principle, unproven in practice for this specific problem, and — critically — the free data sources available carry real point-in-time and black-box risk (Section 5). Deferred, not dismissed.

**Does DBS alone provide enough data?** Marginally. Daily data over decades sounds substantial, but overlapping 5/10-day labels mean the effective number of *independent* observations is far smaller — plausibly in the low hundreds per walk-forward fold, not thousands. This is the single biggest reason the modeling order in Section 10 starts simple: a model with many parameters (XGBoost) is easy to overfit on a sample this size unless heavily regularized and validated correctly.

**Overfitting risk.** Real, and compounded by testing 36–39 features against 2 targets across 3 models — some combination will look good by chance. A single good backtest is not evidence; only results that survive purged walk-forward validation across multiple distinct market regimes should be trusted.

**Mistaking correlation for predictive ability.** The core discipline against this: every claimed result must be judged against explicit baselines (Section 11), reported with calibration (not just accuracy), and broken out by regime — not presented as a single flattering headline number.

---

## 18. Recommendation

**Yes, proceed with this project as designed** — the spec is methodologically sound, appropriately skeptical about its own likely success, and correctly scoped for one developer. Two scope decisions are flagged for your explicit sign-off rather than assumed: (a) exclude the banking-sector peer group and news/sentiment from v1 (both deferred, Sections 5 and 7), and (b) accept that "V1 complete" may honestly mean "no model here beats a naive baseline" — that outcome should be reported plainly, not avoided by scope creep into more complex models or data sources.

**The first coding phase should be Phase 2: real price and index ingestion** — building `ingestion/prices.py` and `validation/checks.py` for real, to load and gap-check D05.SI and ^STI daily OHLCV into DuckDB. This is the smallest possible step that produces one real, validated, end-to-end data source before anything else (macro, fundamentals, features, models) is built on top of it.
