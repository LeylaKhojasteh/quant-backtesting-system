"""Generic-symbol coverage: BTCUSDT ingestion, price-term runs, resolver safety."""

import json
import math
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import data_engine as de  # noqa: E402
from src import run_backtest, run_request  # noqa: E402
from src.bridge import _complete_years  # noqa: E402

DATA_FIXTURES = ROOT / "tests" / "fixtures" / "data_engine"


def _write_ticks(path: Path, *, epoch_ms: bool = False) -> None:
    start = pd.Timestamp("2025-01-02T00:00:00Z")
    rows = []
    for i in range(240):
        t = start + pd.Timedelta(minutes=i)
        price = 100000.0 + 300.0 * math.sin(i / 7.0)
        stamp = int(t.timestamp() * 1000) if epoch_ms else t.isoformat().replace("+00:00", "Z")
        rows.append({"timestamp": stamp, "bid": round(price, 2), "ask": round(price + 1.0, 2)})
    pd.DataFrame(rows).to_csv(path, index=False)


class GenericSymbolTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.data = self.root / "data"
        self.cache = self.root / "cache"
        self.data.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_filename_and_completeness_are_generic(self):
        meta = de.parse_source_filename("BTCUSDT_BINANCE_2025_01_ticks.csv")
        self.assertEqual(meta["symbol"], "BTCUSDT")
        self.assertEqual(meta["provider"], "BINANCE")
        self.assertEqual(de.source_completeness("BTCUSDT_BINANCE_2025_01_ticks.csv"), "unknown")
        self.assertEqual(de.parse_source_filename("EUR_USD_DUKAS_2025_01_ticks.csv")["symbol"], "EUR_USD")
        self.assertEqual(de.parse_source_filename("BTC-USD_BINANCE_2025_01_ticks.csv")["symbol"], "BTC-USD")
        spot = de.parse_source_filename("BTCUSDT_BINANCE_SPOT_2025_01_ticks.csv")
        self.assertEqual(spot["symbol"], "BTCUSDT")
        self.assertEqual(spot["provider"], "BINANCE_SPOT")
        self.assertEqual(_complete_years([{"month": "2099-01", "completeness": "unknown"}]), [])
        self.assertEqual(_complete_years([{"month": "2099-01", "completeness": "complete"}]), [2099])

    def test_epoch_timestamps_seconds_and_milliseconds(self):
        seconds = de.parse_timestamps(pd.Series([1735689600]))
        millis = de.parse_timestamps(pd.Series([1735689600000]))
        self.assertEqual(str(seconds.iloc[0]), "2025-01-01 00:00:00+00:00")
        self.assertEqual(str(millis.iloc[0]), "2025-01-01 00:00:00+00:00")

    def test_epoch_header_csv_builds_canonical_ticks(self):
        path = self.data / "BTCUSDT_BINANCE_2025_01_ticks.csv"
        _write_ticks(path, epoch_ms=True)
        frame = next(de.canonical_chunks(path))
        self.assertEqual(str(frame["timestamp"].iloc[0]), "2025-01-02 00:00:00+00:00")
        self.assertEqual(list(frame.columns), list(de.CANONICAL_COLUMNS))

    def test_header_columns_are_case_insensitive(self):
        path = self.data / "BTCUSDT_BINANCE_2025_01_ticks.csv"
        path.write_text(
            "Timestamp,Bid,Ask\n2025-01-02T00:00:00Z,100,101\n"
            "2025-01-02T00:00:01Z,100.5,101.5\n",
            encoding="utf-8",
        )
        frame = next(de.canonical_chunks(path))
        self.assertEqual(len(frame), 2)
        self.assertEqual(str(frame["timestamp"].iloc[0]), "2025-01-02 00:00:00+00:00")

    def test_ohlcv_and_trade_only_are_rejected_clearly(self):
        ohlcv = self.data / "OHLCV_BINANCE_2025_01_ticks.csv"
        ohlcv.write_text(
            "timestamp,open,high,low,close,volume\n2025-01-02T00:00:00Z,1,2,1,2,10\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(de.MalformedSchemaError, "OHLCV"):
            next(de.canonical_chunks(ohlcv))

        trade = self.data / "TRADE_BINANCE_2025_01_ticks.csv"
        trade.write_text(
            "timestamp,price,volume\n2025-01-02T00:00:00Z,100,10\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(de.MalformedSchemaError, "trade-only"):
            next(de.canonical_chunks(trade))

    def test_explicit_unknown_symbol_never_falls_back_to_the_only_dataset(self):
        for src in DATA_FIXTURES.glob("TESTSYM_*.csv"):
            shutil.copy(src, self.data / src.name)
        de.build_all(self.data, self.cache)
        with self.assertRaises(de.PartitionMissingError):
            de.load_ticks_frame(
                "cache:", symbol="OTHER", start="2099-01-01", end="2099-01-31", root=self.cache
            )
        with self.assertRaises(de.PartitionMissingError):
            de.load_ticks_frame(
                "cache:other_dataset", start="2099-01-01", end="2099-01-31", root=self.cache
            )
        # Legacy cache: with no explicit symbol/ref may still use the only dataset.
        frame = de.load_ticks_frame(
            "cache:", start="2099-01-01", end="2099-01-31", root=self.cache
        )
        self.assertGreater(len(frame), 0)

    def test_price_term_btcusdt_runs_without_account(self):
        path = self.data / "BTCUSDT_BINANCE_2025_01_ticks.csv"
        _write_ticks(path)
        request = run_request.parse_request_payload({
            "strategy": {"selector": "candle_direction"},
            "market": {
                "symbol": "BTCUSDT",
                "timeframe": "15min",
                "start": "2025-01-02",
                "end": "2025-01-02",
                "data_source": str(path),
            },
            "execution": {"stop_distance": 100, "take_profit_distance": 200},
        })
        info = run_backtest.execute_run(
            request,
            out=str(self.root / "runs"),
            report=True,
            public=str(self.root / "public"),
        )
        payload = json.loads((Path(info["directory"]) / "result.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["config"]["symbol"], "BTCUSDT")
        self.assertEqual(payload["config"]["instrument"]["quote_currency"], "USDT")
        self.assertIsNone(payload["account"])
        self.assertGreater(len(payload["trades"]), 0)
        self.assertEqual(payload["data_quality"]["data_source"], str(path))
        self.assertTrue(payload["data_quality"]["validation"]["ticks_finite"])
        html = (Path(info["directory"]) / "report.html").read_text(encoding="utf-8")
        self.assertIn("Ticks finite", html)
        self.assertIn("Bid le ask", html)

    def test_btcusdt_account_uses_registry_metadata(self):
        path = self.data / "BTCUSDT_BINANCE_2025_01_ticks.csv"
        _write_ticks(path)
        request = run_request.parse_request_payload({
            "strategy": {"selector": "candle_direction"},
            "market": {
                "symbol": "BTCUSDT",
                "timeframe": "15min",
                "start": "2025-01-02",
                "end": "2025-01-02",
                "data_source": str(path),
            },
            "execution": {"stop_distance": 100, "take_profit_distance": 200},
            "account": {
                "account_currency": "USDT",
                "starting_balance": 10000,
                "position_size_lots": 1,
                "commission_per_lot_round_turn": 0,
            },
        })
        info = run_backtest.execute_run(
            request,
            out=str(self.root / "runs"),
            report=False,
            public=str(self.root / "public"),
        )
        payload = json.loads((Path(info["directory"]) / "result.json").read_text(encoding="utf-8"))
        self.assertTrue(payload["account"]["available"])
        self.assertEqual(payload["account"]["instrument"]["quote_currency"], "USDT")
        self.assertEqual(payload["account"]["instrument"]["contract_size"], 1.0)


if __name__ == "__main__":
    unittest.main()
