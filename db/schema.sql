-- MarketPulse SGX database schema
-- See PROJECT_SPEC.md Section 4 for the full rationale.
--
-- Design principle: every raw data table distinguishes the date a data
-- point *describes* from the date it was *actually knowable*. This is the
-- structural defense against look-ahead bias (PROJECT_SPEC.md Section 8).

-- ============================================================
-- Dimension tables
-- ============================================================

CREATE TABLE IF NOT EXISTS dim_securities (
    security_id INTEGER PRIMARY KEY,
    ticker      VARCHAR NOT NULL UNIQUE,   -- e.g. 'D05.SI'
    name        VARCHAR NOT NULL,
    exchange    VARCHAR NOT NULL,
    sector      VARCHAR,
    listed_date DATE
);

CREATE TABLE IF NOT EXISTS dim_indices (
    index_id INTEGER PRIMARY KEY,
    ticker   VARCHAR NOT NULL UNIQUE,      -- e.g. '^STI'
    name     VARCHAR NOT NULL
);

-- ============================================================
-- Price ingestion audit trail (Phase 2)
-- ============================================================
-- Data flow: price_fetches -> raw_price_rows -> validation -> prices_daily / index_daily
-- See PROJECT_SPEC.md and PROJECT_STATUS.md Phase 2 notes for the full
-- rationale behind this two-table fetch/raw-row split.

CREATE TABLE IF NOT EXISTS price_fetches (
    fetch_id                BIGINT PRIMARY KEY,   -- surrogate key, assigned by ingestion code
    ticker                   VARCHAR NOT NULL,
    entity_type              VARCHAR NOT NULL,     -- 'security' | 'index'
    source                   VARCHAR NOT NULL,     -- 'yfinance'
    source_library_version   VARCHAR,
    requested_start_date     DATE,
    requested_end_date       DATE,
    requested_at             TIMESTAMP NOT NULL,
    fetched_at               TIMESTAMP,
    status                   VARCHAR NOT NULL,     -- 'success' | 'failed'
    row_count                INTEGER,              -- rows actually received (0 if failed)
    error_message             VARCHAR              -- NULL on success
);

CREATE TABLE IF NOT EXISTS raw_price_rows (
    fetch_id    BIGINT NOT NULL,          -- references price_fetches.fetch_id
    ticker      VARCHAR NOT NULL,
    trade_date  DATE NOT NULL,
    open        DOUBLE,
    high        DOUBLE,
    low         DOUBLE,
    close       DOUBLE,                   -- actual traded close, NOT dividend-adjusted
    adj_close   DOUBLE,                   -- dividend/split-adjusted, total-return series
    volume      BIGINT,
    PRIMARY KEY (fetch_id, trade_date)
    -- Deliberately no uniqueness constraint across fetch_id: if the same
    -- date is fetched again in a later run, both raw observations are
    -- preserved here. Only prices_daily/index_daily are deduplicated.
);

CREATE TABLE IF NOT EXISTS data_quality_warnings (
    warning_id    BIGINT PRIMARY KEY,     -- surrogate key, assigned by ingestion code
    warning_type  VARCHAR NOT NULL,       -- e.g. 'cross_instrument_date_anomaly'
    ticker        VARCHAR,
    trade_date    DATE,
    detail        VARCHAR,
    detected_at   TIMESTAMP NOT NULL,
    reviewed      BOOLEAN DEFAULT FALSE
);

