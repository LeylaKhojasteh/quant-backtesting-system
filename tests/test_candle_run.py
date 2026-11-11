"""Candle dataset wiring: execute_run uses the candle engine, not ticks."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import candle_data, data_engine, run_backtest, run_request  # noqa: E402

DATASET = {
    "dataset_id": "btcusdt_binance_candles_1min",
    "symbol": "BTCUSDT",
    "provider": "BINANCE",
    "data_type": "candles",
    "base_timeframe": "1min",
    "price_type": "trade",
    "timezone": "UTC",
    "coverage_start": "2025-01-01 00:00:00+00:00",
    "coverage_end": "2025-01-01 02:00:00+00:00",
    "partitions": 1,
    "row_total": 120,
    "months": ["2025"],
    "month_details": [],
    "years": [2025],
    "partial_months": [],
}


def _candles():
    index = pd.date_range("2025-01-01 00:00:00", periods=120, freq="1min", tz="UTC")
    base = 100.0 + np.arange(120) * 0.05 + np.where(np.arange(120) % 2 == 0, 0.0, 0.6)
    close = base + np.where(np.arange(120) % 2 == 0, 0.5, -0.4)
    return pd.DataFrame({
        "open": base,
        "high": np.maximum(base, close) + 0.3,
        "low": np.minimum(base, close) - 0.3,
        "close": close,
    }, index=index)


class CandleRunWiringTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)

        def fake_load_catalog(root=None):
            return {"datasets": {DATASET["dataset_id"]: dict(DATASET)}}

        self.patches = [
            mock.patch.object(data_engine, "load_catalog", side_effect=fake_load_catalog),
            mock.patch.object(candle_data, "resolve_candle_dataset",
                              return_value=_candles()),
            mock.patch.object(candle_data, "candle_fingerprint",
                              return_value="sha256:candle-test"),
            mock.patch.object(candle_data, "candle_provenance", return_value=[]),
        ]
        for patch in self.patches:
            patch.start()
        self.out = base / "runs"
        self.public = base / "public"

    def tearDown(self):
        for patch in self.patches:
            patch.stop()
        self.tmp.cleanup()

    def _request(self, spread_bps=2.0):
        return run_request.BacktestRunRequest(
            strategy=run_request.StrategyRequest(selector="candle_direction"),
            market=run_request.MarketRequest(
                symbol="BTCUSDT", timeframe="1min",
                start="2025-01-01", end="2025-01-02",
                data_source="cache:" + DATASET["dataset_id"],
            ),
            execution=run_request.ExecutionRequest(
                stop_distance=1.0, take_profit_distance=2.0, spread_bps=spread_bps),
        )

    def test_run_uses_candle_model_and_no_ticks(self):
        info = run_backtest.execute_run(
            self._request(), out=str(self.out), report=False, public=str(self.public))
        result = info["result"]
        self.assertEqual(result.execution.execution_model, "candle")
        self.assertIsNone(result.data_quality.tick_count)
        self.assertEqual(result.data_quality.candle_count, 120)
        self.assertGreater(result.metrics.total_trades, 0)
        self.assertIsInstance(result.execution.ambiguous_exits, int)
        self.assertEqual(result.config.data_fingerprint, "sha256:candle-test")
        self.assertTrue(any("Candle execution" in w for w in result.data_quality.warnings))

    def test_archived_config_records_spread(self):
        info = run_backtest.execute_run(
            self._request(spread_bps=5.0), out=str(self.out), report=False,
            public=str(self.public))
        import json

        config = json.loads(
            (Path(info["directory"]) / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(config["execution"]["spread_bps"], 5.0)
        self.assertEqual(config["market"]["data_source"], DATASET["dataset_id"])
        self.assertEqual(config["execution"]["stop_distance"], 1.0)

    def test_contract_accepts_candle_model(self):
        info = run_backtest.execute_run(
            self._request(), out=str(self.out), report=False, public=str(self.public))
        self.assertEqual(info["result"].validate(), [])


if __name__ == "__main__":
    unittest.main()
