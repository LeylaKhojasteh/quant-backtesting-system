"""Archive writes are atomic: no partial visible run and no temp leftovers."""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import run_store  # noqa: E402


class RunStoreAtomicityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_success_promotes_complete_run(self):
        directory = run_store.save_run(
            self.root,
            "run_atomic_ok",
            config={"symbol": "TESTSYM"},
            result={"trades": []},
            trades=[{"id": 1, "pnl": 1.0}],
            equity=[{"pnl": 1.0}],
            strategy_source="NAME='x'\n",
        )
        self.assertTrue((directory / "config.json").is_file())
        self.assertTrue((directory / "result.json").is_file())
        self.assertTrue((directory / "trades.csv").is_file())
        self.assertTrue((directory / "equity.csv").is_file())
        self.assertTrue((directory / "strategy.py").is_file())
        self.assertEqual(list(self.root.glob(".run_atomic_ok.tmp-*")), [])

    def test_failure_leaves_no_visible_run_or_temp_directory(self):
        def broken_trades():
            yield {"id": 1}
            raise RuntimeError("simulated archive failure")

        with self.assertRaises(RuntimeError):
            run_store.save_run(
                self.root,
                "run_atomic_fail",
                config={},
                result={},
                trades=broken_trades(),
                equity=[],
            )
        self.assertFalse((self.root / "run_atomic_fail").exists())
        self.assertEqual(list(self.root.glob(".run_atomic_fail.tmp-*")), [])

    def test_run_id_path_traversal_is_rejected(self):
        with self.assertRaises(ValueError):
            run_store.save_run(
                self.root, "../escape", config={}, result={}, trades=[], equity=[]
            )
        self.assertFalse((self.root.parent / "escape").exists())

    def test_list_runs_ignores_temp_directories(self):
        temp = self.root / ".run_half.tmp-deadbeef"
        temp.mkdir()
        (temp / "result.json").write_text("{}", encoding="utf-8")
        self.assertEqual(run_store.list_runs(self.root), [])


if __name__ == "__main__":
    unittest.main()
