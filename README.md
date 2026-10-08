# Quant Backtest Lab

A local, research-oriented backtesting workstation for tick-level BID/ASK strategy
research: import strategies, run reproducible backtests on real market data,
inspect trades, and archive/share results as standalone reports.

**Version: 1.0.0**

## What it does

- Real tick-by-tick BID/ASK backtesting (verified engine; `src/backtest.py`).
- Immutable raw-data pipeline → verified Parquet tick store → verified candle cache.
- Logical datasets (e.g. `XAUUSD · Dukascopy`) with controlled timeframes M1/M5/M15/M30/H1/H4.
- Strategy import, generic typed parameters, SL/TP execution settings.
- Optional Account Model (account-currency PnL, commission, realized balance, return, drawdown).
- Run archive + standalone HTML reports + Run Library + Trade Inspector/Debugger.
- Data Explorer, Run Comparison, Optimization, Walk-Forward, Monte Carlo.
- Responsive local workstation UI + one-command launcher.

## Requirements

- Windows 10+ (launcher is `START_QUANT_LAB.bat`).
- Python 3.11+ with the project virtualenv (`.venv`).
- Node.js 18+ and a package manager (`pnpm` preferred; `npm` works).
- Raw validated market data in `data/` (immutable CSV sources).

## Initial setup

1. Create/activate the Python virtualenv and install dependencies:
   - `python -m venv .venv`
   - `.venv\Scripts\python.exe -m pip install -r requirements.txt`
2. Install frontend dependencies (inside `Quant-Backtest-Lab/`):
   - `pnpm install` (or `npm install`)
3. Place validated raw market data under `data/` (never edit it).

## Raw market data

- `data/*.csv` is the immutable RAW SOURCE OF TRUTH. Never edit, sort, merge, or
  regenerate it. `XAUUSD_DUKAS_ALL_ticks.csv` is an aggregate reference only and
  is excluded from the logical dataset.
- The authoritative 2025 family is 12 monthly partitions: **71,413,977 ticks**.
- Logical dataset: `xauusd_dukas_ticks` (XAUUSD · Dukascopy), 2025 complete,
  2026-01 partial.

## Prepare the Data Engine cache (explicit)

Conversion is explicit — it never runs automatically on app startup.

```
python -m src.data_engine inventory          # inspect raw sources (read-only)
python -m src.data_engine build --all        # build/update tick Parquet partitions
python -m src.data_engine build-candles      # build/update candle partitions (all 6 timeframes)
python -m src.data_engine verify             # exact CSV <-> Parquet tick parity
python -m src.data_engine verify-candles     # exact candle parity (78/78)
python -m src.data_engine inspect            # logical dataset + prepared state
```
(Equivalent wrapper: `python scripts/build_data_cache.py <command>`.)

Derived data lives under `cache/market_data/` (git-ignored, rebuildable). The
normal Run workflow never rebuilds caches; if data is not prepared it reports a
clear "data not prepared" state.

## Start the app

- Double-click `START_QUANT_LAB.bat`, or run `.venv\Scripts\python.exe scripts\start_quant_lab.py`.
- It checks prerequisites, starts the Python Bridge, waits for health, starts the
  frontend, opens the browser, and cleans up child processes on exit.
- Healthy topbar: `Python Bridge: Connected · v1.0.0`.

## Run a real backtest

1. Run Setup → Dataset `XAUUSD · Dukascopy` (Symbol derives to `XAUUSD`).
2. Choose a timeframe (M1/M5/M15/M30/H1/H4) and a real date range inside coverage.
3. **Strategy & Trade Settings**: strategy inputs (dynamic), Stop/Take, optional Account & Costs.
4. Run Backtest → result loads in the dashboard (Price Chart, metrics, Trade Log).
5. Open the run from the Run Library; inspect trades in the Trade Inspector.

## Research Workspace

Run Library · Strategies · Compare · Optimize · Walk-Forward · Monte Carlo —
all inside one workspace, using the current logical-dataset / run contracts.

## Where archives live

- Runs: `runs/<run_id>/` — `config.json`, `result.json`, `trades.csv`,
  `equity.csv`, `report.html`, `strategy.py`. Raw ticks are NOT copied into runs;
  `config.json` records data provenance.
- Studies: `studies/`, `walkforwards/`, `montecarlo/`.
- Published artifacts + `adapters-run-manifest.json`: `Quant-Backtest-Lab/public/`.

## Production / local commands

```
# frontend production build
cd Quant-Backtest-Lab && node scripts/run-framework.mjs build

# tests
.venv\Scripts\python.exe -m pytest tests
cd Quant-Backtest-Lab && node scripts/test.mjs
```

## Documentation

- `ARCHITECTURE.md` — system architecture (source of truth).
- `docs/DATA_ENGINE.md` — raw → Parquet ticks → candle cache, lineage, range semantics.
- `docs/EXECUTION_MODEL.md` — verified BID/ASK execution behavior.
- `docs/STRATEGY_GUIDE.md` — strategy author guide (real API).
- `docs/RUN_ARCHIVE.md` — run archive + study archives.
- `docs/RELEASE_v1.md` — v1.0 release readiness and known limitations.

## Known v1 limitations

- Account Model is run-level lot sizing; no leverage, margin-call, risk-% sizing,
  swap, or slippage model beyond actual BID/ASK tick prices.
- Sharpe/Sortino/Calmar/annualized return and dataset-wide spread statistics are
  not calculated (shown as unavailable).
- Full-year M15 runs take ~2 minutes on the documented reference machine (engine
  is the dominant cost); engine performance work is a separate future milestone.
- Local single-user application (no auth, database, or cloud backend).
