"""Per-signal SL/TP: optional columns win per trade, null falls back."""

import sys
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.backtest import run_backtest  # noqa: E402
from src.strategies import base as strategy_base  # noqa: E402


def _ticks(n=8):
    stamps = pd.to_datetime([f"2025-01-02T00:0{i}:00Z" for i in range(n)], utc=True)
    return pd.DataFrame({
        "timestamp": stamps,
        "bid": [100.0 + i for i in range(n)],
        "ask": [100.2 + i for i in range(n)],
    })


def _signal(when, direction="LONG", stop=None, take=None):
    row = {
        "signal_candle_start": when,
        "signal_available_time": when,
        "direction": direction,
    }
    if stop is not None or take is not None:
        row["stop_distance"] = stop
        row["take_profit_distance"] = take
    return pd.DataFrame([row])


class PerSignalLevelsTest(unittest.TestCase):
    def test_signal_levels_win_over_run(self):
        ticks = _ticks()
        signals = _signal(ticks["timestamp"].iloc[0], stop=0.5, take=100.0)
        trades = run_backtest(ticks, signals, stop_distance=50.0, take_profit_distance=50.0)
        row = trades.iloc[0]
        # Entry ask = 100.2, signal stop 0.5 -> SL at 99.7, hit on tick 0 (bid 100? no).
        # Bid falls? bids rise here; use falling ticks for the SL case below.
        self.assertEqual(row["stop_loss"], 100.2 - 0.5)

    def test_signal_stop_triggers_before_run_take(self):
        ticks = pd.DataFrame({
            "timestamp": pd.to_datetime(
                [f"2025-01-02T00:0{i}:00Z" for i in range(5)], utc=True),
            "bid": [100.0, 99.0, 98.0, 97.0, 96.0],
            "ask": [100.2, 99.2, 98.2, 97.2, 96.2],
        })
        signals = _signal(ticks["timestamp"].iloc[0], stop=1.0, take=100.0)
        trades = run_backtest(ticks, signals, stop_distance=50.0, take_profit_distance=50.0)
        row = trades.iloc[0]
        self.assertEqual(row["exit_reason"], "SL")
        self.assertAlmostEqual(row["stop_loss"], 99.2)

    def test_null_signal_falls_back_to_run(self):
        ticks = _ticks()
        signals = _signal(ticks["timestamp"].iloc[0], stop=None, take=None)
        trades = run_backtest(ticks, signals, stop_distance=5.0, take_profit_distance=10.0)
        row = trades.iloc[0]
        self.assertEqual(row["stop_loss"], 100.2 - 5.0)
        self.assertEqual(row["take_profit"], 100.2 + 10.0)

    def test_validator_rejects_bad_levels(self):
        ticks = _ticks()
        bad = _signal(ticks["timestamp"].iloc[0], stop=-1.0, take=1.0)
        self.assertIn(
            "stop_distance",
            " ".join(strategy_base.validate_signals(bad)),
        )
        zero = _signal(ticks["timestamp"].iloc[0], stop=0.0, take=1.0)
        self.assertTrue(strategy_base.validate_signals(zero))

    def test_validator_accepts_clean_per_signal_frame(self):
        ticks = _ticks()
        ok = _signal(ticks["timestamp"].iloc[0], stop=1.0, take=2.0)
        self.assertEqual(strategy_base.validate_signals(ok), [])


