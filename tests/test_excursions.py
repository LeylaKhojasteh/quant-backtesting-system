"""MAE/MFE contract: exact tick-side excursion tracking.

Conventions pinned here:
- MAE is signed and <= 0, MFE is signed and >= 0 (both price terms).
- The exit side drives the movement: BID for LONG, ASK for SHORT.
- The entry spread is an immediate adverse move (same basis as PnL).
- The exit tick is included, so gap fills are reflected.
"""

import sys
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import backtest  # noqa: E402
from src.result_contract import trades_from_engine  # noqa: E402

TIMES = [
    "2025-01-01T00:00:00Z",
    "2025-01-01T00:00:01Z",
    "2025-01-01T00:00:02Z",
    "2025-01-01T00:00:03Z",
]


def _ticks(bid, ask):
    return pd.DataFrame(
        {
            "timestamp": pd.to_datetime(TIMES[: len(bid)], utc=True),
            "bid": bid,
            "ask": ask,
        }
    )


def _signals(direction="LONG"):
    return pd.DataFrame(
        {
            "signal_candle_start": pd.to_datetime(["2024-12-31T23:45:00Z"], utc=True),
            "signal_available_time": pd.to_datetime(["2025-01-01T00:00:00Z"], utc=True),
            "direction": [direction],
        }
    )


class ExcursionTest(unittest.TestCase):
    def test_long_excursion_uses_bid_and_entry_spread(self):
        ticks = _ticks([100.0, 101.0, 99.0, 104.0], [100.5, 101.5, 99.5, 104.5])
        trade = backtest.run_backtest(ticks, _signals("LONG"), 5, 10).iloc[0]
        # Entry at ASK 100.5 -> entry-tick BID 100.0 is already -0.5 adverse.
        # Worst BID before exit is 99.0 (-1.5); best is 104.0 (+3.5 at exit).
        self.assertAlmostEqual(trade["mae"], -1.5, places=9)
        self.assertAlmostEqual(trade["mfe"], 3.5, places=9)
        self.assertLessEqual(trade["mae"], 0.0)
        self.assertGreaterEqual(trade["mfe"], 0.0)

    def test_short_excursion_uses_ask(self):
        ticks = _ticks([100.0, 101.0, 99.0, 104.0], [100.5, 101.5, 99.5, 104.5])
        trade = backtest.run_backtest(ticks, _signals("SHORT"), 10, 20).iloc[0]
        # Entry at BID 100.0; ASK 100.5 is -0.5, ASK 101.5 is -1.5,
        # ASK 99.5 is +0.5, final ASK 104.5 is -4.5.
        self.assertAlmostEqual(trade["mae"], -4.5, places=9)
        self.assertAlmostEqual(trade["mfe"], 0.5, places=9)

    def test_exit_tick_and_gap_are_included(self):
        ticks = _ticks([100.0, 94.0], [100.5, 94.5])
        trade = backtest.run_backtest(ticks, _signals("LONG"), 5, 10).iloc[0]
        self.assertEqual(trade["exit_reason"], "SL")
        self.assertAlmostEqual(trade["exit_price"], 94.0, places=9)
        # A gap through the 95.5 stop fills at 94.0: MAE must equal that move.
        self.assertAlmostEqual(trade["mae"], -6.5, places=9)
        self.assertAlmostEqual(trade["pnl_price"], -6.5, places=9)

    def test_contract_mapping_and_legacy_none(self):
        ticks = _ticks([100.0, 101.0], [100.5, 101.5])
        df = backtest.run_backtest(ticks, _signals("LONG"), 5, 10)
        record = trades_from_engine(df)[0]
        self.assertAlmostEqual(record.mae, -0.5, places=9)
        self.assertAlmostEqual(record.mfe, 0.5, places=9)

        legacy = df.drop(columns=["mae", "mfe"])
        old = trades_from_engine(legacy)[0]
        self.assertIsNone(old.mae)
        self.assertIsNone(old.mfe)


if __name__ == "__main__":
    unittest.main()
