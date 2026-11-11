"""Candle dataset ingestion: parquet store, catalog entry, resampling."""

import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import candle_data as cd  # noqa: E402
from src import data_engine as de  # noqa: E402

CSV = (
    "open_time_utc,open,high,low,close,volume,quote_volume,trades\n"
    "2025-01-01 00:00:00,100,101,99,100.5,1,1,10\n"
    "2025-01-01 00:01:00,100.5,102,100,101.5,1,1,10\n"
    "2025-01-01 00:02:00,101.5,103,101,102.5,1,1,10\n"
    "2025-01-01 00:03:00,102.5,104,102,103.5,1,1,10\n"
    "2025-01-01 00:04:00,103.5,105,103,104.5,1,1,10\n"
)


class CandleDataTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.candles = Path(self.tmp.name) / "data_candles"
        self.cache = Path(self.tmp.name) / "cache"
        self.symbol_dir = self.candles / "BTCUSDT" / "1m" / "yearly"
        self.symbol_dir.mkdir(parents=True)
        self.path = self.symbol_dir / "BTCUSDT_1m_2025.csv"
        self.path.write_text(CSV, encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def _build(self):
        results = cd.build_all_candles(self.candles, self.cache)
        de.build_catalog(self.cache)
        return results

    def test_filename_parsing_and_aliases(self):
        meta = cd.parse_candle_filename("BTCUSDT_1m_2025.csv")
        self.assertEqual(meta, {"symbol": "BTCUSDT", "timeframe": "1min", "year": 2025})
        self.assertIsNone(cd.parse_candle_filename("notes.csv"))

    def test_build_writes_parquet_and_catalog_entry(self):
        results = self._build()
        self.assertEqual(results[0]["rows"], 5)
        parquet = (self.cache / "candles_src" / "provider=UNSPECIFIED"
                   / "symbol=BTCUSDT" / "tf=1min" / "year=2025" / "candles.parquet")
        self.assertTrue(parquet.is_file())

        dataset = de.load_catalog(self.cache)["datasets"][
            "btcusdt_unspecified_candles_1min"]
        self.assertEqual(dataset["data_type"], "candles")
        self.assertEqual(dataset["base_timeframe"], "1min")
        self.assertEqual(dataset["row_total"], 5)
        self.assertEqual(dataset["years"], [2025])
        self.assertEqual(dataset["price_type"], "unknown")

    def test_resample_uses_bar_start_labels(self):
        self._build()
        dataset = de.load_catalog(self.cache)["datasets"][
            "btcusdt_unspecified_candles_1min"]
        frame = cd.resolve_candle_dataset(
            dataset, "5min", "2025-01-01", "2025-01-02", self.cache)
        self.assertEqual(len(frame), 1)
        bar = frame.iloc[0]
        self.assertEqual(str(frame.index[0]), "2025-01-01 00:00:00+00:00")
        self.assertAlmostEqual(bar["open"], 100.0)
        self.assertAlmostEqual(bar["high"], 105.0)
        self.assertAlmostEqual(bar["low"], 99.0)
        self.assertAlmostEqual(bar["close"], 104.5)

    def test_date_only_end_includes_the_whole_day(self):
        self._build()
        dataset = de.load_catalog(self.cache)["datasets"][
            "btcusdt_unspecified_candles_1min"]
        # Data spans 00:00..00:04 on 2025-01-01 only.
        same_day = cd.resolve_candle_dataset(
            dataset, "1min", "2025-01-01", "2025-01-01", self.cache)
        self.assertEqual(len(same_day), 5)

    def test_datetime_end_is_exclusive(self):
        self._build()
        dataset = de.load_catalog(self.cache)["datasets"][
            "btcusdt_unspecified_candles_1min"]
        frame = cd.resolve_candle_dataset(
            dataset, "1min", "2025-01-01", "2025-01-01 00:03:00", self.cache)
        self.assertEqual(len(frame), 3)

    def test_duplicate_source_is_rejected(self):
        duplicate = self.candles / "BTCUSDT" / "copy"
        duplicate.mkdir(parents=True)
        (duplicate / "BTCUSDT_1m_2025.csv").write_text(CSV, encoding="utf-8")
        with self.assertRaises(cd.CandleDataError):
            cd.discover_candle_sources(self.candles)

    def test_inconsistent_ohlc_is_rejected(self):
        self.path.write_text(
            "open_time_utc,open,high,low,close\n"
            "2025-01-01 00:00:00,100,99,101,100\n", encoding="utf-8")
        with self.assertRaises(cd.CandleDataError):
            cd.build_all_candles(self.candles, self.cache)

    def test_changed_source_needs_explicit_optin(self):
        cd.build_all_candles(self.candles, self.cache)
        self.path.write_text(CSV.replace("100,101,99,100.5", "100,101,99,100.6"),
                             encoding="utf-8")
        with self.assertRaises(cd.CandleDataError):
            cd.build_all_candles(self.candles, self.cache)
        results = cd.build_all_candles(self.candles, self.cache, allow_source_change=True)
        self.assertEqual(results[0]["status"], "BUILT")

    def test_sidecar_declares_provider_and_price_type(self):
        (self.candles / "BTCUSDT" / "_dataset.json").write_text(
            '{"provider": "BINANCE", "price_type": "trade"}', encoding="utf-8")
        self._build()
        datasets = de.load_catalog(self.cache)["datasets"]
        self.assertIn("btcusdt_binance_candles_1min", datasets)
        self.assertEqual(datasets["btcusdt_binance_candles_1min"]["price_type"], "trade")


if __name__ == "__main__":
    unittest.main()
