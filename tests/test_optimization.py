"""Tests for the optimization orchestrator (``src.optimization``).

Uses a small synthetic tick CSV and the deterministic ``threshold_direction``
fixture strategy, so studies run through the normal single-run pipeline without
``data/``.
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

from src import optimization, run_errors, run_request  # noqa: E402

THRESHOLD = ROOT / "tests" / "fixtures" / "strategies" / "threshold_direction.py"
DECLARED = {"threshold": 0.0, "side": "both", "enabled": True}


def _write_ticks(path: Path) -> None:
    start = pd.Timestamp("2025-01-02T00:00:00Z")
    rows = []
    for i in range(240):
        t = start + pd.Timedelta(minutes=i)
        price = 2600.0 + 40.0 * math.sin(i / 7.0)
        rows.append({"timestamp": t, "bid": round(price, 2), "ask": round(price + 0.5, 2)})
    pd.DataFrame(rows).to_csv(path, index=False)


class OptimizationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.data = self.root / "ticks.csv"
        _write_ticks(self.data)
        self.runs = self.root / "runs"
        self.studies = self.root / "studies"
        self.public = self.root / "public"

    def tearDown(self):
        self.tmp.cleanup()

    def _req(self, grid, *, exec_grid=None, objective=("net_pnl", "maximize"), max_candidates=100):
        return optimization.OptimizationRequest(
            strategy=str(THRESHOLD),
            market=run_request.MarketRequest(
                symbol="XAUUSD", timeframe="15min", start="2025-01-02", end="2025-01-02",
                data_source=str(self.data),
            ),
            execution=run_request.ExecutionRequest(stop_distance=5, take_profit_distance=10),
            strategy_grid=grid,
            execution_grid=exec_grid or {},
            objective=optimization.Objective(*objective),
            max_candidates=max_candidates,
        )

    # ---- grid building ----------------------------------------------------

    def test_deterministic_typed_combination_generation(self):
        req = self._req(
            {"side": ["both", "long"], "threshold": [0.0, 1.0]},
            exec_grid={"stop_distance": [3.0, 5.0]},
        )
        combos = optimization.build_combinations(req, DECLARED)
        self.assertEqual(len(combos), 8)
        # deterministic: sorted keys (side, threshold), product order
        self.assertEqual(combos[0]["overrides"], {"side": "both", "threshold": 0.0})
        self.assertEqual(combos[0]["execution"], {"stop_distance": 3.0, "take_profit_distance": 10.0})
        self.assertEqual(combos[1]["execution"], {"stop_distance": 5.0, "take_profit_distance": 10.0})
        self.assertEqual(combos[2]["overrides"], {"side": "both", "threshold": 1.0})
        self.assertEqual(combos[-1]["overrides"], {"side": "long", "threshold": 1.0})
        # bool/typed coercion
        bool_combo = optimization.build_combinations(self._req({"enabled": [True, False]}), DECLARED)
        self.assertEqual([c["overrides"]["enabled"] for c in bool_combo], [True, False])

    def test_grid_rejections(self):
        with self.assertRaises(run_errors.InvalidOptimizationError):
            optimization.build_combinations(self._req({"nope": [1]}), DECLARED)
        with self.assertRaises(run_errors.InvalidOptimizationError):
            optimization.build_combinations(self._req({"stop_distance": [1.0]}), DECLARED)
        with self.assertRaises(run_errors.InvalidOptimizationError):
            optimization.build_combinations(self._req({"threshold": []}), DECLARED)
        with self.assertRaises(run_errors.InvalidOptimizationError):
            optimization.build_combinations(self._req({}, exec_grid={"nope": [1.0]}), DECLARED)
        with self.assertRaises(run_errors.InvalidOptimizationError):
            optimization.build_combinations(self._req({}, exec_grid={"stop_distance": [0]}), DECLARED)
        with self.assertRaises(run_errors.InvalidParametersError):
            optimization.build_combinations(self._req({"threshold": ["abc"]}), DECLARED)

    def test_candidate_limit_enforced(self):
        many = self._req({"threshold": [float(i) for i in range(101)]})
        with self.assertRaises(run_errors.CandidateLimitError):
            optimization.build_combinations(many, DECLARED)
        over_hard = self._req({"threshold": [float(i) for i in range(600)]}, max_candidates=600)
        with self.assertRaises(run_errors.CandidateLimitError):
            optimization.build_combinations(over_hard, DECLARED)

    def test_objective_validation(self):
        with self.assertRaises(run_errors.InvalidOptimizationError):
            optimization.parse_optimization_payload({
                "strategy": "candle_direction",
                "market": {"symbol": "XAUUSD", "data_source": "x.csv"},
                "strategy_grid": {"a": [1]},
                "objective": {"metric": "sharpe", "direction": "maximize"},
            })
        with self.assertRaises(run_errors.InvalidOptimizationError):
            optimization.parse_optimization_payload({
                "strategy": "candle_direction",
                "market": {"symbol": "XAUUSD", "data_source": "x.csv"},
                "objective": {"metric": "net_pnl", "direction": "best"},
            })

    # ---- study execution --------------------------------------------------

    def test_run_study_creates_normal_runs_and_ranks_maximize(self):
        req = self._req({"side": ["both", "long"], "threshold": [0.0, 1.0]})
        study = optimization.run_study(req, runs=self.runs, studies=self.studies, public=self.public)
        self.assertEqual(study["status"], "completed")
        self.assertEqual(study["candidate_count"], 4)
        self.assertTrue((self.studies / study["study_id"] / "study.json").is_file())
        for c in study["candidates"]:
            self.assertEqual(c["status"], "ok")
            self.assertTrue(c["run_id"].startswith(f"opt_{study['study_id']}_"))
            archive = self.runs / c["run_id"]
            self.assertTrue((archive / "result.json").is_file())
            self.assertTrue((archive / "report.html").is_file())
            # candidate config exactly matches the requested combination
            result = json.loads((archive / "result.json").read_text(encoding="utf-8"))
            for key, value in c["parameters"].items():
                self.assertEqual(result["config"]["parameters"][key], value)
            config = json.loads((archive / "config.json").read_text(encoding="utf-8"))
            self.assertEqual(config["execution"]["stop_distance"], c["execution"]["stop_distance"])
        # maximize net_pnl -> descending, best matches max
        values = [c["objective_value"] for c in study["candidates"]]
        self.assertEqual(study["ranking"][0], max(study["candidates"], key=lambda c: c["objective_value"])["run_id"])
        ranked_vals = [next(c for c in study["candidates"] if c["run_id"] == rid)["objective_value"] for rid in study["ranking"]]
        self.assertEqual(ranked_vals, sorted(values, reverse=True))

    def test_minimize_ranking(self):
        req = self._req({"side": ["both", "long"]}, objective=("max_drawdown", "minimize"))
        study = optimization.run_study(req, runs=self.runs, studies=self.studies, public=self.public)
        vals = [next(c for c in study["candidates"] if c["run_id"] == rid)["objective_value"] for rid in study["ranking"]]
        self.assertEqual(vals, sorted(vals))
        best = next(c for c in study["candidates"] if c["run_id"] == study["best_run_id"])
        self.assertEqual(best["objective_value"], min(c["objective_value"] for c in study["candidates"]))

    def test_failed_candidate_recorded_safely(self):
        req = self._req({"side": ["both", "bogus"]})
        study = optimization.run_study(req, runs=self.runs, studies=self.studies, public=self.public)
        ok = [c for c in study["candidates"] if c["status"] == "ok"]
        failed = [c for c in study["candidates"] if c["status"] == "failed"]
        self.assertEqual(len(ok), 1)
        self.assertEqual(len(failed), 1)
        self.assertIsNotNone(failed[0]["failure"]["code"])
        self.assertIsNone(failed[0]["metrics"])
        self.assertNotIn(failed[0]["run_id"], study["ranking"])
        self.assertEqual(study["best_run_id"], ok[0]["run_id"])

    def test_unavailable_objective_is_not_zero(self):
        self.assertIsNone(optimization._objective_value({"profit_factor": None}, optimization.Objective("profit_factor", "maximize")))
        self.assertIsNone(optimization._objective_value({"net_pnl": float("nan")}, optimization.Objective("net_pnl", "maximize")))
        self.assertIsNone(optimization._objective_value({}, optimization.Objective("net_pnl", "maximize")))
        self.assertEqual(optimization._objective_value({"net_pnl": 0.0}, optimization.Objective("net_pnl", "maximize")), 0.0)

    def test_study_reload_and_history(self):
        req = self._req({"side": ["both", "long"]})
        study = optimization.run_study(req, runs=self.runs, studies=self.studies, public=self.public)
        reloaded = optimization.load_study(self.studies, study["study_id"])
        self.assertEqual(reloaded, study)
        history = optimization.list_studies(self.studies)
        self.assertEqual([s["study_id"] for s in history], [study["study_id"]])
        self.assertEqual(history[0]["best_run_id"], study["best_run_id"])
        with self.assertRaises(run_errors.StudyNotFoundError):
            optimization.load_study(self.studies, "study_missing")
        with self.assertRaises(run_errors.InvalidStudyIdError):
            optimization.load_study(self.studies, "../etc")


if __name__ == "__main__":
    unittest.main()
