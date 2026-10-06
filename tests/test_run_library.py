"""Tests for the Run Library service (``src.run_library``).

Uses a temp runs directory seeded from the deterministic run_0001 fixture, so
listing/ordering/publish/delete are exercised without the engine or data/.
"""

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import run_errors, run_library  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "run_0001"


class RunLibraryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.runs = self.root / "runs"
        self.public = self.root / "public"
        self.runs.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def _seed(self, run_id: str, created_at: str, *, nested: bool = False):
        dest = self.runs / run_id
        shutil.copytree(FIXTURE, dest)
        result = json.loads((dest / "result.json").read_text(encoding="utf-8"))
        result["run_id"] = run_id
        result["created_at"] = created_at
        (dest / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        if nested:
            (dest / "config.json").write_text(json.dumps({
                "run_id": run_id,
                "strategy": {"selector": "candle_direction", "file": None,
                             "parameters": {"fast": 2}, "overrides": {"fast": 2}},
                "market": {"symbol": "XAUUSD", "timeframe": "15min"},
                "execution": {"stop_distance": 7.0, "take_profit_distance": 14.0},
            }, indent=2), encoding="utf-8")
        return dest

    def test_list_deterministic_newest_first(self):
        self._seed("run_a", "2025-01-01T00:00:00Z")
        self._seed("run_b", "2025-02-01T00:00:00Z")
        summaries = run_library.list_summaries(self.runs, self.public)
        self.assertEqual([s["run_id"] for s in summaries], ["run_b", "run_a"])

    def test_summary_includes_metrics_and_report(self):
        self._seed("run_a", "2025-01-01T00:00:00Z")
        summary = run_library.run_summary(self.runs, "run_a", self.public)
        self.assertEqual(summary["strategy"]["name"], "Candle Direction")
        self.assertEqual(summary["symbol"], "XAUUSD")
        self.assertIsInstance(summary["total_trades"], int)
        self.assertIn("win_rate", summary)
        self.assertTrue(summary["report"])
        self.assertFalse(summary["published"])  # nothing published in temp public

    def test_unpublished_then_published_state(self):
        self._seed("run_a", "2025-01-01T00:00:00Z")
        self.assertFalse(run_library.is_published("run_a", self.public))
        info = run_library.publish_archived("run_a", self.runs, self.public)
        self.assertTrue((self.public / "runs-run_a-result.json").is_file())
        self.assertTrue(run_library.is_published("run_a", self.public))
        # idempotent second publish writes nothing
        again = run_library.publish_archived("run_a", self.runs, self.public)
        self.assertFalse(again["wrote_manifest"])
        self.assertFalse(again["wrote_result"])

    def test_detail_returns_reuse_config_nested(self):
        self._seed("run_a", "2025-01-01T00:00:00Z", nested=True)
        detail = run_library.run_detail(self.runs, "run_a", self.public)
        self.assertEqual(detail["parameters"], {"fast": 2})
        self.assertEqual(detail["execution"], {"stop_distance": 7.0, "take_profit_distance": 14.0})
        self.assertEqual(detail["market"]["symbol"], "XAUUSD")
        self.assertTrue(str(detail["market"]["dataset"]).endswith(".csv"))

    def test_detail_handles_legacy_flat_config(self):
        self._seed("run_a", "2025-01-01T00:00:00Z")  # fixture uses flat config
        detail = run_library.run_detail(self.runs, "run_a", self.public)
        # engine keys are excluded from strategy parameters
        self.assertNotIn("stop_distance", detail["parameters"])
        self.assertIsInstance(detail["parameters"], dict)

    def test_report_missing_and_present(self):
        self._seed("run_a", "2025-01-01T00:00:00Z")
        self.assertTrue(run_library.report_path(self.runs, "run_a").is_file())
        (self.runs / "run_a" / "report.html").unlink()
        with self.assertRaises(run_errors.ReportUnavailableError):
            run_library.report_path(self.runs, "run_a")

    def test_invalid_and_missing_ids(self):
        for bad in ("../etc", "a/b", "a\\b", "", "..", "."):
            with self.subTest(run_id=bad):
                with self.assertRaises(run_errors.InvalidRunIdError):
                    run_library.validate_run_id(bad)
        with self.assertRaises(run_errors.RunNotFoundError):
            run_library.run_summary(self.runs, "nope", self.public)

    def test_delete_removes_archive_and_published_artifacts(self):
        self._seed("run_a", "2025-01-01T00:00:00Z")
        self._seed("run_b", "2025-02-01T00:00:00Z")
        run_library.publish_archived("run_a", self.runs, self.public)
        info = run_library.delete_run(self.runs, "run_a", self.public)
        self.assertFalse((self.runs / "run_a").exists())
        self.assertFalse((self.public / "runs-run_a-result.json").exists())
        self.assertTrue(info["removed_manifest_entry"])
        manifest = json.loads((self.public / "adapters-run-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual([r["runId"] for r in manifest["runs"]], [])
        # unrelated run untouched
        self.assertTrue((self.runs / "run_b" / "result.json").is_file())

    def test_delete_missing_run(self):
        with self.assertRaises(run_errors.RunNotFoundError):
            run_library.delete_run(self.runs, "nope", self.public)


if __name__ == "__main__":
    unittest.main()
