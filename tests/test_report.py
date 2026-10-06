"""Tests for the standalone HTML report generator (``src/report.py``).

Covers the Task 2 contract: report generated from the validated archived
BacktestResult, all 7 run_0001 trades present, metrics match result.json,
no fabricated account metrics, portable (inline CSS/SVG, no externals).
"""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import report as report_mod  # noqa: E402
from src.result_contract import BacktestResult  # noqa: E402

RUN_0001 = ROOT / "runs" / "run_0001"
# Prefer the committed deterministic fixture so the suite is self-contained
# (runs/ is a git-ignored local archive; data/ cannot be re-run in CI).
_FIXTURE = ROOT / "tests" / "fixtures" / "run_0001"
if (_FIXTURE / "result.json").is_file():
    RUN_0001 = _FIXTURE


class ReportMatchesArchive(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.payload = json.loads((RUN_0001 / "result.json").read_text(encoding="utf-8"))
        assert BacktestResult.from_dict(cls.payload).validate() == []
        cls.html = report_mod.render_report(cls.payload)

    def test_run_identity_present(self):
        for needle in ("run_0001", "Candle Direction", "1.0", "XAUUSD",
                       "15min", "2025-01-02", "stop_distance=5"):
            self.assertIn(needle, self.html)

    def test_metrics_match_result_json(self):
        m = self.payload["metrics"]
        # win rate / net / profit factor rendered verbatim (formatted)
        self.assertIn(f"{m['win_rate']:.4f}".rstrip("0").rstrip("."), self.html)
        net = f"{m['net_pnl']:.4f}".rstrip("0").rstrip(".")
        self.assertIn(net, self.html)
        pf = f"{m['profit_factor']:.4f}".rstrip("0").rstrip(".")
        self.assertIn(pf, self.html)
        self.assertIn("Total trades</td><td>7", self.html)

    def test_all_seven_trades_present(self):
        for t in self.payload["trades"]:
            self.assertIn(str(t["entry_price"]), self.html)
            self.assertIn(str(t["exit_price"]), self.html)
            self.assertIn(t["exit_reason"], self.html)
        self.assertIn("Trades (7)", self.html)

    def test_mae_mfe_na_not_synthesized(self):
        # run_0001 carries mae/mfe None -> N/A cells, no invented numbers
        self.assertIn(">N/A</td>", self.html)

    def test_no_fabricated_account_metrics(self):
        for m in ("Balance", "Return %", "Sharpe", "Sortino", "Calmar"):
            self.assertIn(m, self.html)
        self.assertIn("Not available — requires account model", self.html)
        # no balance curve data: svg holds exactly one path (price PnL)
        self.assertEqual(self.html.count("<svg"), 1)
        self.assertNotIn("balance curve", self.html.lower().replace(
            "no balance curve", ""))

    def test_portable_no_external_deps(self):
        self.assertIn("<style>", self.html)
        self.assertNotIn("script src", self.html)
        self.assertNotIn("link rel=\"stylesheet\"", self.html)
        self.assertNotIn("http://", self.html)
        self.assertNotIn("https://", self.html)

    def test_generate_report_roundtrip(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "run_fixture"
            d.mkdir()
            (d / "result.json").write_text(
                json.dumps(self.payload), encoding="utf-8")
            out = report_mod.generate_report(d)
            self.assertTrue(out.is_file())
            text = out.read_text(encoding="utf-8")
            self.assertIn("run_0001", text)
            with self.assertRaises(FileExistsError):
                report_mod.generate_report(d)

    def test_ondisk_report_matches_result_json(self):
        """The archived report.html must be exactly the render of result.json."""
        out = RUN_0001 / "report.html"
        self.assertTrue(out.is_file(), f"missing {out}")
        text = out.read_text(encoding="utf-8")
        self.assertEqual(text, report_mod.render_report(self.payload))
        # Identity, metrics and every archived trade are present verbatim.
        for needle in ("run_0001", self.payload["strategy"]["name"],
                       self.payload["config"]["symbol"], "Trades (7)"):
            self.assertIn(needle, text)
        for t in self.payload["trades"]:
            self.assertIn(str(t["entry_price"]), text)
            self.assertIn(str(t["exit_price"]), text)
            self.assertIn(t["exit_reason"], text)
        self.assertIn("Not available — requires account model", text)


if __name__ == "__main__":
    unittest.main()

