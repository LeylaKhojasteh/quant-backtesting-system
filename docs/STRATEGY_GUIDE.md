# Strategy Author Guide

Strategies generate **signals only**. They never execute trades, manage positions,
or touch market data.

## Contract

A strategy is a Python file exposing:

- `SIGNAL_COLUMNS = ["signal_candle_start", "signal_available_time", "direction"]`
- `generate_signals(candles, ...) -> pandas.DataFrame` with exactly those columns.

Optional metadata/parameters used by import and the UI:

- `NAME`, `VERSION`, `DESCRIPTION` (strings)
- `PARAMETERS` (dict of `name -> default`) — defaults declare the typed parameters
  the UI renders. Supported types: `float`, `int`, `string`, `bool`.

Two accepted call conventions (the loader inspects the signature):

- `generate_signals(candles)` — no parameters.
- `generate_signals(candles, parameters)` — receives the effective parameter dict
  (declared defaults merged with user overrides).
- `generate_signals(candles, timeframe=...)` — receives the market timeframe that
  produced `candles` (e.g. `"15min"`, `"1h"`); combinable with `parameters`.
- The builtin `candle_direction` uses `generate_signals(candles, timeframe=None)`
  where `None` infers one bar from the candle index; runs always pass the market
  timeframe explicitly.

## Candles

`candles` is a BID OHLC frame indexed by candle start (UTC), columns
`open, high, low, close`, left-labeled / left-closed, `[start, start + T)`.
The strategy must treat a signal as available only **after** the candle closes:
set `signal_available_time = candle_start + timeframe`.

## Signals

- `direction` ∈ `{"LONG", "SHORT"}`.
- `signal_candle_start` — the candle's start timestamp.
- `signal_available_time` — when the signal becomes tradable (≥ candle start).
- Return an empty frame (with `SIGNAL_COLUMNS`) when there are no signals.

## Boundaries

- Strategy owns: signal logic + declared parameters.
- Engine owns: entry/exit, SL/TP, position management, PnL (see EXECUTION_MODEL.md).
- Do not perform per-trade sizing in a strategy — the Account Model uses run-level
  lot sizing.

## Example (module style)

```python
import pandas as pd

NAME = "Threshold Direction"
VERSION = "1.0"
DESCRIPTION = "Signal when close moves at least `threshold`."
PARAMETERS = {"threshold": 0.0, "side": "both"}
SIGNAL_COLUMNS = ["signal_candle_start", "signal_available_time", "direction"]

def generate_signals(candles, parameters):
    threshold = float(parameters["threshold"])
    side = str(parameters["side"])
    bar = pd.Timedelta("15min")
    rows = []
    previous = None
    for start, row in candles.iterrows():
        if previous is not None:
            move = row["close"] - previous
            if abs(move) >= threshold and side in ("both", "long" if move > 0 else "short"):
                rows.append({
                    "signal_candle_start": start,
                    "signal_available_time": start + bar,
                    "direction": "LONG" if move > 0 else "SHORT",
                })
        previous = row["close"]
    return pd.DataFrame(rows, columns=SIGNAL_COLUMNS)
```

Imported strategies are validated: the loader rejects files that cannot be loaded,
that omit `generate_signals` / `run_strategy`, or whose signals violate the normalized contract.

## Strategy-owned execution (v1, single runs only)

A file may own execution instead of emitting signals by exposing
`run_strategy(data, ctx)` with a declared `PARAMETERS` mapping:

```python
NAME = "EMA 9/20 Cross"
VERSION = "1.0"
PARAMETERS = {"warmup_bars": 200}  # strategy-owned inputs only

def run_strategy(data, ctx):
    # data.candles: OHLC frame (UTC index), already ranged + resampled.
    # ctx.parameters: effective strategy inputs from the UI.
    # ctx.instrument / ctx.account: contract_size, lots, balance (or None).
    # ctx.run_engine(signals): optional tick engine for this run's ticks.
    trades, equity, summary = run_backtest(df=..., lots=..., ...)
    return trades  # or (trades, equity, summary)
```

Contract for the returned trade table (6 required columns):
`direction` (`BUY`/`SELL`/`LONG`/`SHORT`), `entry_time`, `exit_time`,
`entry_price`, `exit_price`, `exit_reason`. Optional: `stop_loss`,
`take_profit`, `gross_pnl` (money) or `pnl_price` (price terms).
`pnl_price` is derived from `gross` money only; commission flows to the
account block, never into price-term fields.

Ownership: Quant owns data/range/timeframe/UI parameters, normalization,
metrics and reports. The file owns entry/exit only. Optimization and
walk-forward reject `external` strategies in v1; the UI labels such runs
`strategy-owned` and comparisons across execution models are flagged.
