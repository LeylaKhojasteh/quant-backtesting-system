"""End-to-end CLI tests for the one-command run + optional publish workflow.

Uses a small deterministic synthetic tick CSV so the tests never depend on the
git-ignored ``data/`` directory. Also covers the run request contract, strategy
parameter overrides and the engine/execution boundary.
"""

import json
import math
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import run_backtest  # noqa: E402
from src.result_contract import BacktestResult  # noqa: E402
from src.strategy_loader import StrategyLoadError  # noqa: E402

TREND = ROOT / "tests" / "fixtures" / "strategies" / "trend_direction.py"
THRESHOLD = ROOT / "tests" / "fixtures" / "strategies" / "threshold_direction.py"
MANIFEST = "adapters-run-manifest.json"


def _write_ticks(path: Path) -> None:
    start = pd.Timestamp("2025-01-02T00:00:00Z")
    rows = []
    for i in range(240):  # four hours of minute ticks -> 16 M15 candles
        t = start + pd.Timedelta(minutes=i)
        price = 2600.0 + 40.0 * math.sin(i / 7.0)
        rows.append({"timestamp": t, "bid": round(price, 2), "ask": round(price + 0.5, 2)})
    pd.DataFrame(rows).to_csv(path, index=False)


class RunCliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.data = self.root / "ticks.csv"
        _write_ticks(self.data)
        self.runs = self.root / "runs"
        self.public = self.root / "public"

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, *extra, run_id="run_cli"):
        return run_backtest.main([
            "--data", str(self.data),
            "--out", str(self.runs),
            "--run-id", run_id,
            "--public", str(self.public),
            *extra,
        ])

    def _result(self, directory) -> dict:
        return json.loads((Path(directory) / "result.json").read_text(encoding="utf-8"))

    def _config(self, directory) -> dict:
        return json.loads((Path(directory) / "config.json").read_text(encoding="utf-8"))

    # ---- existing workflow ------------------------------------------------

    def test_builtin_candle_direction_without_publish(self):
        directory = Path(self._run())
        for name in ("config.json", "result.json", "trades.csv", "equity.csv", "report.html"):
            self.assertTrue((directory / name).is_file(), name)
        payload = self._result(directory)
        self.assertEqual(BacktestResult.from_dict(payload).validate(), [])
        self.assertEqual(payload["strategy"]["name"], "Candle Direction")
        # Without --publish the frontend public artifacts are untouched.
        self.assertFalse(self.public.exists())

    def test_generic_strategy_with_publish(self):
        directory = Path(self._run("--strategy", str(TREND), "--publish"))
        payload = self._result(directory)
        self.assertEqual(BacktestResult.from_dict(payload).validate(), [])
        self.assertEqual(payload["strategy"]["name"], "Trend Direction")
        # The archive's strategy snapshot is the generic strategy file itself.
        self.assertIn("Trend Direction", (directory / "strategy.py").read_text(encoding="utf-8"))
        published = self.public / "runs-run_cli-result.json"
        self.assertTrue(published.is_file())
        self.assertEqual(published.read_bytes(), (directory / "result.json").read_bytes())
        manifest = json.loads((self.public / MANIFEST).read_text(encoding="utf-8"))
        self.assertEqual([r["runId"] for r in manifest["runs"]], ["run_cli"])
        self.assertEqual(manifest["runs"][0]["file"], "runs/run_cli/result.json")

    def test_invalid_strategy_rejected_before_engine(self):
        bad = self.root / "bad.py"
        bad.write_text("X = 1\n", encoding="utf-8")
        with self.assertRaises(StrategyLoadError):
            run_backtest.main([
                "--data", str(self.root / "missing.csv"),
                "--out", str(self.runs),
                "--strategy", str(bad),
                "--run-id", "run_bad",
            ])
        self.assertFalse((self.runs / "run_bad").exists())

    def test_publish_requires_report(self):
        with self.assertRaises(SystemExit):
            run_backtest.main([
                "--data", str(self.data),
                "--out", str(self.runs),
                "--no-report", "--publish",
            ])
        self.assertFalse(self.public.exists())

    # ---- request contract + parameters ------------------------------------

    def test_config_separates_strategy_market_execution(self):
        directory = Path(self._run(
            "--stop-distance", "7", "--take-profit-distance", "14",
        ))
        config = self._config(directory)
        self.assertEqual(set(config), {"run_id", "strategy", "market", "execution"})
        # candle_direction is fixed logic: no strategy parameters.
        self.assertEqual(config["strategy"]["parameters"], {})
        self.assertEqual(config["strategy"]["selector"], "candle_direction")
        self.assertEqual(config["strategy"]["file"], None)
        self.assertEqual(config["market"]["symbol"], "XAUUSD")
        self.assertEqual(config["market"]["timeframe"], "15min")
        self.assertEqual(config["market"]["data_fingerprint"],
                         self._result(directory)["config"]["data_fingerprint"])
        self.assertEqual(config["execution"],
                         {"stop_distance": 7.0, "take_profit_distance": 14.0})

    def test_generic_defaults_are_archived(self):
        directory = Path(self._run("--strategy", str(THRESHOLD), run_id="run_def"))
        defaults = {"threshold": 0.0, "side": "both", "enabled": True}
        config = self._config(directory)
        self.assertEqual(config["strategy"]["parameters"], defaults)
        self.assertEqual(config["strategy"]["overrides"], {})
        self.assertEqual(config["strategy"]["file"], str(THRESHOLD))
        # BacktestResult stays traceable to the effective strategy parameters.
        self.assertEqual(self._result(directory)["config"]["parameters"], defaults)

    def test_valid_overrides_are_archived(self):
        directory = Path(self._run(
            "--strategy", str(THRESHOLD),
            "--param", "threshold=0.75",
            "--param", "side=long",
            run_id="run_ovr",
        ))
        config = self._config(directory)
        self.assertEqual(
            config["strategy"]["parameters"],
            {"threshold": 0.75, "side": "long", "enabled": True},
        )
        self.assertEqual(
            config["strategy"]["overrides"], {"threshold": 0.75, "side": "long"}
        )
        self.assertEqual(
            self._result(directory)["config"]["parameters"],
            {"threshold": 0.75, "side": "long", "enabled": True},
        )

    def test_unknown_parameter_rejected(self):
        with self.assertRaises(ValueError):
            self._run("--strategy", str(THRESHOLD), "--param", "nope=1", run_id="run_bad1")
        self.assertFalse((self.runs / "run_bad1").exists())

    def test_invalid_parameter_value_rejected(self):
        with self.assertRaises(ValueError):
            self._run("--strategy", str(THRESHOLD), "--param", "threshold=abc", run_id="run_bad2")
        with self.assertRaises(ValueError):
            self._run("--strategy", str(THRESHOLD), "--param", "enabled=maybe", run_id="run_bad3")
        self.assertFalse((self.runs / "run_bad2").exists())
        self.assertFalse((self.runs / "run_bad3").exists())

    def test_execution_settings_are_not_strategy_parameters(self):
        # stop_distance through --param is rejected with guidance.
        with self.assertRaises(ValueError) as ctx:
            self._run("--param", "stop_distance=5", run_id="run_bad4")
        self.assertIn("--stop-distance", str(ctx.exception))
        # The dedicated flag sets execution config instead.
        directory = Path(self._run("--stop-distance", "9", run_id="run_exec"))
        self.assertEqual(
            self._config(directory)["execution"]["stop_distance"], 9.0
        )

    def test_execution_change_does_not_change_signals(self):
        a = Path(self._run("--stop-distance", "5", run_id="run_e5"))
        b = Path(self._run("--stop-distance", "25", run_id="run_e25"))
        # Same signals; only execution differs.
        self.assertEqual(
            self._result(a)["execution"]["signals_received"],
            self._result(b)["execution"]["signals_received"],
        )
        self.assertEqual(self._config(a)["strategy"]["parameters"],
                         self._config(b)["strategy"]["parameters"])

    def test_strategy_parameter_changes_signals_and_result(self):
        base = Path(self._run("--strategy", str(THRESHOLD), run_id="run_base"))
        long_only = Path(self._run(
            "--strategy", str(THRESHOLD), "--param", "side=long", run_id="run_long",
        ))
        off = Path(self._run(
            "--strategy", str(THRESHOLD), "--param", "enabled=false", run_id="run_off",
        ))
        base_sig = self._result(base)["execution"]["signals_received"]
        long_sig = self._result(long_only)["execution"]["signals_received"]
        off_sig = self._result(off)["execution"]["signals_received"]
        self.assertGreater(base_sig, 0)
        self.assertGreater(long_sig, 0)
        self.assertLess(long_sig, base_sig)       # side filter changed signals
        self.assertEqual(off_sig, 0)              # disabled strategy emits nothing
        self.assertGreater(self._result(base)["metrics"]["total_trades"], 0)

    def test_result_is_reproducible_from_inputs(self):
        a = Path(self._run("--strategy", str(THRESHOLD),
                           "--param", "side=long", run_id="run_rep_a"))
        b = Path(self._run("--strategy", str(THRESHOLD),
                           "--param", "side=long", run_id="run_rep_b"))
        ra, rb = self._result(a), self._result(b)
        self.assertEqual(ra["trades"], rb["trades"])
        self.assertEqual(ra["equity"], rb["equity"])
        self.assertEqual(ra["metrics"], rb["metrics"])
        self.assertEqual(ra["config"], rb["config"])
        ca, cb = self._config(a), self._config(b)
        for section in ("strategy", "market", "execution"):
            self.assertEqual(ca[section], cb[section])


if __name__ == "__main__":
    unittest.main()
