"""Optional SL/TP: the engine and the request contract accept ``None``.

Covers: no exit levels (position closes at end of data), stop-only, target-only,
omitted execution fields, one-sided execution, and rejection of non-positive
values when a level *is* provided.
"""

import sys
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import run_request  # noqa: E402
from src.backtest import run_backtest  # noqa: E402
from src.result_contract import trades_from_engine  # noqa: E402


def _ticks(prices, spread=0.2):
    stamps = pd.to_datetime(
        [f"2025-01-02T00:{i:02d}:00Z" for i in range(len(prices))], utc=True
    )
    return pd.DataFrame(
        {
            "timestamp": stamps,
            "bid": [float(p) for p in prices],
            "ask": [float(p) + spread for p in prices],
        }
    )


def _signal(when):
    return pd.DataFrame(
        [{
            "signal_candle_start": when,
            "signal_available_time": when,
            "direction": "LONG",
        }]
    )


class OptionalStopTargetTest(unittest.TestCase):
    def test_no_levels_holds_until_end_of_data(self):
        ticks = _ticks([100, 99, 98, 97, 96])
        trades = run_backtest(ticks, _signal(ticks["timestamp"].iloc[0]), None, None)
        self.assertEqual(len(trades), 1)
        row = trades.iloc[0]
        self.assertEqual(row["exit_reason"], "END_OF_DATA")
        self.assertIsNone(row["stop_loss"])
        self.assertIsNone(row["take_profit"])

    def test_stop_only_triggers_stop(self):
        ticks = _ticks([100, 99, 98, 97, 96])
        trades = run_backtest(ticks, _signal(ticks["timestamp"].iloc[0]), 1.0, None)
        row = trades.iloc[0]
        self.assertEqual(row["exit_reason"], "SL")
        self.assertIsNotNone(row["stop_loss"])
        self.assertIsNone(row["take_profit"])

    def test_target_only_triggers_target(self):
        ticks = _ticks([100, 101, 102, 103, 104])
        trades = run_backtest(ticks, _signal(ticks["timestamp"].iloc[0]), None, 1.0)
        row = trades.iloc[0]
        self.assertEqual(row["exit_reason"], "TP")
        self.assertIsNone(row["stop_loss"])
        self.assertIsNotNone(row["take_profit"])

    def test_contract_maps_missing_levels_to_none(self):
        ticks = _ticks([100, 101])
        records = trades_from_engine(
            run_backtest(ticks, _signal(ticks["timestamp"].iloc[0]), None, None)
        )
        self.assertIsNone(records[0].stop_loss)
        self.assertIsNone(records[0].take_profit)


class OptionalExecutionPayloadTest(unittest.TestCase):
    def _payload(self, execution=None):
        payload = {
            "strategy": {"selector": "candle_direction"},
            "market": {
                "symbol": "XAUUSD",
                "timeframe": "15min",
                "start": "2025-01-01",
                "end": "2025-01-31",
                "data_source": "cache:xauusd_dukas_ticks",
            },
        }
        if execution is not None:
            payload["execution"] = execution
        return payload

    def test_omitted_execution_disables_both(self):
        request = run_request.parse_request_payload(self._payload())
        self.assertIsNone(request.execution.stop_distance)
        self.assertIsNone(request.execution.take_profit_distance)

    def test_single_side_can_be_provided(self):
        request = run_request.parse_request_payload(self._payload({"stop_distance": 7}))
        self.assertEqual(request.execution.stop_distance, 7.0)
        self.assertIsNone(request.execution.take_profit_distance)

    def test_non_positive_values_still_rejected(self):
        for bad in (0, -1, True):
            with self.assertRaises(run_request.InvalidExecutionError):
                run_request.parse_request_payload(self._payload({"stop_distance": bad}))


if __name__ == "__main__":
    unittest.main()
