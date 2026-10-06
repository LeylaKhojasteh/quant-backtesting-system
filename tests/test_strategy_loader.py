"""Tests for the generic strategy loader (``src.strategy_loader``).

Covers: loading a valid file strategy, normalized signals, and explicit
rejection of missing files, unimportable modules, missing interfaces, invalid
metadata, and signals that violate the normalized contract.
"""

import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.strategies import base as strategy_base  # noqa: E402
from src.strategy_loader import (  # noqa: E402
    StrategyLoadError,
    generate_validated_signals,
    load_strategy,
)

STRATEGIES_DIR = ROOT / "tests" / "fixtures" / "strategies"
TREND = STRATEGIES_DIR / "trend_direction.py"
THRESHOLD = STRATEGIES_DIR / "threshold_direction.py"
CLASS_THRESHOLD = STRATEGIES_DIR / "class_threshold.py"
NOT_A_STRATEGY = STRATEGIES_DIR / "not_a_strategy.py"


def _candles() -> pd.DataFrame:
    index = pd.to_datetime([
        "2025-01-02T00:00:00Z", "2025-01-02T00:15:00Z",
        "2025-01-02T00:30:00Z", "2025-01-02T00:45:00Z",
    ])
    return pd.DataFrame(
        {
            "open": [1.0, 2.0, 3.0, 2.0],
            "high": [2.0, 3.0, 4.0, 3.0],
            "low": [0.0, 1.0, 2.0, 1.0],
            "close": [1.5, 2.5, 2.0, 2.2],
        },
        index=index,
    )


class StrategyLoaderTest(unittest.TestCase):
    def test_loads_valid_fixture(self):
        strategy = load_strategy(TREND)
        meta = strategy.metadata()
        self.assertEqual(meta["name"], "Trend Direction")
        self.assertEqual(meta["version"], "test-1.0")
        self.assertIsInstance(strategy.parameters(), dict)
        self.assertEqual(strategy.parameters(), {})
        self.assertTrue(callable(strategy.generate_signals))

    def test_parameterized_strategy_receives_effective_parameters(self):
        strategy = load_strategy(THRESHOLD)
        self.assertEqual(
            strategy.parameters(),
            {"threshold": 0.0, "side": "both", "enabled": True},
        )
        candles = _candles()

        # Declared defaults (threshold 0.0, side both): one signal per move.
        default_signals = generate_validated_signals(strategy, candles)
        self.assertEqual(len(default_signals), 3)

        # A larger threshold removes signals; the strategy used its parameters.
        strategy.configure({"threshold": 0.75, "side": "both", "enabled": True})
        filtered = generate_validated_signals(strategy, candles)
        self.assertEqual(len(filtered), 1)
        self.assertEqual(list(filtered["direction"]), ["LONG"])

        # Side filter + disable switch are honored.
        strategy.configure({"threshold": 0.0, "side": "long", "enabled": True})
        self.assertEqual(len(generate_validated_signals(strategy, candles)), 2)
        strategy.configure({"threshold": 0.0, "side": "both", "enabled": False})
        self.assertEqual(len(generate_validated_signals(strategy, candles)), 0)

    def test_fixture_signals_are_normalized(self):
        strategy = load_strategy(TREND)
        signals = generate_validated_signals(strategy, _candles())
        self.assertEqual(strategy_base.validate_signals(signals), [])
        self.assertEqual(list(signals.columns), strategy_base.SIGNAL_COLUMNS)
        self.assertGreaterEqual(len(signals), 1)

    def test_class_strategy_uses_effective_parameters(self):
        strategy = load_strategy(CLASS_THRESHOLD)
        self.assertEqual(strategy.metadata()["name"], "Threshold Class")
        self.assertEqual(strategy.parameters(), {"threshold": 0.0})
        candles = _candles()
        self.assertEqual(len(generate_validated_signals(strategy, candles)), 3)
        strategy.configure({"threshold": 0.75})
        self.assertEqual(len(generate_validated_signals(strategy, candles)), 1)

    def test_missing_file_rejected(self):
        with self.assertRaises(StrategyLoadError):
            load_strategy(STRATEGIES_DIR / "does_not_exist.py")

    def test_missing_interface_rejected(self):
        with self.assertRaises(StrategyLoadError):
            load_strategy(NOT_A_STRATEGY)

    def test_unimportable_module_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "boom.py"
            path.write_text("raise RuntimeError('boom')\n", encoding="utf-8")
            with self.assertRaises(StrategyLoadError):
                load_strategy(path)

    def test_invalid_metadata_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "noname.py"
            path.write_text(
                "import pandas as pd\n"
                "NAME = ''\n"
                "VERSION = '1'\n"
                "def generate_signals(candles):\n"
                "    return pd.DataFrame(columns=['signal_candle_start','signal_available_time','direction'])\n",
                encoding="utf-8",
            )
            with self.assertRaises(StrategyLoadError):
                load_strategy(path)

    def test_invalid_parameters_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "badparams.py"
            path.write_text(
                "import pandas as pd\n"
                "NAME = 'Bad Params'\n"
                "VERSION = '1'\n"
                "PARAMETERS = ['not', 'a', 'mapping']\n"
                "def generate_signals(candles):\n"
                "    return pd.DataFrame(columns=['signal_candle_start','signal_available_time','direction'])\n",
                encoding="utf-8",
            )
            with self.assertRaises(StrategyLoadError):
                load_strategy(path)

    def test_signals_contract_violation_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad_sig.py"
            path.write_text(
                "import pandas as pd\n"
                "NAME = 'Bad Signals'\n"
                "VERSION = '1'\n"
                "def generate_signals(candles):\n"
                "    return pd.DataFrame([{'signal_candle_start': candles.index[0],"
                " 'signal_available_time': candles.index[0], 'direction': 'UP'}])\n",
                encoding="utf-8",
            )
            strategy = load_strategy(path)
            with self.assertRaises(StrategyLoadError):
                generate_validated_signals(strategy, _candles())


if __name__ == "__main__":
    unittest.main()
