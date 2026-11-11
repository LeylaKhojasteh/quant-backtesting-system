# Strategy Comparison Package — Candle Direction on the Bundled Sample

A small, fixed, reproducible comparison case: one strategy, one symbol, one
timeframe and one complete tick session, with a reference trade table that can be
compared against an independent execution system.

Everything here is generated from the repository by:

```
python scripts/export_comparison_package.py
```

That command writes `docs/comparison/run_manifest.json` and
`docs/comparison/reference_trades.csv`. It changes no engine, data, API or
strategy code.

---

## 1. Strategy logic (exact)

The executable strategy is the built-in **`candle_direction`** (name
`Candle Direction`, version `1.0`). The user-interface fixture label is
`Candle Direction Test`; it refers to this same logic. There is no separate
"Test" implementation.

For every closed BID candle of the selected timeframe:

| Condition | Signal |
|---|---|
| `close > open` | `LONG` |
| `close < open` | `SHORT` |
| `close == open` | no signal |

- `signal_candle_start` is the candle's open time.
- `signal_available_time` is the candle's close time (start + timeframe).
- The strategy sees only BID OHLC candles; it never sees ticks and never places
  orders or sets SL/TP.

Source: `src/strategies/candle_direction.py`,
`src/strategies/base.py`, `src/run_backtest.py`.

## 2. Fixed input

| Item | Value |
|---|---|
| Data file | `data/sample/XAUUSD_SAMPLE_2024_12_ticks.csv` (bundled, committed) |
| Format | headerless `date,time,bid,ask,bid_volume,ask_volume`; timestamps are **UTC** |
| Ticks | 193,121 |
| Coverage | `2024-12-04T00:00:01.034Z` → `2024-12-04T15:59:59.975Z` |
| SHA-256 | `dc93d9703be5e37245875490a407882f16d24532027887062360780f5762ea71` |
| BID range | 2632.315 → 2656.998 |
| Observed spread | min 0.001 · mean 0.38872 · max 1.842 |

Use **this exact file**. Verify the hash before running; a different tick file
invalidates the comparison.

## 3. Fixed settings

| Setting | Value |
|---|---|
| Symbol | `XAUUSD` |
| Timeframe | `15min` (64 candles in the session) |
| Start (UTC) | `2024-12-04` |
| End (UTC) | `2024-12-04` |
| Stop distance | **2.00** price units |
| Take-profit distance | **4.00** price units |
| Spread | use the `bid`/`ask` columns as-is; **do not add an extra spread** |
| Volume | 1.0 (reference value; price-term PnL is volume-independent here) |
| Commission | 0.00 (not modelled by the engine; results are price terms) |

## 4. Execution contract (must match)

Full contract: `docs/EXECUTION_PARITY.md`. For this package the decisive rules
are:

- LONG enters at **ASK**, exits at **BID**; SHORT enters at **BID**, exits at **ASK**.
- A signal is eligible at the first tick with `timestamp >= signal_available_time`
  (inclusive).
- On each tick the open position is checked **before** signals; SL is checked
  before TP.
- Fills use the **observed** tick price, including a gap through a level.
- One position at a time; signals while a position is open are **dropped**, not queued.
- At the end of the data an open position is closed at the last tick's BID (LONG)
  or ASK (SHORT), reason `END_OF_DATA`.

## 5. Reference result

| | |
|---|---|
| Trades | **31** |
| Net PnL (price terms) | **−20.658** |
| Signals received / executed / ignored | **64 / 31 / 33** |
| Candles / ticks | 64 / 193,121 |

Signals are dropped while a position is open, so 33 of the 64 candle signals do
not become trades.

### Trades

Prices are shown to 3 decimals for readability; `reference_trades.csv` carries the
full precision. Times are UTC.

