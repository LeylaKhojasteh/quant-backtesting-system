"""Tests for Walk-Forward testing (``src.walk_forward``).

Uses a small multi-day synthetic tick CSV and the deterministic
``threshold_direction`` fixture strategy, so folds run through the normal
optimization + single-run pipeline without touching ``data/``.
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

from src import optimization, run_errors, run_request, walk_forward  # noqa: E402

THRESHOLD = ROOT / "tests" / "fixtures" / "strategies" / "threshold_direction.py"
DECLARED = {"threshold": 0.0, "side": "both", "enabled": True}


def _write_ticks(path: Path, start: str, days: int, minutes: int = 15) -> None:
    begin = pd.Timestamp(start, tz="UTC")
    per_day = (24 * 60) // minutes
    rows = []
    for i in range(days * per_day):
        t = begin + pd.Timedelta(minutes=minutes * i)
        price = 2600.0 + 40.0 * math.sin(i / 9.0)
        rows.append({"timestamp": t, "bid": round(price, 2), "ask": round(price + 0.5, 2)})
    pd.DataFrame(rows).to_csv(path, index=False)


class WalkForwardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.data = self.root / "ticks.csv"
        _write_ticks(self.data, "2025-01-01", 6)
        self.runs = self.root / "runs"
        self.studies = self.root / "studies"
        self.walkforwards = self.root / "walkforwards"
        self.public = self.root / "public"

    def tearDown(self):
        self.tmp.cleanup()

    def _req(self, grid, window, *, exec_grid=None, objective=("net_pnl", "maximize"),
             max_folds=10, max_total=500, end="2025-01-06"):
        return walk_forward.WalkForwardRequest(
            strategy=str(THRESHOLD),
            market=run_request.MarketRequest(
                symbol="XAUUSD", timeframe="15min", start="2025-01-01", end=end,
                data_source=str(self.data),
            ),
            execution=run_request.ExecutionRequest(stop_distance=5, take_profit_distance=10),
            window=window,
            strategy_grid=grid,
            execution_grid=exec_grid or {},
            objective=optimization.Objective(*objective),
            max_folds=max_folds,
            max_total_candidates=max_total,
        )

    # ---- window generation ------------------------------------------------

    def test_deterministic_rolling_windows(self):
        window = walk_forward.WindowConfig(2, 1, 1, "rolling")
        folds = walk_forward.generate_folds("2025-01-01", "2025-01-05", window)
        again = walk_forward.generate_folds("2025-01-01", "2025-01-05", window)
        self.assertEqual([f.to_dict() for f in folds], [f.to_dict() for f in again])
        self.assertEqual(len(folds), 3)
        self.assertEqual(
            [(f.train_start, f.train_end, f.test_start, f.test_end) for f in folds],
            [
                ("2025-01-01", "2025-01-02", "2025-01-03", "2025-01-03"),
                ("2025-01-02", "2025-01-03", "2025-01-04", "2025-01-04"),
                ("2025-01-03", "2025-01-04", "2025-01-05", "2025-01-05"),
            ],
        )

    def test_anchored_windows(self):
        window = walk_forward.WindowConfig(2, 1, 1, "anchored")
        folds = walk_forward.generate_folds("2025-01-01", "2025-01-05", window)
        self.assertTrue(all(f.train_start == "2025-01-01" for f in folds))
        self.assertEqual([f.train_end for f in folds],
                         ["2025-01-02", "2025-01-03", "2025-01-04"])

    def test_no_look_ahead_and_boundaries(self):
        folds = walk_forward.generate_folds(
            "2025-01-01", "2025-01-05", walk_forward.WindowConfig(2, 1, 1, "rolling"))
        for f in folds:
            self.assertLess(f.train_end, f.test_start)  # test strictly after train
            self.assertLessEqual(f.test_end, "2025-01-05")

    def test_invalid_window_config(self):
        with self.assertRaises(run_errors.InvalidWalkForwardError):
            walk_forward.generate_folds("2025-01-05", "2025-01-01",
                                        walk_forward.WindowConfig(2, 1, 1, "rolling"))
        with self.assertRaises(run_errors.InvalidWalkForwardError):
            walk_forward.generate_folds("2025-01-01", "2025-01-05",
                                        walk_forward.WindowConfig(0, 1, 1, "rolling"))
        with self.assertRaises(run_errors.InvalidWalkForwardError):
            walk_forward.generate_folds("2025-01-01", "2025-01-05",
                                        walk_forward.WindowConfig(2, 1, 1, "diagonal"))
        # Window larger than the whole range -> no folds.
        with self.assertRaises(run_errors.InvalidWalkForwardError):
            walk_forward.generate_folds("2025-01-01", "2025-01-02",
                                        walk_forward.WindowConfig(5, 5, 1, "rolling"))

    # ---- orchestration + integrity ---------------------------------------

    def test_walk_forward_creates_oos_runs_after_train(self):
        req = self._req({"side": ["both", "long"]}, walk_forward.WindowConfig(2, 1, 1, "rolling"))
        study = walk_forward.run_walk_forward(
            req, runs=self.runs, studies=self.studies,
            walkforwards=self.walkforwards, public=self.public)
        self.assertEqual(study["status"], "completed")
        self.assertEqual(study["fold_count"], 4)
        self.assertEqual(study["candidates_per_fold"], 2)
        self.assertTrue((self.walkforwards / study["wf_id"] / "study.json").is_file())

        train_metrics_sum = 0.0
        for fold in study["folds"]:
            self.assertEqual(fold["status"], "ok")
            self.assertLess(fold["train_end"], fold["test_start"])
            # OOS run is a normal archive + report, local only
            archive = self.runs / fold["test_run_id"]
            self.assertTrue((archive / "result.json").is_file())
            self.assertTrue((archive / "report.html").is_file())
            self.assertFalse((self.public / f"runs-{fold['test_run_id']}-result.json").exists())
            # selected parameters equal the referenced training best candidate
            opt_study = json.loads(
                (self.studies / fold["optimization_study_id"] / "study.json").read_text(encoding="utf-8"))
            best = next(c for c in opt_study["candidates"] if c["run_id"] == opt_study["best_run_id"])
            self.assertEqual(fold["selected_parameters"], best["parameters"])
            self.assertEqual(fold["selected_run_id"], opt_study["best_run_id"])
            for m in opt_study["candidates"]:
                train_metrics_sum += m["objective_value"] or 0.0
        # aggregate must reflect OOS only
        expected_oos = sum(f["test_metrics"]["net_pnl"] for f in study["folds"])
        self.assertAlmostEqual(study["aggregate"]["total_oos_net_pnl"], expected_oos, places=6)
        self.assertNotAlmostEqual(study["aggregate"]["total_oos_net_pnl"], train_metrics_sum, places=6)

    def test_oos_parameters_applied_to_test_run(self):
        req = self._req({"side": ["long"]}, walk_forward.WindowConfig(3, 1, 1, "rolling"))
        study = walk_forward.run_walk_forward(
            req, runs=self.runs, studies=self.studies,
            walkforwards=self.walkforwards, public=self.public)
        for fold in study["folds"]:
            result = json.loads((self.runs / fold["test_run_id"] / "result.json").read_text(encoding="utf-8"))
            for key, value in fold["selected_parameters"].items():
                self.assertEqual(result["config"]["parameters"][key], value)
            self.assertEqual(result["execution"]["engine"], "src.backtest")

    def test_aggregation_only_oos_and_combines_honestly(self):
        req = self._req({"side": ["both", "long"]}, walk_forward.WindowConfig(2, 1, 1, "rolling"))
        study = walk_forward.run_walk_forward(
            req, runs=self.runs, studies=self.studies,
            walkforwards=self.walkforwards, public=self.public)
        agg = study["aggregate"]
        metrics = [f["test_metrics"] for f in study["folds"] if f["status"] == "ok"]
        self.assertEqual(agg["completed_folds"], len(metrics))
        self.assertEqual(agg["total_oos_trades"], sum(m["total_trades"] for m in metrics))
        self.assertEqual(agg["oos_wins"], sum(m["wins"] for m in metrics))
        self.assertEqual(agg["oos_losses"], sum(m["losses"] for m in metrics))
        self.assertEqual(agg["profitable_folds"], sum(1 for m in metrics if m["net_pnl"] > 0))
        self.assertEqual(agg["best_fold_pnl"], max(m["net_pnl"] for m in metrics))
        self.assertEqual(agg["worst_fold_pnl"], min(m["net_pnl"] for m in metrics))
        # PF is recomputed from summed gross values, never averaged
        gp = sum(m["gross_profit"] for m in metrics)
        gl = sum(m["gross_loss"] for m in metrics)
        if gl:
            self.assertAlmostEqual(agg["oos_profit_factor"], gp / abs(gl), places=9)

    def test_failed_training_fold_is_recorded(self):
        req = self._req({"side": ["bogus"]}, walk_forward.WindowConfig(2, 1, 1, "rolling"))
        study = walk_forward.run_walk_forward(
            req, runs=self.runs, studies=self.studies,
            walkforwards=self.walkforwards, public=self.public)
        self.assertEqual(study["status"], "failed")
        self.assertTrue(all(f["status"] == "train_failed" for f in study["folds"]))
        for fold in study["folds"]:
            self.assertIsNone(fold["test_run_id"])
            self.assertIsNone(fold["selected_parameters"])
            self.assertIsNotNone(fold["failure"]["code"])
        self.assertIsNone(study["aggregate"]["total_oos_net_pnl"])

    # ---- archive ----------------------------------------------------------

    def test_archive_reload_and_history(self):
        req = self._req({"side": ["both", "long"]}, walk_forward.WindowConfig(2, 1, 1, "rolling"))
        study = walk_forward.run_walk_forward(
            req, runs=self.runs, studies=self.studies,
            walkforwards=self.walkforwards, public=self.public)
        reloaded = walk_forward.load_walk_forward(self.walkforwards, study["wf_id"])
        self.assertEqual(reloaded, study)
        history = walk_forward.list_walk_forwards(self.walkforwards)
        self.assertEqual([s["wf_id"] for s in history], [study["wf_id"]])
        self.assertEqual(history[0]["oos_net_pnl"], study["aggregate"]["total_oos_net_pnl"])
        # Missing referenced test run does not break reload (frontend marks it).
        missing_id = study["folds"][0]["test_run_id"]
        for name in sorted((self.runs / missing_id).iterdir()):
            name.unlink()
        (self.runs / missing_id).rmdir()
        again = walk_forward.load_walk_forward(self.walkforwards, study["wf_id"])
        self.assertEqual(again["folds"][0]["test_run_id"], missing_id)

    def test_archive_rejections(self):
        with self.assertRaises(run_errors.WalkForwardNotFoundError):
            walk_forward.load_walk_forward(self.walkforwards, "wf_missing")
        with self.assertRaises(run_errors.InvalidWalkForwardIdError):
            walk_forward.load_walk_forward(self.walkforwards, "../etc")

    # ---- limits -----------------------------------------------------------

    def test_fold_and_total_limits(self):
        window = walk_forward.WindowConfig(2, 1, 1, "rolling")  # 4 folds
        with self.assertRaises(run_errors.WalkForwardLimitError):
            walk_forward.build_study_plan(self._req({"side": ["both"]}, window, max_folds=2), DECLARED)
        with self.assertRaises(run_errors.WalkForwardLimitError):
            walk_forward.build_study_plan(
                self._req({"side": ["both", "long"]}, window, max_total=4), DECLARED)
        # within limits
        folds, combos = walk_forward.build_study_plan(
            self._req({"side": ["both", "long"]}, window), DECLARED)
        self.assertEqual(len(folds), 4)
        self.assertEqual(len(combos), 2)

    def test_unknown_parameter_and_bad_mode_rejected(self):
        with self.assertRaises(run_errors.InvalidOptimizationError):
            walk_forward.build_study_plan(
                self._req({"nope": [1]}, walk_forward.WindowConfig(2, 1, 1, "rolling")), DECLARED)
        with self.assertRaises(run_errors.InvalidWalkForwardError):
            walk_forward.parse_walk_forward_payload({
                "strategy": "candle_direction",
                "market": {"symbol": "XAUUSD", "data_source": "x.csv",
                           "start": "2025-01-01", "end": "2025-01-10"},
                "window": {"train_days": 1, "test_days": 1, "step_days": 1, "mode": "diagonal"},
            })
        with self.assertRaises(run_errors.InvalidWalkForwardError):
            walk_forward.parse_walk_forward_payload({
                "strategy": "candle_direction",
                "market": {"symbol": "XAUUSD", "data_source": "x.csv",
                           "start": "2025-01-01", "end": "2025-01-10"},
                "window": {"train_days": 1, "test_days": 1, "step_days": 1},
                "objective": {"metric": "sharpe", "direction": "maximize"},
            })


if __name__ == "__main__":
    unittest.main()
