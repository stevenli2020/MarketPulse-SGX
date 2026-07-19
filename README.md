# MarketPulse SGX

Transparent, explainable historical market analysis system.
First supported stock: DBS Group Holdings (SGX: D05.SI).

This is a research/decision-support tool. It does **not** execute trades and
does **not** connect to any brokerage.

## Status

This project is in early skeleton form. No data collection or modeling code
has been implemented yet. See `PROJECT_STATUS.md` for the current state and
`PROJECT_SPEC.md` for the full design (architecture, database schema,
features, prediction targets, backtesting methodology, and leakage
prevention).

## Setup

```bash
python -m venv venv
source venv/bin/activate   # on Windows: venv\Scripts\activate
pip install -r requirements.txt
```

Run scripts as modules from the project root (not directly), so Python
can resolve the `db`, `ingestion`, etc. packages correctly:

```bash
python -m scripts.run_ingestion
```

## Project layout

```
config.py                        - shared settings (tickers, paths, date ranges)
db/schema.sql                    - DuckDB table definitions
db/connection.py                 - opens the DuckDB file and applies schema.sql
ingestion/prices.py              - (stub) price/volume data collection
ingestion/macro.py               - (stub) interest-rate/macro data collection
ingestion/fundamentals.py        - (stub) DBS fundamentals data collection
validation/checks.py             - (stub) data quality and gap checks
features/feature_engineering.py  - (stub) point-in-time-safe feature computation
labeling/labels.py               - (stub) forward-looking label computation
tests/test_leakage.py            - (stub) leakage-prevention tests
scripts/run_ingestion.py         - (stub) CLI entry point for ingestion
```

## Development rules (see PROJECT_SPEC.md for full detail)

- No deep learning, reinforcement learning, LSTM, or Transformer models.
- Every prediction must be explainable, not a black box.
- Feature code and label code are kept structurally separate.
- Every data table distinguishes "observation date" from "availability date"
  to prevent look-ahead bias.