| # | dir | signal available | entry time | entry | exit time | exit | reason | pnl | spread in / out |
|---:|---|---|---|---:|---|---:|---|---:|---|
| 1 | SHORT | 00:15:00 | 00:15:00.779 | 2642.195 | 01:00:32.267 | 2638.152 | TP | +4.043 | 0.460 / 0.377 |
| 2 | SHORT | 01:15:00 | 01:15:00.054 | 2639.225 | 01:15:26.233 | 2641.295 | SL | −2.070 | 0.420 / 0.407 |
| 3 | LONG | 01:30:00 | 01:30:00.205 | 2640.005 | 02:02:26.645 | 2644.024 | TP | +4.019 | 0.380 / 0.321 |
| 4 | LONG | 02:15:00 | 02:15:00.037 | 2645.255 | 02:27:45.993 | 2643.235 | SL | −2.020 | 0.310 / 0.461 |
| 5 | SHORT | 02:30:00 | 02:30:00.003 | 2643.685 | 02:35:36.403 | 2645.745 | SL | −2.060 | 0.410 / 0.410 |
| 6 | SHORT | 02:45:00 | 02:45:08.000 | 2643.555 | 02:57:48.742 | 2645.566 | SL | −2.011 | 0.391 / 0.421 |
| 7 | LONG | 03:00:00 | 03:00:00.237 | 2646.186 | 03:22:05.214 | 2644.185 | SL | −2.001 | 0.438 / 0.390 |
| 8 | SHORT | 03:30:00 | 03:30:00.165 | 2643.145 | 03:36:35.860 | 2645.155 | SL | −2.010 | 0.457 / 0.450 |
| 9 | LONG | 03:45:00 | 03:45:01.151 | 2645.606 | 05:27:20.488 | 2649.625 | TP | +4.019 | 0.451 / 0.340 |
| 10 | LONG | 05:30:00 | 05:30:00.076 | 2649.805 | 05:41:44.177 | 2647.785 | SL | −2.020 | 0.370 / 0.477 |
| 11 | SHORT | 05:45:00 | 05:45:00.091 | 2648.235 | 05:53:06.742 | 2650.295 | SL | −2.060 | 0.310 / 0.297 |
| 12 | LONG | 06:00:00 | 06:00:00.095 | 2649.905 | 06:15:28.143 | 2647.895 | SL | −2.010 | 0.300 / 0.430 |
| 13 | LONG | 06:30:00 | 06:30:00.149 | 2649.865 | 07:01:15.123 | 2647.815 | SL | −2.050 | 0.430 / 0.400 |
| 14 | SHORT | 07:15:00 | 07:15:00.041 | 2646.585 | 07:42:09.800 | 2642.575 | TP | +4.010 | 0.441 / 0.330 |
| 15 | SHORT | 07:45:00 | 07:45:00.214 | 2640.945 | 08:35:26.650 | 2636.942 | TP | +4.003 | 0.357 / 0.357 |
| 16 | LONG | 08:45:00 | 08:45:00.104 | 2640.515 | 09:04:14.177 | 2638.515 | SL | −2.000 | 0.247 / 0.360 |
| 17 | LONG | 09:15:00 | 09:15:00.094 | 2642.375 | 09:18:35.887 | 2640.365 | SL | −2.010 | 0.350 / 0.320 |
| 18 | LONG | 09:30:00 | 09:30:00.126 | 2642.815 | 09:36:11.818 | 2640.685 | SL | −2.130 | 0.350 / 0.380 |
| 19 | SHORT | 09:45:00 | 09:45:00.654 | 2640.885 | 10:35:47.733 | 2642.905 | SL | −2.020 | 0.417 / 0.370 |
| 20 | SHORT | 10:45:00 | 10:45:00.322 | 2641.505 | 10:51:48.325 | 2643.525 | SL | −2.020 | 0.390 / 0.400 |
| 21 | LONG | 11:00:00 | 11:00:00.844 | 2644.652 | 12:20:34.222 | 2642.645 | SL | −2.007 | 0.387 / 0.380 |
| 22 | SHORT | 12:30:00 | 12:30:00.044 | 2643.075 | 12:50:55.963 | 2639.065 | TP | +4.010 | 0.400 / 0.341 |
| 23 | SHORT | 13:00:00 | 13:00:00.081 | 2636.885 | 13:00:16.423 | 2638.906 | SL | −2.021 | 0.267 / 0.411 |
| 24 | SHORT | 13:15:00 | 13:15:00.062 | 2634.435 | 13:15:53.106 | 2636.545 | SL | −2.110 | 0.701 / 0.340 |
| 25 | LONG | 13:30:00 | 13:30:00.005 | 2645.465 | 13:34:44.106 | 2643.465 | SL | −2.000 | 0.390 / 0.410 |
| 26 | SHORT | 13:45:00 | 13:45:00.437 | 2644.645 | 13:49:58.125 | 2646.665 | SL | −2.020 | 0.440 / 0.417 |
| 27 | LONG | 14:00:00 | 14:00:00.078 | 2648.705 | 14:18:52.865 | 2646.605 | SL | −2.100 | 0.360 / 0.327 |
| 28 | LONG | 14:30:00 | 14:30:00.000 | 2652.826 | 14:36:57.755 | 2650.768 | SL | −2.058 | 0.401 / 0.354 |
| 29 | SHORT | 14:45:00 | 14:45:00.002 | 2650.988 | 15:00:04.654 | 2653.065 | SL | −2.077 | 0.607 / 0.687 |
| 30 | LONG | 15:15:00 | 15:15:00.017 | 2650.972 | 15:25:49.726 | 2655.135 | TP | +4.163 | 0.437 / 0.411 |
| 31 | LONG | 15:30:00 | 15:30:00.124 | 2654.085 | 15:48:03.556 | 2652.045 | SL | −2.040 | 0.237 / 0.380 |

