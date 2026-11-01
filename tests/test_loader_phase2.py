"""Phase 2: loader detects signal vs external strategies (no behavior change)."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import strategy_library, strategy_loader  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "strategies"
TREND = FIXTURES / "trend_direction.py"
EXTERNAL = FIXTURES / "external_minimal.py"
EXTERNAL_NO_PARAMS = FIXTURES / "external_no_params.py"
NOT_A_STRATEGY = FIXTURES / "not_a_strategy.py"


class _Data:
    candles = [0, 1, 2, 3]


class _Ctx:
    parameters = {"warmup_bars": 2}


class LoaderPhase2Test(unittest.TestCase):
    def test_signal_path_unchanged_and_kind_signal(self):
        strategy = strategy_loader.load_strategy(TREND)
        manifest = strategy_loader.manifest_for(strategy)
        self.assertEqual(manifest["kind"], "signal")
        self.assertEqual(manifest["name"], "Trend Direction")
        self.assertTrue(callable(strategy.generate_signals))

    def test_external_detected_with_metadata_and_params(self):
        strategy = strategy_loader.load_strategy(EXTERNAL)
        manifest = strategy_loader.manifest_for(strategy)
        self.assertEqual(manifest["kind"], "external")
        self.assertEqual(manifest["name"], "External Minimal")
        self.assertEqual(manifest["version"], "test-x1")
        self.assertEqual(manifest["parameters"], {"warmup_bars": 2, "spread": 0.0})
        out = strategy.run_strategy(_Data(), _Ctx())
        self.assertEqual(out["signals_seen"], 2)

    def test_external_without_parameters_rejected(self):
        with self.assertRaises(strategy_loader.StrategyLoadError) as ctx:
            strategy_loader.load_strategy(EXTERNAL_NO_PARAMS)
        self.assertIn("PARAMETERS", str(ctx.exception))

    def test_neither_interface_rejected_with_new_message(self):
        with self.assertRaises(strategy_loader.StrategyLoadError) as ctx:
            strategy_loader.load_strategy(NOT_A_STRATEGY)
        self.assertIn("run_strategy", str(ctx.exception))

    def test_catalog_exposes_signal_and_external_kinds(self):
        import shutil
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            for name in ("trend_direction.py", "external_minimal.py"):
                shutil.copy2(FIXTURES / name, target / name)
            entries = {
                e["name"]: e
                for e in strategy_library.list_strategies(target)
                if e["source"] == "file"
            }
        self.assertEqual(entries["Trend Direction"]["kind"], "signal")
        self.assertEqual(entries["Trend Direction"]["execution_model"], "engine")
        self.assertEqual(entries["External Minimal"]["kind"], "external")
        self.assertEqual(entries["External Minimal"]["execution_model"], "strategy")
        self.assertEqual(
            set(entries["External Minimal"]["parameters"]),
            {"warmup_bars", "spread"},
        )


if __name__ == "__main__":
    unittest.main()
