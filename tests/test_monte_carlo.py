"""Tests for Monte Carlo robustness analysis (``src.monte_carlo``).

Uses small deterministic synthetic archives (no engine, no data/ dependency):
Monte Carlo reads an already-completed run and must never execute anything.
"""

import json
import math
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import monte_carlo as mc  # noqa: E402
from src import run_errors  # noqa: E402


def _write_run(runs: Path, run_id: str, cumulative=None, account=None, metrics=None, raw_equity=None) -> None:
    directory = runs / run_id
    directory.mkdir(parents=True, exist_ok=True)
    equity = raw_equity if raw_equity is not None else [
        {"timestamp": f"2025-01-02T00:{i:02d}:00Z", "cumulative_pnl": v} for i, v in enumerate(cumulative or [])
    ]
    result = {
        "contract_version": "1.0",
        "run_id": run_id,
        "created_at": "2025-01-02T00:00:00Z",
        "strategy": {"name": "Candle Direction", "version": "1.0", "description": ""},
        "config": {"symbol": "XAUUSD", "timeframe": "15min", "start": "2025-01-02", "end": "2025-01-02",
                   "parameters": {}},
        "trades": [],
        "equity": equity,
        "metrics": metrics or {"total_trades": len(equity), "net_pnl": (cumulative or [0])[-1],
                               "max_drawdown": 0, "win_rate": 0, "profit_factor": 0},
        "data_quality": {"tick_count": 10, "candle_count": 10, "warnings": []},
        "execution": {"engine": "src.backtest"},
        "account": account,
    }
    (directory / "result.json").write_text(json.dumps(result), encoding="utf-8")


def _account(balances, start=1000.0, currency="USD"):
    return {
        "available": True,
        "account_currency": currency,
        "model": {"account_currency": currency, "starting_balance": start, "position_size_lots": 1, "commission_per_lot_round_turn": 0},
        "instrument": {"symbol": "XAUUSD", "quote_currency": "USD", "contract_size": 100, "tick_size": 0.01},
        "metrics": {"starting_balance": start, "ending_balance": balances[-1], "net_pnl": balances[-1] - start,
                    "total_return_pct": (balances[-1] - start) / start * 100},
        "balance_curve": [{"timestamp": f"2025-01-02T00:{i:02d}:00Z", "realized_balance": b} for i, b in enumerate(balances)],
        "trades": [],
    }


class PureStatsTest(unittest.TestCase):
    def test_max_drawdown_known_paths(self):
        self.assertEqual(mc.max_drawdown([0, 5, 2, 8]), 3)
        self.assertEqual(mc.max_drawdown([10, 10, 10]), 0)
        self.assertEqual(mc.max_drawdown([5, 4, 3, 2]), 3)  # peak includes the start

    def test_max_drawdown_pct_account(self):
        self.assertAlmostEqual(mc.max_drawdown_pct([1000, 1100, 900, 1050]), (200 / 1100) * 100)
        self.assertIsNone(mc.max_drawdown_pct([0, -5, -10]))  # peak not positive

    def test_max_losing_streak_and_zero_pnl(self):
        self.assertEqual(mc.max_losing_streak([1, -1, -1, 0, -1, -1, -1]), 3)  # zero breaks the streak
        self.assertEqual(mc.max_losing_streak([0, 0, 0]), 0)
        self.assertEqual(mc.max_losing_streak([-1, -2, -3]), 3)

    def test_percentile_and_summary(self):
        self.assertAlmostEqual(mc._percentile([1, 2, 3, 4], 50), 2.5)
        self.assertEqual(mc._percentile([7], 95), 7)
        self.assertIsNone(mc.summary_stats([]))
        stats = mc.summary_stats([float(n) for n in range(1, 101)])
        self.assertEqual(stats["min"], 1.0)
        self.assertEqual(stats["max"], 100.0)
        self.assertAlmostEqual(stats["median"], 50.5, places=4)

    def test_non_finite_values_are_ignored(self):
        stats = mc.summary_stats([1.0, float("nan"), 3.0, float("inf")])
        self.assertEqual(stats["min"], 1.0)
        self.assertEqual(stats["max"], 3.0)


class MonteCarloEngineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.runs = self.root / "runs"
        self.runs.mkdir()
        self.mc = self.root / "montecarlo"
        self.public = self.root / "public"

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, run_id, method="shuffle", sims=200, seed=1):
        return mc.run_monte_carlo(
            mc.MonteCarloRequest(run_id, method, sims, seed),
            runs=self.runs, public=self.public, montecarlo=self.mc,
        )

    def test_determinism_same_and_different_seed(self):
        _write_run(self.runs, "run_p", cumulative=[1, 3, 6, 4, 5, 9])
        a = self._run("run_p", seed=7)
        b = self._run("run_p", seed=7)
        self.assertEqual(a["summary"], b["summary"])
        self.assertEqual(a["envelope"], b["envelope"])
        c = self._run("run_p", seed=8)
        self.assertTrue(a["envelope"] != c["envelope"] or a["summary"]["max_drawdown"] != c["summary"]["max_drawdown"])

    def test_shuffle_terminal_constant_and_bootstrap_varies(self):
        _write_run(self.runs, "run_p", cumulative=[1, 3, 6, 4, 5, 9])
        sh = self._run("run_p", "shuffle", seed=3)
        self.assertTrue(sh["summary"]["terminal_pnl_constant"])
        stats = sh["summary"]["terminal_pnl"]
        self.assertEqual(stats["min"], stats["max"])       # no terminal variation
        self.assertEqual(stats["median"], 9.0)             # source net PnL
        self.assertIsNone(sh["summary"]["probability_terminal_negative"])
        bo = self._run("run_p", "bootstrap", seed=3)
        self.assertFalse(bo["summary"]["terminal_pnl_constant"])
        self.assertIsNotNone(bo["summary"]["probability_terminal_negative"])
        self.assertNotEqual(bo["summary"]["terminal_pnl"]["min"], bo["summary"]["terminal_pnl"]["max"])

    def test_price_basis_used_for_plain_run(self):
        _write_run(self.runs, "run_p", cumulative=[1, 3, 6])
        study = self._run("run_p")
        self.assertEqual(study["basis"], "price")
        self.assertIsNone(study["account"])
        self.assertIn("price terms", study["labels"]["path"])
        self.assertEqual(study["original_path"], [0.0, 1.0, 3.0, 6.0])

    def test_account_basis_used_when_available(self):
        _write_run(self.runs, "run_a", cumulative=[1, 3, 6], account=_account([1010, 990, 1030]))
        study = self._run("run_a", "shuffle")
        self.assertEqual(study["basis"], "account")
        self.assertEqual(study["account"]["currency"], "USD")
        self.assertEqual(study["account"]["starting_balance"], 1000.0)
        self.assertIn("Simulated Realized Balance", study["labels"]["path"])
        self.assertEqual(study["original_path"], [1000.0, 1010.0, 990.0, 1030.0])
        # shuffle keeps ending balance constant at the source ending balance
        self.assertEqual(study["summary"]["ending_balance"]["min"], 1030.0)
        self.assertEqual(study["summary"]["ending_balance"]["max"], 1030.0)
        self.assertIsNotNone(study["summary"]["max_drawdown_pct"])

    def test_unavailable_account_falls_back_to_price(self):
        _write_run(self.runs, "run_u", cumulative=[1, 3, 6], account={"available": False, "reason_unavailable": "FX"})
        study = self._run("run_u")
        self.assertEqual(study["basis"], "price")
        self.assertIsNone(study["summary"]["ending_balance"])
        self.assertNotIn("USD", study["labels"]["path"])

    def test_account_drawdown_amount_and_pct(self):
        # both trades positive -> no drawdown on any path
        _write_run(self.runs, "run_a", account=_account([1010, 1030]))
        study = self._run("run_a", sims=50)
        self.assertEqual(study["basis"], "account")
        self.assertEqual(study["summary"]["max_drawdown"]["max"], 0.0)
        # +200 then -200: worst % drawdown is 200/1000 (loss lands first)
        _write_run(self.runs, "run_b", account=_account([1200, 1000]))
        study_b = self._run("run_b", sims=50)
        self.assertEqual(study_b["summary"]["max_drawdown"]["max"], 200.0)
        self.assertAlmostEqual(study_b["summary"]["max_drawdown_pct"]["max"], (200 / 1000) * 100, places=4)

    def test_insufficient_trades_rejected(self):
        _write_run(self.runs, "run_one", cumulative=[5])
        with self.assertRaises(run_errors.InvalidMonteCarloError):
            self._run("run_one")

    def test_non_finite_trade_data_rejected(self):
        _write_run(self.runs, "run_bad", raw_equity=[
            {"timestamp": "2025-01-02T00:00:00Z", "cumulative_pnl": 5},
            {"timestamp": "2025-01-02T00:01:00Z", "cumulative_pnl": float("nan")},
        ])
        with self.assertRaises(run_errors.InvalidMonteCarloError):
            self._run("run_bad")

    def test_envelope_has_percentiles_and_original_path(self):
        _write_run(self.runs, "run_p", cumulative=[1, 3, 6, 4, 5, 9])
        study = self._run("run_p", sims=300)
        self.assertEqual(len(study["envelope"]), 7)  # n + 1
        self.assertEqual(study["envelope"][0]["p05"], 0.0)
        for row in study["envelope"]:
            self.assertLessEqual(row["p05"], row["median"])
            self.assertLessEqual(row["median"], row["p95"])
        self.assertEqual(len(study["original_path"]), 7)

    # ---- archive -----------------------------------------------------------

    def test_archive_save_reload_and_history(self):
        _write_run(self.runs, "run_p", cumulative=[1, 3, 6])
        study = self._run("run_p")
        reloaded = mc.load_monte_carlo(self.mc, study["study_id"])
        self.assertEqual(reloaded, study)
        items = mc.list_monte_carlo(self.mc)
        self.assertEqual([s["study_id"] for s in items], [study["study_id"]])
        self.assertEqual(items[0]["source_run_id"], "run_p")
        self.assertEqual(items[0]["basis"], "price")

    def test_missing_source_run_still_loads_study(self):
        _write_run(self.runs, "run_p", cumulative=[1, 3, 6])
        study = self._run("run_p")
        for name in (self.runs / "run_p").iterdir():
            name.unlink()
        (self.runs / "run_p").rmdir()
        cached = mc.load_monte_carlo(self.mc, study["study_id"])
        self.assertEqual(cached["source_actual"]["trade_count"], 3)
        self.assertEqual(cached["envelope"], study["envelope"])

    def test_archive_rejections(self):
        with self.assertRaises(run_errors.MonteCarloNotFoundError):
            mc.load_monte_carlo(self.mc, "mc_missing")
        with self.assertRaises(run_errors.InvalidMonteCarloIdError):
            mc.load_monte_carlo(self.mc, "../etc")


class LimitsTest(unittest.TestCase):
    def test_payload_limits_and_validation(self):
        base = {"run_id": "run_x"}
        self.assertEqual(mc.parse_monte_carlo_payload(base).simulations, mc.DEFAULT_SIMULATIONS)
        with self.assertRaises(run_errors.InvalidMonteCarloError):
            mc.parse_monte_carlo_payload({**base, "simulations": 0})
        with self.assertRaises(run_errors.MonteCarloLimitError):
            mc.parse_monte_carlo_payload({**base, "simulations": mc.HARD_MAX_SIMULATIONS + 1})
        with self.assertRaises(run_errors.InvalidMonteCarloError):
            mc.parse_monte_carlo_payload({**base, "method": "block"})
        with self.assertRaises(run_errors.InvalidMonteCarloError):
            mc.parse_monte_carlo_payload({**base, "seed": "x"})
        with self.assertRaises(run_errors.InvalidMonteCarloError):
            mc.parse_monte_carlo_payload({**base, "unknown": 1})


if __name__ == "__main__":
    unittest.main()
