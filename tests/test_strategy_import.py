"""Tests for local strategy import (``src.strategy_import``).

Covers filename/size validation, the authoritative loader contract, conflict
protection, atomic writes, and that failures leave the directory unchanged with
no temp garbage.
"""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.strategy_import import (  # noqa: E402
    MAX_STRATEGY_BYTES,
    InvalidStrategyFileError,
    StrategyConflictError,
    import_strategy,
)

IMPORT_FIXTURES = ROOT / "tests" / "fixtures" / "import"
EMA_SOURCE = (IMPORT_FIXTURES / "ema_cross.py").read_text(encoding="utf-8")
SYNTAX_ERROR = (IMPORT_FIXTURES / "syntax_error.py").read_text(encoding="utf-8")
NOT_A_STRATEGY = (ROOT / "tests" / "fixtures" / "strategies" / "not_a_strategy.py").read_text(
    encoding="utf-8"
)


class StrategyImportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name) / "strategies"

    def tearDown(self):
        self.tmp.cleanup()

    def _entries(self):
        return sorted(p.name for p in self.dir.iterdir()) if self.dir.is_dir() else []

    # ---- success ----------------------------------------------------------

    def test_valid_import(self):
        strategy, target = import_strategy("ema_cross.py", EMA_SOURCE, strategy_dir=self.dir)
        self.assertEqual(target.name, "ema_cross.py")
        self.assertTrue(target.is_file())
        self.assertEqual(target.read_text(encoding="utf-8"), EMA_SOURCE)
        meta = strategy.metadata()
        self.assertEqual(meta["name"], "EMA Cross")
        self.assertEqual(strategy.parameters(), {"fast_period": 3, "slow_period": 8})
        self.assertEqual(self._entries(), ["ema_cross.py"])

    def test_legacy_function_falls_back_to_filename_stem(self):
        source = "def generate_signals(candles):\n    return candles.iloc[0:0]\n"
        strategy, _ = import_strategy("my_thing.py", source, strategy_dir=self.dir)
        self.assertEqual(strategy.metadata()["name"], "my_thing")

    # ---- filename rejection ----------------------------------------------

    def test_rejects_path_traversal_and_absolute_and_nested(self):
        for bad in ("../evil.py", "..\\evil.py", "/etc/evil.py", "C:\\evil.py",
                    "sub/evil.py", "sub\\evil.py", ".py", "evil.txt", "_hidden.py",
                    "a b.py", "", "   "):
            with self.subTest(filename=bad):
                with self.assertRaises(InvalidStrategyFileError):
                    import_strategy(bad, EMA_SOURCE, strategy_dir=self.dir)
        self.assertEqual(self._entries(), [])

    def test_rejects_empty_and_oversized_source(self):
        with self.assertRaises(InvalidStrategyFileError):
            import_strategy("empty.py", "", strategy_dir=self.dir)
        with self.assertRaises(InvalidStrategyFileError):
            import_strategy("blank.py", "   \n  ", strategy_dir=self.dir)
        with self.assertRaises(InvalidStrategyFileError):
            import_strategy("big.py", "x = 1\n" * (MAX_STRATEGY_BYTES), strategy_dir=self.dir)
        self.assertEqual(self._entries(), [])

    # ---- loader contract rejection ---------------------------------------

    def test_rejects_syntax_error_and_leaves_no_garbage(self):
        with self.assertRaises(InvalidStrategyFileError):
            import_strategy("broken.py", SYNTAX_ERROR, strategy_dir=self.dir)
        self.assertEqual(self._entries(), [])

    def test_rejects_missing_interface(self):
        with self.assertRaises(InvalidStrategyFileError):
            import_strategy("not_a_strategy.py", NOT_A_STRATEGY, strategy_dir=self.dir)
        self.assertEqual(self._entries(), [])

    def test_rejects_invalid_metadata(self):
        source = (
            "import pandas as pd\n"
            "NAME = ''\n"
            "VERSION = '1'\n"
            "def generate_signals(candles):\n"
            "    return pd.DataFrame(columns=['signal_candle_start','signal_available_time','direction'])\n"
        )
        with self.assertRaises(InvalidStrategyFileError):
            import_strategy("bad_meta.py", source, strategy_dir=self.dir)
        self.assertEqual(self._entries(), [])

    # ---- conflict ---------------------------------------------------------

    def test_duplicate_is_rejected_without_overwrite(self):
        import_strategy("ema_cross.py", EMA_SOURCE, strategy_dir=self.dir)
        original = (self.dir / "ema_cross.py").read_text(encoding="utf-8")
        changed = EMA_SOURCE.replace('VERSION = "import-1.0"', 'VERSION = "import-2.0"')
        with self.assertRaises(StrategyConflictError):
            import_strategy("ema_cross.py", changed, strategy_dir=self.dir)
        self.assertEqual((self.dir / "ema_cross.py").read_text(encoding="utf-8"), original)
        self.assertEqual(self._entries(), ["ema_cross.py"])


if __name__ == "__main__":
    unittest.main()
