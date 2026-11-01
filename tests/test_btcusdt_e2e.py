"""End-to-end synthetic BTCUSDT path: build -> catalog -> candles -> run -> archive.

The fixture is deterministic synthetic data, not market data. No external
download or real backtest is involved.
"""

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import data_engine as de  # noqa: E402
from src import run_backtest, run_errors, run_request  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "data_engine" / "BTCUSDT_BINANCE_2025_01_ticks.csv"
DATASET_ID = "btcusdt_binance_ticks"


class BtcusdtEndToEndTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.data = self.root / "data"
        self.cache = self.root / "cache"
        self.data.mkdir()
        shutil.copy2(FIXTURE, self.data / FIXTURE.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _temp_cache_patch(self):
        """Keep execute_run's data-engine reads inside this test's temp cache."""
        load_ticks = de.load_ticks_frame
        load_candles = de.load_candles_frame
        fingerprint = de.cache_fingerprint
        provenance = de.data_provenance
        validate = de.validate_range

        def ticks(source, **kwargs):
            return load_ticks(source, **{**kwargs, "root": self.cache})

        def candles(source, **kwargs):
            return load_candles(source, **{**kwargs, "root": self.cache})

        def fp(source, **kwargs):
            return fingerprint(source, **{**kwargs, "root": self.cache})

        def prov(source, **kwargs):
            return provenance(source, **{**kwargs, "root": self.cache})

        def validate_range(source, **kwargs):
            return validate(source, **{**kwargs, "root": self.cache})

        return mock.patch.multiple(
            de,
            load_ticks_frame=mock.Mock(side_effect=ticks),
            load_candles_frame=mock.Mock(side_effect=candles),
            cache_fingerprint=mock.Mock(side_effect=fp),
            data_provenance=mock.Mock(side_effect=prov),
            validate_range=mock.Mock(side_effect=validate_range),
        )

    def _run_request(self, *, account=None):
        payload = {
            "strategy": {"selector": "candle_direction"},
            "market": {
                "symbol": "BTCUSDT",
                "timeframe": "15min",
                "start": "2025-01-02",
                "end": "2025-01-02",
                "dataset_id": DATASET_ID,
            },
            "execution": {"stop_distance": 100, "take_profit_distance": 200},
        }
        if account is not None:
            payload["account"] = account
        return run_request.parse_request_payload(payload)

    def test_build_catalog_candles_price_term_and_archive(self):
        built = de.build_all(self.data, self.cache)
        self.assertEqual(len(built), 1)
        catalog = de.load_catalog(self.cache)
        self.assertEqual(list(catalog["datasets"]), [DATASET_ID])
        dataset = catalog["datasets"][DATASET_ID]
        self.assertEqual(dataset["symbol"], "BTCUSDT")
        self.assertEqual(dataset["provider"], "BINANCE")
        self.assertEqual(dataset["source_timezone"], "UTC")

        candles_built = de.build_all_candles(self.data, self.cache, timeframe="15min")
        self.assertEqual(len(candles_built), 1)
        candles = de.load_candles_frame(
            f"cache:{DATASET_ID}",
            timeframe="15min",
            symbol="BTCUSDT",
            start="2025-01-02",
            end="2025-01-02",
            root=self.cache,
        )
        self.assertGreaterEqual(len(candles), 4)
        self.assertEqual(str(candles.index.tz), "UTC")
        preview = de.load_ticks_frame(
            f"cache:{DATASET_ID}",
            symbol="BTCUSDT",
            start="2025-01-02",
            end="2025-01-02",
            root=self.cache,
            limit=3,
        )
        self.assertEqual(len(preview), 3)

        request = self._run_request()
        self.assertIsNone(request.execution.pip_size)
        with self._temp_cache_patch():
            info = run_backtest.execute_run(
                request,
                out=str(self.root / "runs"),
                report=True,
                public=str(self.root / "public"),
            )
        run_dir = Path(info["directory"])
        config = json.loads((run_dir / "config.json").read_text(encoding="utf-8"))
        result = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))

        self.assertEqual(config["market"]["symbol"], "BTCUSDT")
        self.assertEqual(config["instrument"]["symbol"], "BTCUSDT")
        self.assertEqual(config["instrument"]["quote_currency"], "USDT")
        self.assertIsNone(config["instrument"]["pip_size"])
        self.assertEqual(result["config"]["symbol"], "BTCUSDT")
        self.assertEqual(result["config"]["instrument"]["symbol"], "BTCUSDT")
        self.assertIsNone(result["config"]["instrument"]["pip_size"])
        self.assertEqual(config["data_provenance"]["source_timezone"], "UTC")
        self.assertIsNone(result["account"])
        self.assertGreater(len(result["trades"]), 0)
        self.assertIn(DATASET_ID, result["data_quality"]["data_source"])
        self.assertTrue((run_dir / "report.html").is_file())

    def test_report_failure_removes_incomplete_archive(self):
        de.build_all(self.data, self.cache)
        de.build_all_candles(self.data, self.cache, timeframe="15min")
        with mock.patch("src.report.generate_report", side_effect=RuntimeError("boom")):
            with self.assertRaises(run_errors.ReportFailedError):
                with self._temp_cache_patch():
                    run_backtest.execute_run(
                        self._run_request(),
                        out=str(self.root / "runs"),
                        report=True,
                        public=str(self.root / "public"),
                    )
        runs = self.root / "runs"
        self.assertEqual(list(runs.glob("run_*")) if runs.is_dir() else [], [])

    def test_missing_or_mismatched_dataset_never_falls_back(self):
        de.build_all(self.data, self.cache)
        with self.assertRaises(de.PartitionMissingError):
            de.load_ticks_frame(
                "cache:not_btcusdt",
                start="2025-01-02",
                end="2025-01-02",
                root=self.cache,
            )
        with self.assertRaises(de.PartitionMissingError):
            de.load_ticks_frame(
                f"cache:{DATASET_ID}",
                symbol="XAUUSD",
                start="2025-01-02",
                end="2025-01-02",
                root=self.cache,
            )

    def test_account_without_compatible_currency_is_honestly_unavailable(self):
        de.build_all(self.data, self.cache)
        de.build_all_candles(self.data, self.cache, timeframe="15min")
        with self._temp_cache_patch():
            info = run_backtest.execute_run(
                self._run_request(account={
                    "account_currency": "USD",
                    "starting_balance": 10000,
                    "position_size_lots": 1,
                    "commission_per_lot_round_turn": 0,
                }),
                out=str(self.root / "runs"),
                report=False,
                public=str(self.root / "public"),
            )
        result = info["result"]
        self.assertIsNotNone(result.account)
        self.assertFalse(result.account["available"])
        self.assertTrue(result.account["reason_unavailable"])
        self.assertGreater(len(result.trades), 0)


if __name__ == "__main__":
    unittest.main()
