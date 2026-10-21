"""Deterministic execution-contract test (no external data, no I/O services).

Runs the verified engine on a small, readable fixture and compares the actual
output with the reference result recorded in ``expected.json``.

Contract under test: ``docs/EXECUTION_PARITY.md``.
"""

import json
import unittest
from pathlib import Path

import pandas as pd

from src import backtest

FIXTURE = Path(__file__).parent / "fixtures" / "execution_parity"


def _load_fixture():
    ticks = pd.read_csv(FIXTURE / "ticks.csv")
    ticks["timestamp"] = pd.to_datetime(ticks["timestamp"], utc=True)
    signals = pd.read_csv(FIXTURE / "signals.csv")
    signals["signal_candle_start"] = pd.to_datetime(signals["signal_candle_start"], utc=True)
    signals["signal_available_time"] = pd.to_datetime(signals["signal_available_time"], utc=True)
    expected = json.loads((FIXTURE / "expected.json").read_text(encoding="utf-8"))
    return ticks, signals, expected


def _iso(value) -> str:
    ts = pd.Timestamp(value)
    if ts.tz is None:
        ts = ts.tz_localize("UTC")
    return ts.tz_convert("UTC").isoformat().replace("+00:00", "Z")


class ExecutionParityTest(unittest.TestCase):
    """The engine must reproduce the recorded reference result exactly."""

    def test_reference_result(self):
        ticks, signals, expected = _load_fixture()
        trades = backtest.run_backtest(
            ticks,
            signals,
            expected["stop_distance"],
            expected["take_profit_distance"],
        )

        self.assertEqual(len(trades), len(expected["trades"]))
        for actual, want in zip(trades.to_dict("records"), expected["trades"]):
            with self.subTest(trade=want["trade_id"]):
                self.assertEqual(actual["trade_id"], want["trade_id"])
                self.assertEqual(actual["direction"], want["direction"])
                self.assertEqual(_iso(actual["signal_candle_start"]), want["signal_candle_start"])
                self.assertEqual(_iso(actual["signal_available_time"]), want["signal_available_time"])
                self.assertEqual(_iso(actual["entry_time"]), want["entry_time"])
                self.assertAlmostEqual(actual["entry_price"], want["entry_price"], places=9)
                self.assertAlmostEqual(actual["stop_loss"], want["stop_loss"], places=9)
                self.assertAlmostEqual(actual["take_profit"], want["take_profit"], places=9)
                self.assertEqual(_iso(actual["exit_time"]), want["exit_time"])
                self.assertAlmostEqual(actual["exit_price"], want["exit_price"], places=9)
                self.assertEqual(actual["exit_reason"], want["exit_reason"])
                self.assertAlmostEqual(actual["pnl_price"], want["pnl_price"], places=9)
                self.assertAlmostEqual(actual["duration_seconds"], want["duration_seconds"], places=6)
                self.assertAlmostEqual(actual["spread_at_entry"], want["spread_at_entry"], places=9)
                self.assertAlmostEqual(actual["spread_at_exit"], want["spread_at_exit"], places=9)

        # Signal accounting used by the runner: received / executed / ignored.
        executed = len(trades)
        received = len(signals)
        self.assertEqual(received, expected["signals"]["received"])
        self.assertEqual(executed, expected["signals"]["executed"])
        self.assertEqual(received - executed, expected["signals"]["ignored"])

    def test_exit_is_evaluated_before_signals_on_the_same_tick(self):
        """A signal available on the tick that closes a position can enter.

        Also pins the inclusive ``available_time <= tick_time`` rule.
        """
        ticks = pd.DataFrame(
            {
                "timestamp": pd.to_datetime(
                    [
                        "2025-01-06T00:00:00Z",
                        "2025-01-06T00:00:10Z",
                        "2025-01-06T00:00:20Z",
                    ],
                    utc=True,
                ),
                "bid": [100.00, 100.00, 99.40],
                "ask": [100.20, 100.20, 99.60],
            }
        )
        signals = pd.DataFrame(
            {
                "signal_candle_start": pd.to_datetime(
                    ["2025-01-06T00:00:00Z", "2025-01-06T00:00:20Z"], utc=True
                ),
                "signal_available_time": pd.to_datetime(
                    ["2025-01-06T00:00:05Z", "2025-01-06T00:00:20Z"], utc=True
                ),
                "direction": ["LONG", "LONG"],
            }
        )

        trades = backtest.run_backtest(ticks, signals, 0.5, 1.0)

        self.assertEqual(len(trades), 2)
        first, second = trades.to_dict("records")
        # First LONG entered at the ask of the first eligible tick.
        self.assertEqual(_iso(first["entry_time"]), "2025-01-06T00:00:10Z")
        self.assertAlmostEqual(first["entry_price"], 100.20, places=9)
        # Stop is hit on the third tick; the exit uses the observed BID.
        self.assertEqual(_iso(first["exit_time"]), "2025-01-06T00:00:20Z")
        self.assertAlmostEqual(first["exit_price"], 99.40, places=9)
        self.assertEqual(first["exit_reason"], "SL")
        # The second signal (available exactly at that tick) opens afterwards,
        # on the same tick, at that tick's ask.
        self.assertEqual(_iso(second["entry_time"]), _iso(first["exit_time"]))
        self.assertAlmostEqual(second["entry_price"], 99.60, places=9)
        # End of data closes it at the last tick's bid.
        self.assertEqual(second["exit_reason"], "END_OF_DATA")
        self.assertAlmostEqual(second["exit_price"], 99.40, places=9)


if __name__ == "__main__":
    unittest.main()