class TrailAndTimeExitTest(unittest.TestCase):
    def _falling_ticks(self, n=6):
        return pd.DataFrame({
            "timestamp": pd.to_datetime(
                [f"2025-01-02T00:0{i}:00Z" for i in range(n)], utc=True),
            "bid": [100.0 - i for i in range(n)],
            "ask": [100.2 - i for i in range(n)],
        })

    def test_trailing_stop_moves_only_forward_and_closes_tsl(self):
        ticks = pd.DataFrame({
            "timestamp": pd.to_datetime(
                [f"2025-01-02T00:0{i}:00Z" for i in range(6)], utc=True),
            "bid": [100.0, 101.0, 102.0, 101.5, 101.0, 100.5],
            "ask": [100.2, 101.2, 102.2, 101.7, 101.2, 100.7],
        })
        # LONG entry ask 100.2, no fixed stop, trail 1.0: best bid 102 ->
        # stop ratchets to 101 -> exit TSL at bid 101.0 on tick 4.
        frame = _signal(ticks["timestamp"].iloc[0])
        frame["trail_distance"] = 1.0
        trades = run_backtest(ticks, frame, None, None)
        row = trades.iloc[0]
        self.assertEqual(row["exit_reason"], "TSL")
        self.assertAlmostEqual(row["exit_price"], 101.0)
        self.assertAlmostEqual(row["stop_loss"], 101.0)

    def test_trailing_never_widens(self):
        ticks = pd.DataFrame({
            "timestamp": pd.to_datetime(
                [f"2025-01-02T00:0{i}:00Z" for i in range(5)], utc=True),
            "bid": [100.0, 99.5, 99.0, 98.5, 98.0],
            "ask": [100.2, 99.7, 99.2, 98.7, 98.2],
        })
        frame = _signal(ticks["timestamp"].iloc[0], stop=0.5)
        frame["trail_distance"] = 1.0
        trades = run_backtest(ticks, frame, None, None)
        row = trades.iloc[0]
        # Price never favors the trade: no ratchet happens, so the plain
        # stop (entry ask 100.2 - 0.5 = 99.7) fires with reason SL.
        self.assertEqual(row["exit_reason"], "SL")
        self.assertAlmostEqual(row["stop_loss"], 99.7)

    def test_time_exit_closes_at_deadline(self):
        ticks = _ticks(8)
        frame = _signal(ticks["timestamp"].iloc[0])
        frame["max_holding_seconds"] = 120.0
        trades = run_backtest(ticks, frame, None, None)
        row = trades.iloc[0]
        self.assertEqual(row["exit_reason"], "TIME")
        self.assertEqual(
            str(row["exit_time"]), str(ticks["timestamp"].iloc[2]),
        )

    def _px(self, bids):
        return pd.DataFrame({
            "timestamp": pd.to_datetime(
                [f"2025-01-02T00:0{i}:00Z" for i in range(len(bids))], utc=True),
            "bid": [float(b) for b in bids],
            "ask": [float(b) + 0.2 for b in bids],
        })

    def test_break_even_moves_stop_to_entry_and_reports_be(self):
        ticks = self._px([100.0, 101.5, 100.0, 99.0])
        frame = _signal(ticks["timestamp"].iloc[0], stop=2.0)
        frame["breakeven_after_distance"] = 1.0
        row = run_backtest(ticks, frame, None, None).iloc[0]
        self.assertEqual(row["exit_reason"], "BE")
        self.assertAlmostEqual(row["stop_loss"], 100.2)
        self.assertAlmostEqual(row["exit_price"], 100.0)

    def test_break_even_offset_is_applied(self):
        ticks = self._px([100.0, 101.5, 100.0, 99.0])
        frame = _signal(ticks["timestamp"].iloc[0], stop=2.0)
        frame["breakeven_after_distance"] = 1.0
        frame["breakeven_offset"] = 0.5
        row = run_backtest(ticks, frame, None, None).iloc[0]
        self.assertEqual(row["exit_reason"], "BE")
        self.assertAlmostEqual(row["stop_loss"], 100.7)

    def test_break_even_offset_zero_is_valid_and_lands_on_entry(self):
        ticks = self._px([100.0, 101.5, 100.0, 99.0])
        frame = _signal(ticks["timestamp"].iloc[0], stop=2.0)
        frame["breakeven_after_distance"] = 1.0
        frame["breakeven_offset"] = 0.0
        row = run_backtest(ticks, frame, None, None).iloc[0]
        self.assertEqual(row["exit_reason"], "BE")
        self.assertAlmostEqual(row["stop_loss"], 100.2)

    def test_trailing_waits_for_activation_distance(self):
        ticks = self._px([100.0, 100.5, 103.0, 102.0, 101.0])
        frame = _signal(ticks["timestamp"].iloc[0], stop=1.0)
        frame["trail_distance"] = 0.5
        frame["trail_after_distance"] = 2.0
        row = run_backtest(ticks, frame, None, None).iloc[0]
        self.assertEqual(row["exit_reason"], "TSL")
        self.assertAlmostEqual(row["stop_loss"], 102.5)

    def test_stepped_trailing_only_advances_by_step(self):
        ticks = self._px([100.0, 100.8, 101.2, 101.6, 100.2])
        frame = _signal(ticks["timestamp"].iloc[0], stop=2.0)
        frame["trail_distance"] = 0.5
        frame["trail_step"] = 1.0
        row = run_backtest(ticks, frame, None, None).iloc[0]
        # Only the first ratchet (98.2 -> 100.3) cleared the 1.0 step; later
        # candidates stayed below prev + step, so the stop never crept up.
        self.assertEqual(row["exit_reason"], "TSL")
        self.assertAlmostEqual(row["stop_loss"], 100.3)

    def test_engine_rejects_non_positive_run_levels(self):
        ticks = _ticks()
        signals = _signal(ticks["timestamp"].iloc[0])
        for bad in (-1.0, 0.0):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    run_backtest(ticks, signals, stop_distance=bad)
                with self.assertRaises(ValueError):
                    run_backtest(ticks, signals, take_profit_distance=bad)

    def test_engine_rejects_non_positive_signal_levels(self):
        ticks = _ticks()
        for column in ("stop_distance", "take_profit_distance", "trail_distance"):
            with self.subTest(column=column):
                frame = _signal(ticks["timestamp"].iloc[0])
                frame[column] = -0.5
                with self.assertRaises(ValueError):
                    run_backtest(ticks, frame)

    def test_misspelled_column_is_rejected_not_ignored(self):
        ticks = _ticks()
        typo = _signal(ticks["timestamp"].iloc[0])
        typo["trail_distanse"] = 1.0
        errors = " ".join(strategy_base.validate_signals(typo))
        self.assertIn("unknown columns", errors)
        self.assertIn("trail_distanse", errors)

    def test_all_allowed_columns_together_are_valid(self):
        ticks = _ticks()
        frame = _signal(ticks["timestamp"].iloc[0], stop=1.0, take=2.0)
        frame["trail_distance"] = 0.5
        frame["trail_after_distance"] = 0.2
        frame["trail_step"] = 0.1
        frame["breakeven_after_distance"] = 0.3
        frame["breakeven_offset"] = 0.0
        frame["max_holding_seconds"] = 3600.0
        self.assertEqual(strategy_base.validate_signals(frame), [])

    def test_dependent_options_are_not_silently_ignored(self):
        ticks = _ticks()
        step_only = _signal(ticks["timestamp"].iloc[0])
        step_only["trail_step"] = 1.0
        self.assertIn(
            "trail_step requires trail_distance",
            " ".join(strategy_base.validate_signals(step_only)),
        )
        offset_only = _signal(ticks["timestamp"].iloc[0])
        offset_only["breakeven_offset"] = 0.5
        self.assertIn(
            "breakeven_offset requires breakeven_after_distance",
            " ".join(strategy_base.validate_signals(offset_only)),
        )

    def test_validator_rejects_bad_trail_and_hold(self):
        ticks = _ticks()
        bad = _signal(ticks["timestamp"].iloc[0])
        bad["trail_distance"] = -1.0
        bad["max_holding_seconds"] = 0.0
        errors = " ".join(strategy_base.validate_signals(bad))
        self.assertIn("trail_distance", errors)
        self.assertIn("max_holding_seconds", errors)


if __name__ == "__main__":
    unittest.main()
