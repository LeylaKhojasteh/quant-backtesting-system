"""Tests for strategy management (``src.strategy_library``)."""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import run_errors, strategy_library  # noqa: E402

STRATEGY_FIXTURES = ROOT / "tests" / "fixtures" / "strategies"
IMPORT_FIXTURES = ROOT / "tests" / "fixtures" / "import"
EMA_SOURCE = (IMPORT_FIXTURES / "ema_cross.py").read_text(encoding="utf-8")


class StrategyLibraryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name) / "strategies"
        self.dir.mkdir()
        # an imported-style file strategy
        (self.dir / "ema_cross.py").write_text(EMA_SOURCE, encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_catalog_distinguishes_builtin_and_file(self):
        entries = strategy_library.list_strategies(self.dir)
        by_source = {e["source"] for e in entries}
        self.assertIn("builtin", by_source)
        self.assertIn("file", by_source)
        builtin = next(e for e in entries if e["id"] == "candle_direction")
        self.assertEqual(builtin["file"], None)
        imported = next(e for e in entries if e["name"] == "EMA Cross")
        self.assertEqual(imported["source"], "file")
        self.assertTrue(imported["id"].endswith("ema_cross.py"))

    def test_delete_imported_strategy(self):
        info = strategy_library.delete_imported_strategy("ema_cross.py", self.dir)
        self.assertTrue(info["deleted"])
        self.assertFalse((self.dir / "ema_cross.py").exists())
        # catalog no longer lists it
        names = [e["name"] for e in strategy_library.list_strategies(self.dir)]
        self.assertNotIn("EMA Cross", names)

    def test_delete_builtin_rejected(self):
        with self.assertRaises(run_errors.BuiltinStrategyError):
            strategy_library.delete_imported_strategy("candle_direction", self.dir)
        self.assertTrue(strategy_library.is_builtin("candle_direction"))

    def test_delete_outside_root_rejected(self):
        outside = self.dir.parent / "outside.py"
        outside.write_text("x = 1\n", encoding="utf-8")
        with self.assertRaises(run_errors.StrategyDeleteError):
            strategy_library.delete_imported_strategy("../outside.py", self.dir)
        # nothing outside was removed
        self.assertTrue(outside.is_file())

    def test_delete_missing_and_empty(self):
        with self.assertRaises(run_errors.StrategyNotFoundError):
            strategy_library.delete_imported_strategy("missing.py", self.dir)
        with self.assertRaises(run_errors.StrategyNotFoundError):
            strategy_library.delete_imported_strategy("", self.dir)


if __name__ == "__main__":
    unittest.main()
