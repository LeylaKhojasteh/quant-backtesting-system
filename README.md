# Quant Backtest Lab

Local tick-level backtesting workstation. Import a strategy, run it against
real BID/ASK market data, inspect every trade, archive the result.

**Version: 1.0.3**

---

## 1. What you need

- **Windows 10+**, **Python 3.11+**, **Node.js 18+** (`pnpm` preferred, `npm` works).
- **Market data (your own).** The `data/` folder is NOT in git
  (too large, machine-specific). Every collaborator puts their own CSVs there —
  see section 4.

## 2. Setup

```
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
cd Quant-Backtest-Lab && pnpm install        # or: npm install
```

## 3. Start

Double-click **`START_QUANT_LAB.bat`**, or:

```
.venv\Scripts\python.exe scripts\start_quant_lab.py
```

- App: **http://127.0.0.1:5173/**
- Engine (API only, no page): **http://127.0.0.1:8765**
- Topbar must say `Python Bridge: Connected · v1.0.3`.

## 4. Put your data in `data/`

One CSV **per symbol, provider, and month**:

```
data/
  EURUSD_DUKAS_2025_01_ticks.csv
  EURUSD_DUKAS_2025_02_ticks.csv
  BTCUSD_BINANCE_2025_01_ticks.csv
```

Pattern: `<SYMBOL>_<PROVIDER>_YYYY_MM_ticks.csv`

Two CSV shapes are accepted:

| | Dukascopy shape | Generic shape (crypto, other exports) |
|---|---|---|
| Header | none (auto-detected) | `timestamp,bid,ask` (+ optional `bid_volume,ask_volume`) |
| Time | `YYYY.MM.DD HH:MM:SS.mmm` = UTC | any timestamp parsed as UTC |
| Note | — | single-price exports: copy the price into BOTH `bid` and `ask` (the engine runs on BID/ASK and never invents a spread) |

Rules: never edit files in `data/` afterwards (fingerprinted as immutable).
Gaps from weekends/holidays are normal, not corruption. Files named
`..._ALL_ticks.csv` are treated as read-only reference, never used for runs.

## 5. Prepare the cache (once per new data)

```
.venv\Scripts\python.exe -m src.data_engine build --all
.venv\Scripts\python.exe -m src.data_engine build-candles
.venv\Scripts\python.exe -m src.data_engine verify
.venv\Scripts\python.exe -m src.data_engine inspect
```

What this does: reads your CSVs, builds a derived Parquet cache under
`cache/` (git-ignored, rebuildable), verifies it bit-for-bit, and registers
one logical dataset per symbol (e.g. `eurusd_dukas_ticks`,
`btcusd_binance_ticks`). The dataset then appears in the app's dataset list.

## 6. Run a backtest

1. Sidebar → **Run Setup**: strategy, dataset, timeframe, start/end dates
   (UTC, inside the dataset's coverage).
2. **Strategy & Trade Settings**: strategy inputs, Stop/Take, optional
   Account Model.
3. Click **Run Backtest**. A live bar shows
   `● Backtest running · <stage> · mm:ss` with the worker heartbeat.
   The old result stays visible until the new one completes, then loads
   automatically. Reloading mid-run reconnects to the same job.
4. Optional: **Stop Backtest** discards the run (archives and library stay
   untouched, previous result stays on screen).
5. Inspect: price chart, `Cumulative Trade PnL` (or `Realized Account
   Balance` with the Account Model), Trade Log, Trade Inspector, Data
   Explorer, Analytics, Full Report, Run Library.

Only one real run at a time — a second start is rejected until the active
job finishes or is stopped.

## 7. Account Model (optional)

Price-term results work for every symbol with zero config. For
account-currency PnL/balance/return, the symbol needs economics (quote
currency, contract size, tick size). `XAUUSD/EURUSD/GBPUSD/USDJPY` are
built in; anything else (e.g. `BTCUSD → USD, 1.0, 0.01`) is one entry in
`INSTRUMENT_CATALOG` (`src/accounting.py`) or an explicit `instrument` in
the run request. v1 requires quote currency == account currency.

## 8. Where things live

- `data/` — your raw CSVs (git-ignored, immutable).
- `cache/` — derived Parquet (git-ignored, rebuildable).
- `runs/<run_id>/` — `config.json`, `result.json`, `trades.csv`,
  `equity.csv`, `report.html`, `strategy.py` (local, git-ignored).
- `studies/`, `walkforwards/`, `montecarlo/` — research archives.
- `Quant-Backtest-Lab/public/` — published results + run manifest.

## 9. Commands

```
# tests
.venv\Scripts\python.exe -m pytest tests
cd Quant-Backtest-Lab && node scripts/test.mjs

# production build
cd Quant-Backtest-Lab && node scripts/run-framework.mjs build
```

## 10. Docs

`ARCHITECTURE.md` (source of truth) · `docs/DATA_ENGINE.md` ·
`docs/EXECUTION_MODEL.md` · `docs/STRATEGY_GUIDE.md` ·
`docs/RUN_ARCHIVE.md` · `docs/RELEASE_v1.md`

Known v1 limits: no leverage/margin/swap/slippage beyond real BID/ASK
prices; no Sharpe/annualized metrics; full-year M15 runs take ~2 minutes;
single local user, no auth/database/cloud.