All times are `2024-12-04`, UTC.

## 6. Recording table (for the independent reference run)

Copy `docs/comparison/reference_trades.csv`, or fill a table with the same columns:

| # | dir | signal available | entry time | entry | exit time | exit | reason | pnl | spread in / out |
|---:|---|---|---|---:|---|---:|---|---:|---|
|  |  |  |  |  |  |  |  |  |  |
|  |  |  |  |  |  |  |  |  |  |
|  |  |  |  |  |  |  |  |  |  |
|  |  |  |  |  |  |  |  |  |  |
|  |  |  |  |  |  |  |  |  |  |

Also record the totals you observe: trade count, net price PnL, and
received / executed / ignored signal counts.

## 7. How to run the comparison

1. Verify the input:
   `sha256sum data/sample/XAUUSD_SAMPLE_2024_12_ticks.csv` →
   `dc93d9703be5e37245875490a407882f16d24532027887062360780f5762ea71`.
2. Configure the reference system with **exactly** the file, symbol, timeframe,
   range, stop 2.00, take 4.00, observed bid/ask (no extra spread), zero
   commission.
3. Apply the execution rules in section 4 (single position, availability
   inclusive, exit before signal, observed-price fills).
4. Fill the recording table (or the CSV) and return it with any deviation and the
   tick index where it occurs.

## 8. Match criteria

A match requires identical:

- trade count and ordering by entry time;
- direction, entry time, entry price, exit time, exit price, exit reason;
- price PnL (sign and value), and entry/exit spread.

Formatting differences (e.g. 3 vs 5 decimals) are not discrepancies. A different
tick file, timeframe, SL/TP, or extra spread is a different scenario, not a
mismatch.

## 9. Known limitations

- **Naming:** the UI fixture label `Candle Direction Test` maps to the built-in
  `Candle Direction` (`candle_direction`, v1.0); no separate implementation exists.
- **Volume and commission** are not part of the engine; the reference table is
  price terms. A volume/commission column can be added for an account-currency
  comparison, but it will not change the engine's trade list.
- **Spread** is the observed `ask − bid`; the engine never adds a second spread.
- **One position at a time**, no order types, no partial closes, no trailing;
  signals while a position is open are dropped.
- The session is a single 16-hour window; it does not include overnight or
  weekend behaviour.
