"""Data-driven instrument registry: no symbol/account hardcodes in code."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import instruments, run_request  # noqa: E402


class InstrumentRegistryTest(unittest.TestCase):
    def test_shipped_registry_covers_reference_and_crypto_symbols(self):
        registry = instruments.load_registry()
        for symbol in ("XAUUSD", "EURUSD", "GBPUSD", "USDJPY", "BTCUSDT"):
            self.assertIn(symbol, registry)
        self.assertEqual(registry["EURUSD"].pip_size, 0.0001)
        self.assertEqual(registry["BTCUSDT"].quote_currency, "USDT")
        self.assertIsNone(registry["BTCUSDT"].pip_size)
        self.assertEqual(registry["BTCUSDT"].digits, 2)
        self.assertEqual(registry["BTCUSDT"].asset_class, "crypto")

    def test_unknown_symbol_is_not_invented_and_explicit_wins(self):
        self.assertIsNone(instruments.resolve_instrument("NOT_A_SYMBOL"))
        explicit = instruments.InstrumentSpec("NOT_A_SYMBOL", "USD", 10.0, tick_size=0.1)
        self.assertIs(instruments.resolve_instrument("XAUUSD", explicit), explicit)

    def test_registry_is_editable_data_not_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "instruments.json"
            path.write_text(json.dumps({
                "version": 1,
                "instruments": {
                    "TESTSYM": {
                        "symbol": "TESTSYM",
                        "quote_currency": "USD",
                        "contract_size": 2.0,
                        "tick_size": 0.5,
                        "pip_size": None,
                        "digits": 1,
                        "asset_class": "test",
                    }
                },
            }), encoding="utf-8")
            registry = instruments.load_registry(path)
            self.assertEqual(registry["TESTSYM"].contract_size, 2.0)
            self.assertEqual(registry["TESTSYM"].digits, 1)

    def test_explicit_metadata_survives_request_parsing(self):
        spec = run_request.parse_instrument({
            "symbol": "BTCUSDT",
            "quote_currency": "USDT",
            "contract_size": 1,
            "tick_size": 0.01,
            "pip_size": None,
            "asset_class": "crypto",
            "digits": 2,
            "price_scale": 0.01,
            "session": "24x7",
        })
        self.assertEqual(spec.asset_class, "crypto")
        self.assertEqual(spec.digits, 2)
        self.assertEqual(spec.price_scale, 0.01)
        self.assertEqual(spec.session, "24x7")
        self.assertIsNone(spec.pip_size)

    def test_invalid_registry_fails_loudly(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "instruments.json"
            path.write_text(json.dumps({"instruments": {"BAD": {"symbol": "BAD"}}}), encoding="utf-8")
            with self.assertRaises(instruments.InstrumentRegistryError):
                instruments.load_registry(path)


if __name__ == "__main__":
    unittest.main()
