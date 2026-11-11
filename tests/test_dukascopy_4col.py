"""F17: headerless 4-column Dukascopy files get zero volumes, not a crash."""

import math
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import backtest, data_engine as de  # noqa: E402


class DukascopyFourColumnTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.data = self.root / "data"
        self.cache = self.root / "cache"
        self.data.mkdir()
        self.path = self.data / "TESTSYM_DUKAS_2025_01_ticks.csv"
        start = pd.Timestamp("2025.01.02 00:00:00")
        lines = []
        for i in range(60):
            t = start + pd.Timedelta(minutes=i)
            price = 100.0 + 0.5 * math.sin(i / 7.0)
            lines.append(f"{t.strftime('%Y.%m.%d')},{t.strftime('%H:%M:%S')},"
                         f"{price:.5f},{price + 0.5:.5f}")
        self.path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_build_candles_and_engine_keep_working(self):
        chunk = next(de.canonical_chunks(self.path))
        self.assertTrue((chunk["bid_volume"] == 0.0).all())
        self.assertTrue((chunk["ask_volume"] == 0.0).all())

        built = de.build_all(self.data, self.cache)
        self.assertEqual(len(built), 1)
        candles_built = de.build_all_candles(self.data, self.cache, timeframe="15min")
        self.assertEqual(len(candles_built), 1)
        candles = de.load_candles_frame(
            "cache:",
            timeframe="15min",
            symbol="TESTSYM",
            start="2025-01-02",
            end="2025-01-02",
            root=self.cache,
        )
        self.assertGreaterEqual(len(candles), 4)

        ticks = de.load_ticks_frame(
            "cache:",
            symbol="TESTSYM",
            start="2025-01-02",
            end="2025-01-02",
            root=self.cache,
        )
        signals = pd.DataFrame({
            "signal_candle_start": [ticks["timestamp"].iloc[0]],
            "signal_available_time": [ticks["timestamp"].iloc[0]],
            "direction": ["LONG"],
        })
        trades = backtest.run_backtest(ticks, signals, 0.1, 0.2)
        self.assertGreaterEqual(len(trades), 1)
        self.assertIn("pnl_price", trades.columns)


if __name__ == "__main__":
    unittest.main()
