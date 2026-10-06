"""Tests for the Run Comparison service (``src.run_comparison``)."""

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import run_comparison, run_errors  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "run_0001"


class RunComparisonTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.runs = self.root / "runs"
        self.public = self.root / "public"
        self.runs.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def _seed(self, run_id, *, symbol=None, strategy=None, start=None, end=None, metrics=None):
        dest = self.runs / run_id
        shutil.copytree(FIXTURE, dest)
        result = json.loads((dest / "result.json").read_text(encoding="utf-8"))
        result["run_id"] = run_id
        result["created_at"] = f"2025-01-0{len(run_id)}T00:00:00Z"
        if symbol:
            result["config"]["symbol"] = symbol
        if strategy:
            result["strategy"]["name"] = strategy
        if start:
            result["config"]["start"] = start
        if end:
            result["config"]["end"] = end
        if metrics:
            result["metrics"].update(metrics)
        (dest / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        return dest

    def test_two_valid_runs(self):
        self._seed("run_a")
        self._seed("run_b", metrics={"net_pnl": 5.0})
        data = run_comparison.compare_runs(self.runs, self.public, ["run_a", "run_b"])
        self.assertEqual(len(data["runs"]), 2)
        entry = data["runs"][0]
        for key in ("total_trades", "long_trades", "short_trades", "win_rate", "net_pnl",
                    "avg_trade", "profit_factor", "max_drawdown", "expectancy", "data_quality_status"):
            self.assertIn(key, entry)
        self.assertIn("run_a", data["equity"])
        self.assertIn("run_b", data["equity"])
        self.assertTrue(any("price terms" in w for w in data["warnings"]))

    def test_mixed_symbol_warning(self):
        self._seed("run_a", symbol="XAUUSD")
        self._seed("run_b", symbol="EURUSD")
        data = run_comparison.compare_runs(self.runs, self.public, ["run_a", "run_b"])
        self.assertTrue(data["differences"]["symbol"])
        self.assertTrue(any("Symbols differ" in w for w in data["warnings"]))

    def test_mixed_strategy_and_range_warning(self):
        self._seed("run_a", strategy="Alpha")
        self._seed("run_b", strategy="Beta", start="2024-06-01T00:00:00Z")
        data = run_comparison.compare_runs(self.runs, self.public, ["run_a", "run_b"])
        self.assertTrue(data["differences"]["strategy"])
        self.assertTrue(data["differences"]["date_range"])
        self.assertTrue(any("Strategies differ" in w for w in data["warnings"]))

    def test_missing_metric_stays_unavailable(self):
        self._seed("run_a")
        self._seed("run_b", metrics={"profit_factor": None})
        data = run_comparison.compare_runs(self.runs, self.public, ["run_a", "run_b"])
        b = next(r for r in data["runs"] if r["run_id"] == "run_b")
        self.assertIsNone(b["profit_factor"])

    def test_invalid_and_missing_runs(self):
        self._seed("run_a")
        with self.assertRaises(run_errors.InvalidParametersError):
            run_comparison.compare_runs(self.runs, self.public, ["run_a"])
        with self.assertRaises(run_errors.InvalidParametersError):
            run_comparison.compare_runs(self.runs, self.public, [f"r{i}" for i in range(11)])
        with self.assertRaises(run_errors.InvalidParametersError):
            run_comparison.compare_runs(self.runs, self.public, ["run_a", "run_a"])
        with self.assertRaises(run_errors.InvalidRunIdError):
            run_comparison.compare_runs(self.runs, self.public, ["run_a", "../etc"])
        with self.assertRaises(run_errors.RunNotFoundError):
            run_comparison.compare_runs(self.runs, self.public, ["run_a", "missing"])


if __name__ == "__main__":
    unittest.main()