-- ============================================================
-- Normalized market-price tables (Phase 2)
-- ============================================================
-- Renamed from raw_prices_daily / raw_index_daily (Phase 0/1 naming was
-- misleading: these tables enforce one row per trading day via their
-- primary key, which makes them the NORMALIZED layer, not the raw one.
-- The genuinely raw, unprocessed layer is raw_price_rows above.
--
-- availability_date: for daily price data, a trade_date's data becomes
-- knowable only after that session's close, so availability_date always
-- equals trade_date. Stored as an explicit column (rather than left
-- implicit) so that feature/label code can apply the same
-- "WHERE availability_date <= cutoff" filter uniformly across every
-- table in this schema, with no special-casing for prices.
--
-- POINT-IN-TIME CONVENTION (see PROJECT_SPEC.md / PROJECT_STATUS.md):
-- Features may use price data through the close of trading date T.
-- Prediction targets must begin from the next trading date after T.
-- No feature or target may use information from T+1 or later when
-- making a prediction as of T.

CREATE TABLE IF NOT EXISTS prices_daily (
    security_id       INTEGER NOT NULL,
    trade_date        DATE NOT NULL,
    availability_date DATE NOT NULL,      -- = trade_date, see convention note above
    open              DOUBLE,
    high              DOUBLE,
    low               DOUBLE,
    close             DOUBLE,
    adj_close         DOUBLE,
    volume            BIGINT,
    source            VARCHAR NOT NULL,
    ingested_at       TIMESTAMP NOT NULL,
    PRIMARY KEY (security_id, trade_date)
);

CREATE TABLE IF NOT EXISTS index_daily (
    index_id          INTEGER NOT NULL,
    trade_date        DATE NOT NULL,
    availability_date DATE NOT NULL,      -- = trade_date, see convention note above
    open              DOUBLE,
    high              DOUBLE,
    low               DOUBLE,
    close             DOUBLE,
    volume            BIGINT,
    source            VARCHAR NOT NULL,
    ingested_at       TIMESTAMP NOT NULL,
    PRIMARY KEY (index_id, trade_date)
);

CREATE TABLE IF NOT EXISTS raw_macro_series (
    series_id   VARCHAR NOT NULL,          -- e.g. 'SORA', 'US_FED_FUNDS_RATE'
    obs_date    DATE NOT NULL,             -- period the value describes
    value       DOUBLE,
    as_of_date  DATE NOT NULL,             -- date the value was actually knowable
    source      VARCHAR NOT NULL,
    ingested_at TIMESTAMP NOT NULL,
    PRIMARY KEY (series_id, obs_date, as_of_date)
);

CREATE TABLE IF NOT EXISTS raw_fundamentals (
    security_id        INTEGER NOT NULL,
    period_end_date     DATE NOT NULL,     -- period the figure describes
    report_release_date DATE NOT NULL,     -- date it was actually published
    metric_name         VARCHAR NOT NULL,  -- e.g. 'EPS', 'ROE', 'DIVIDEND_YIELD'
    value                DOUBLE,
    source               VARCHAR NOT NULL,
    ingested_at          TIMESTAMP NOT NULL,
    PRIMARY KEY (security_id, period_end_date, metric_name)
);

-- ============================================================
-- Derived / working tables
-- ============================================================

CREATE TABLE IF NOT EXISTS feature_store (
    security_id     INTEGER NOT NULL,
    feature_date    DATE NOT NULL,         -- observation date the feature is "as of"
    feature_name    VARCHAR NOT NULL,
    feature_value   DOUBLE,
    feature_version VARCHAR NOT NULL,
    computed_at     TIMESTAMP NOT NULL,
    PRIMARY KEY (security_id, feature_date, feature_name, feature_version)
);

CREATE TABLE IF NOT EXISTS labels (
    security_id          INTEGER NOT NULL,
    observation_date      DATE NOT NULL,
    horizon_days           INTEGER NOT NULL,   -- 5 or 10
    forward_return          DOUBLE,
    direction_label          INTEGER,          -- 1 = up, 0 = down
    label_computable_date    DATE NOT NULL,     -- observation_date + horizon trading days
    PRIMARY KEY (security_id, observation_date, horizon_days)
);

CREATE TABLE IF NOT EXISTS situation_matches (
    security_id        INTEGER NOT NULL,
    observation_date    DATE NOT NULL,
    analog_date          DATE NOT NULL,
    similarity_score      DOUBLE,
    analog_fwd_return_5d   DOUBLE,
    analog_fwd_return_10d  DOUBLE,
    PRIMARY KEY (security_id, observation_date, analog_date)
);

-- ============================================================
-- Modeling tables (not populated until Build Order steps 6-7)
-- ============================================================

CREATE TABLE IF NOT EXISTS model_runs (
    run_id          VARCHAR PRIMARY KEY,
    model_name      VARCHAR NOT NULL,
    train_start     DATE,
    train_end       DATE,
    test_start      DATE,
    test_end        DATE,
    hyperparams_json VARCHAR,
    feature_version  VARCHAR,
    created_at       TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS predictions (
    run_id            VARCHAR NOT NULL,
    security_id        INTEGER NOT NULL,
    observation_date    DATE NOT NULL,
    horizon_days          INTEGER NOT NULL,
    predicted_prob_up      DOUBLE,
    predicted_direction     INTEGER,
    actual_direction         INTEGER,
    created_at                TIMESTAMP NOT NULL,
    PRIMARY KEY (run_id, security_id, observation_date, horizon_days)
);

CREATE TABLE IF NOT EXISTS backtest_results (
    run_id        VARCHAR NOT NULL,
    segment_label VARCHAR NOT NULL,
    metric_name   VARCHAR NOT NULL,
    metric_value  DOUBLE,
    PRIMARY KEY (run_id, segment_label, metric_name)
);

-- ============================================================
-- Audit table
-- ============================================================

CREATE TABLE IF NOT EXISTS data_availability_log (
    table_name      VARCHAR NOT NULL,
    entity_type     VARCHAR NOT NULL,      -- 'security' | 'index'
    entity_id       INTEGER NOT NULL,      -- security_id or index_id, per entity_type
    coverage_start  DATE,
    coverage_end    DATE,
    last_updated    TIMESTAMP NOT NULL,
    row_count       BIGINT,
    PRIMARY KEY (table_name, entity_type, entity_id)
);
