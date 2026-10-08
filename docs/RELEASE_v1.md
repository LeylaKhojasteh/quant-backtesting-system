# Quant Backtest Lab — Release v1.0

Version: **1.0.0**

## Status

- **Engine** (`src/backtest.py`): verified, frozen. Tick-by-tick BID/ASK execution.
- **Data Engine v1**: COMPLETE — immutable raw CSVs → verified Parquet tick store
  → verified candle cache; logical dataset `xauusd_dukas_ticks` (`XAUUSD · Dukascopy`);
  2025 complete (71,413,977 ticks), 2026-01 partial; `ALL` aggregate excluded.
- **Real backtesting**: one continuous full-year 2025 run verified and deterministic.
- **Account Model**: optional; account-currency PnL, commission, realized balance,
  return, realized balance drawdown. Accounting is post-execution.
- **Reports**: standalone HTML, truthful (unavailable metrics stay unavailable).
- **UI**: responsive workstation; Run Setup, dashboard, Trade Inspector,
  Data Explorer, Research Workspace (Runs/Strategies/Compare/Optimize/Walk-Forward/Monte Carlo).
- **Test suites**: Python + frontend + browser E2E green.

## Verified reference results

**Full-Year Reference v1** (compact fixture: `tests/fixtures/full_year_reference_v1.json`)

- dataset `xauusd_dukas_ticks` · XAUUSD · 15min · 2025-01-01 → 2025-12-31
- candle_direction · stop 5 / take 10 · price terms (no account model)
- ticks 71,413,977 · candles 23,638 · signals 23,569 · trades 5,749 (2,987 long / 2,762 short)
- total price PnL −3,711.54 · Profit Factor 0.8224 · Max Drawdown 3,777.98
- digest `sha256:9705b059c5e23e8401f62519bdd39bb513ccff57a74098a86674032a97c5e7ed`
- runtime ≈ 118 s, peak RSS ≈ 8 GB on the documented reference machine (class: GOOD)

**Golden Reference** (fast regression): 2025-01-31 M15 → 322,548 ticks · 88 candles · 7 trades · PnL −15.82.

## Known limitations (v1)

- Account Model: run-level lot sizing only; no leverage, margin-call, risk-% sizing,
  swap, or slippage model beyond actual BID/ASK tick prices.
- Sharpe/Sortino/Calmar/annualized return and dataset-wide spread statistics are
  not calculated (unavailable).
- Full-year M15 runtime is engine-bound (~2 minutes); engine performance work is a
  separate future milestone.
- Local single-user application: no auth, database, cloud execution, or live trading.

## Verification summary

- Raw data immutable: 14/14 source SHA-256 unchanged.
- Exact CSV↔Parquet tick parity and 78/78 candle parity.
- Deterministic full-year re-run digest match.
- `src/backtest.py`, `src/strategies/candle_direction.py`, `notebooks/*`, `data/*` unchanged.
