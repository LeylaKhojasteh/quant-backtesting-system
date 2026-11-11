"""Measure the candle model's error against the tick engine on real ticks.

Both models receive the SAME signals (built from the same bid 1m candles) and
the same stop/target. Any difference is the cost of the OHLC execution model,
not of the strategy.

Usage:
    python scripts/candle_model_validation.py --symbol XAUUSD \
        --start 2025-01-02 --end 2025-01-03 --timeframe 1min
"""

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import backtest, candle_engine, data_engine, run_backtest, strategy_loader  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="XAUUSD")
    parser.add_argument("--source", default="cache:xauusd_dukas_ticks")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--timeframe", default="1min")
    parser.add_argument("--stop-distance", type=float, default=5.0)
    parser.add_argument("--take-profit-distance", type=float, default=10.0)
    args = parser.parse_args(argv)

    ticks = data_engine.load_ticks_frame(
        args.source, symbol=args.symbol, start=args.start, end=args.end,
        columns=["timestamp", "bid", "ask"],
    )
    ticks.attrs["qbl_sorted"] = "timestamp"

    bid = ticks.set_index("timestamp")["bid"].resample(args.timeframe).ohlc().dropna()
    ask = ticks.set_index("timestamp")["ask"].resample(args.timeframe).ohlc().dropna()
    mid = pd.DataFrame({
        "open": (bid["open"] + ask["open"]) / 2,
        "high": (bid["high"] + ask["high"]) / 2,
        "low": (bid["low"] + ask["low"]) / 2,
        "close": (bid["close"] + ask["close"]) / 2,
    })

    spread = (ticks["ask"] - ticks["bid"])
    mid_price = (ticks["ask"] + ticks["bid"]) / 2
    spread_bps = float((spread / mid_price * 10000).median())

    strategy, _ = run_backtest.resolve_strategy("candle_direction")
    strategy.configure({})
    signals = strategy_loader.generate_validated_signals(
        strategy, bid, timeframe=args.timeframe)

    tick_trades = backtest.run_backtest(
        ticks, signals, args.stop_distance, args.take_profit_distance)
    candle_trades = candle_engine.run_candle_backtest(
        mid, signals, stop_distance=args.stop_distance,
        take_profit_distance=args.take_profit_distance,
        spread_bps=spread_bps)

    def key(row):
        return pd.Timestamp(row["signal_available_time"])

    tick_by_signal = {key(r): r for _, r in tick_trades.iterrows()}
    candle_by_signal = {key(r): r for _, r in candle_trades.iterrows()}
    shared = sorted(set(tick_by_signal) & set(candle_by_signal))

    direction_mismatch = [s for s in shared
                          if tick_by_signal[s]["direction"] != candle_by_signal[s]["direction"]]
    reason_mismatch = [s for s in shared
                       if tick_by_signal[s]["exit_reason"] != candle_by_signal[s]["exit_reason"]]
    pnl_diff = [abs(float(tick_by_signal[s]["pnl_price"])
                    - float(candle_by_signal[s]["pnl_price"])) for s in shared]

    lines = [
        f"window {args.start} -> {args.end} ({args.timeframe})",
        f"ticks {len(ticks):,} | 1m bars {len(bid):,} | assumed spread {spread_bps:.3f} bps",
        f"signals {len(signals):,}",
        f"trades: tick {len(tick_trades):,} | candle {len(candle_trades):,}",
        f"matched by signal {len(shared):,}",
        f"direction mismatch {len(direction_mismatch):,}",
        f"exit-reason mismatch {len(reason_mismatch):,}",
        f"ambiguous candle exits {int(candle_trades['ambiguous_exit'].sum()):,}",
        f"net pnl: tick {tick_trades['pnl_price'].sum():.2f} | "
        f"candle {candle_trades['pnl_price'].sum():.2f}",
        f"mean |pnl diff| over matched {sum(pnl_diff) / len(pnl_diff):.4f}"
        if pnl_diff else "mean |pnl diff| over matched n/a",
    ]

    first = None
    n = min(len(tick_trades), len(candle_trades))
    for i in range(n):
        a, b = tick_trades.iloc[i], candle_trades.iloc[i]
        if str(a["signal_available_time"]) != str(b["signal_available_time"]) or \
                a["direction"] != b["direction"]:
            first = (i, a, b)
            break
    if first:
        i, a, b = first
        lines += [
            f"first divergence at trade {i + 1}",
            f"  tick   signal={a['signal_available_time']} dir={a['direction']} "
            f"entry={a['entry_price']:.2f} exit={a['exit_time']} "
            f"reason={a['exit_reason']} pnl={a['pnl_price']:.2f}",
            f"  candle signal={b['signal_available_time']} dir={b['direction']} "
            f"entry={b['entry_price']:.2f} exit={b['exit_time']} "
            f"reason={b['exit_reason']} pnl={b['pnl_price']:.2f}",
        ]
    else:
        lines.append("no divergence in the first trades")

    out = Path(ROOT) / "candle_model_validation.txt"
    out.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
