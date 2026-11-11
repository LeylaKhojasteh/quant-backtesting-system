# Candle Execution Model

`src/candle_engine.py` executes normalized signals on OHLC bars for markets that
only publish candles (e.g. crypto 1m klines). It is a **deliberate
approximation**, not a replication of exchange matching.

The dataset decides the model: `data_type: ticks` uses `src/backtest.py`
(verified against MetaTrader), `data_type: candles` uses this model. Never the
symbol name.

## Rules

| Topic | Rule |
|---|---|
| Entry | Signal on a closed bar; fill at the **next bar's open** |
| Spread | `spread_bps` input = FULL spread; `ask = mid*(1+h)`, `bid = mid*(1-h)`, `h = bps/20000` |
| Cost | A round trip costs exactly one full spread (buy at ask, sell at bid) |
| Levels | Fixed for the whole bar; a level created by trailing/break-even becomes active on the **next** bar |
| Gap through level | Fill at the bar open, not at the level |
| Both levels touched | Stop is assumed and the trade is flagged `ambiguous_exit` |
| Time exit | First bar open at/after `entry_time + max_holding_seconds` |
| Excursions | `mae` / `mfe` from bar extremes on the exit side (approximate) |
| End of data | Last close with `END_OF_DATA` |

Signals that become available while a trade is open are ignored, not queued
(single position, same as the tick engine).

## Why levels are fixed per bar

Inside one OHLC bar the order of events is unknown. Building a trailing stop
from that bar's high and then proving a hit with that bar's low would invent an
order that cannot be verified. Updates therefore apply from the next bar.

## What must be reported

- `execution_model: candle`
- `tick_count: null` (no ticks exist)
- the assumed `spread_bps`
- `ambiguous_exits` (trades whose stop/target order is unknown)
- one prominent caveat: execution is approximate; confirm on tick data

Runs of different models must not be ranked against each other.

## Known limitations

- No funding, maker/taker fees, liquidation, minimum notional, partial fills,
  latency or order rejections.
- A touched level is treated as filled; queue position is unknown.
- A missing-data gap is not the same as a real price jump: the position's fate
  inside the gap is unknown.
- Spot shorting and perpetual funding are not modelled; say what the dataset is.

## Validation against the tick engine (XAUUSD, real ticks)

Same signals, same stop/target, spread taken from the tick data.

| Timeframe | Window | Trades (tick / candle) | Matched | Direction mismatch | Net PnL | Mean \|PnL diff\| |
|---|---|---|---|---|---|---|
| 15min | 2 days | 15 / 15 | 15 | 0 | 9.91 / 6.76 | 0.22 |
| 5min | 2 days | 18 / 18 | 18 | 0 | -35.18 / -38.14 | 0.19 |
| 15min | 1 week | 41 / 39 | 36 | 0 | -88.07 / -82.73 | 0.30 |
| 1min | 2 days | 15 / 11 | 9 | 0 | -4.08 / 19.60 | 0.27 |

Per-trade error stays near 0.2–0.3 price units. The larger divergences are
cascades: a knife-edge fill (in the 1min case a 3-cent TP miss caused by a
3-cent entry difference) delays one exit, and with a single position the next
signal chosen differs from then on.

Conclusion: 5min and above are suitable for strategy selection; 1min is
screening only. A strategy that depends on sub-minute precision needs tick data.

Reproduce with:

```
python scripts/candle_model_validation.py --symbol XAUUSD \
    --start 2025-01-02 --end 2025-01-03 --timeframe 5min
```
