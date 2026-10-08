# Execution Model (verified)

The verified engine is `src/backtest.py`. It executes normalized signals tick-by-tick
on real BID/ASK ticks. Strategies only emit signals; the engine only executes them.

## Price authority

- BID and ASK are separate authoritative prices (from canonical Parquet ticks).
- Mid is never used.
- No synthetic ticks; natural market gaps remain natural.

## Entry / exit prices

- LONG entry: **ASK**
- LONG exit: **BID**
- SHORT entry: **BID**
- SHORT exit: **ASK**

Spread is therefore already reflected in the actual fill prices. Spread is not
subtracted from PnL a second time.

## Signals

- A signal becomes available only after its source candle has fully closed
  (`signal_available_time` = candle start + timeframe).
- The entry occurs at the **first eligible tick** at/after `signal_available_time`.
- Signals arriving while a position is open are ignored (not queued).

## SL / TP

- Evaluated tick-by-tick at executable prices:
  - LONG: SL when BID ≤ stop, TP when BID ≥ take (exit at BID).
  - SHORT: SL when ASK ≥ stop, TP when ASK ≤ take (exit at ASK).
- Gap-through: the first tick beyond the level fills at that observed tick price
  (no assumed exact-level fill).

## Ordering

Per tick: (1) evaluate an open position for SL/TP; (2) process eligible signals.
One position at a time; exit-before-new-entry.

## END_OF_DATA

If a position is still open at the last tick, it is closed at that tick's
executable price (LONG → BID, SHORT → ASK) with reason `END_OF_DATA`.

## Terms

- Results are in **price terms** unless the optional Account Model is enabled.
- MAE/MFE are not provided by the engine (shown unavailable).
