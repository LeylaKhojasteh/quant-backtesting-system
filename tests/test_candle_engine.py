"""Candle execution model: deterministic rules, no fabricated ticks."""

import sys
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.candle_engine import run_candle_backtest  # noqa: E402


def _candles(rows):
    index = pd.date_range("2025-01-02 00:00:00", periods=len(rows), freq="1min", tz="UTC")
    frame = pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=index)
    return frame


def _signal(when, **extra):
    row = {
        "signal_candle_start": when,
        "signal_available_time": when,
        "direction": "LONG",
    }
    row.update(extra)
    return pd.DataFrame([row])


class CandleExecutionTest(unittest.TestCase):
    def test_entry_is_next_bar_open(self):
        candles = _candles([
            [100.0, 100.5, 99.5, 100.2],
            [101.0, 101.2, 100.9, 101.1],
            [101.1, 101.4, 100.8, 101.0],
        ])
        signals = _signal(candles.index[1])
        trade = run_candle_backtest(candles, signals).iloc[0]
        self.assertEqual(trade["entry_time"], candles.index[1])
        self.assertAlmostEqual(trade["entry_price"], 101.0)
        self.assertEqual(trade["exit_reason"], "END_OF_DATA")

    def test_round_trip_costs_exactly_one_full_spread(self):
        # Flat prices: pnl must equal minus one full spread, not two.
        candles = _candles([
            [100.0, 100.0, 100.0, 100.0],
            [100.0, 100.0, 100.0, 100.0],
            [100.0, 100.0, 100.0, 100.0],
        ])
        signals = _signal(candles.index[1])
        trade = run_candle_backtest(candles, signals, spread_bps=100).iloc[0]
        self.assertAlmostEqual(trade["entry_price"], 100.5)   # ask
        self.assertAlmostEqual(trade["exit_price"], 99.5)     # bid
        self.assertAlmostEqual(trade["pnl_price"], -1.0)
        self.assertAlmostEqual(trade["spread_at_entry"], 1.0)

    def test_both_levels_in_one_bar_uses_stop_and_flags_it(self):
        candles = _candles([
            [100.0, 100.2, 99.8, 100.0],
            [100.0, 103.0, 97.0, 100.0],
        ])
        signals = _signal(candles.index[1])
        trade = run_candle_backtest(
            candles, signals, stop_distance=2.0, take_profit_distance=2.0).iloc[0]
        self.assertEqual(trade["exit_reason"], "SL")
        self.assertAlmostEqual(trade["exit_price"], 98.0)
        self.assertTrue(bool(trade["ambiguous_exit"]))

    def test_a_closed_trade_is_never_closed_again(self):
        # Regression: the position slot must be freed after a close, otherwise
        # every following bar records the same trade again.
        candles = _candles([
            [100.0, 100.2, 99.8, 100.0],
            [100.0, 100.2, 99.8, 100.0],
            [100.0, 100.2, 96.0, 96.5],
            [96.5, 97.0, 96.0, 96.8],
        ])
        signals = _signal(candles.index[1], stop_distance=None)
        trades = run_candle_backtest(candles, signals, stop_distance=2.0)
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades.iloc[0]["exit_reason"], "SL")

    def test_gap_through_stop_fills_at_open(self):
        candles = _candles([
            [100.0, 100.2, 99.8, 100.0],
            [100.0, 100.5, 99.5, 100.0],
            [95.0, 96.0, 94.0, 95.0],
        ])
        signals = _signal(candles.index[1])
        trade = run_candle_backtest(candles, signals, stop_distance=2.0).iloc[0]
        self.assertEqual(trade["exit_reason"], "SL")
        self.assertAlmostEqual(trade["exit_price"], 95.0)

    def test_time_exit_runs_at_next_open(self):
        candles = _candles([
            [100.0, 100.2, 99.8, 100.0],
            [100.0, 100.2, 99.8, 100.0],
            [100.0, 100.2, 99.8, 100.0],
            [100.0, 100.2, 99.8, 100.0],
        ])
        signals = _signal(candles.index[1], max_holding_seconds=120)
        trade = run_candle_backtest(candles, signals).iloc[0]
        self.assertEqual(trade["exit_reason"], "TIME")
        self.assertEqual(trade["exit_time"], candles.index[3])

    def test_trailing_takes_effect_only_on_the_next_bar(self):
        candles = _candles([
            [100.0, 100.2, 99.8, 100.0],
            [100.0, 102.0, 99.0, 102.0],   # high 102 but low 99: no stop yet
            [101.0, 101.2, 100.5, 100.6],  # stop was 101 from previous close
        ])
        signals = _signal(candles.index[1], trail_distance=1.0)
        trade = run_candle_backtest(candles, signals).iloc[0]
        self.assertEqual(trade["exit_reason"], "TSL")
        self.assertAlmostEqual(trade["stop_loss"], 101.0)
        self.assertEqual(trade["exit_time"], candles.index[2])

    def test_signals_during_an_open_trade_are_ignored_not_queued(self):
        candles = _candles([
            [100.0, 100.2, 99.8, 100.0],
            [100.0, 100.2, 99.8, 100.0],
            [100.0, 100.2, 99.8, 100.0],
            [100.0, 100.2, 99.8, 100.0],
            [100.0, 100.2, 99.8, 100.0],
        ])
        signals = pd.concat([
            _signal(candles.index[1], max_holding_seconds=120),
            _signal(candles.index[2]),
            _signal(candles.index[3]),
        ], ignore_index=True)
        trades = run_candle_backtest(candles, signals)
        # The only trade is the first signal; the ones that arrived while it was
        # open must never be replayed later.
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades.iloc[0]["signal_available_time"], candles.index[1])
        self.assertEqual(trades.iloc[0]["exit_reason"], "TIME")

    def test_no_levels_holds_to_end_of_data(self):
        candles = _candles([[100.0, 100.5, 99.5, 100.0], [100.0, 100.5, 99.5, 100.0]])
        trade = run_candle_backtest(candles, _signal(candles.index[1])).iloc[0]
        self.assertEqual(trade["exit_reason"], "END_OF_DATA")
        self.assertIsNone(trade["stop_loss"])

    def test_inconsistent_ohlc_is_rejected(self):
        candles = _candles([[100.0, 99.0, 101.0, 100.0], [100.0, 100.5, 99.5, 100.0]])
        with self.assertRaises(ValueError):
            run_candle_backtest(candles, _signal(candles.index[1]))

    def test_negative_spread_is_rejected(self):
        candles = _candles([[100.0, 100.5, 99.5, 100.0], [100.0, 100.5, 99.5, 100.0]])
        with self.assertRaises(ValueError):
            run_candle_backtest(candles, _signal(candles.index[1]), spread_bps=-1)


if __name__ == "__main__":
    unittest.main()
