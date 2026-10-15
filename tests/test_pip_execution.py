"""Pip-based SL/TP (v1.0.3).

Users express SL/TP in pips; the engine must still receive price distances.
These tests pin the shared conversion, the legacy passthrough, archiving of both
intent and effective values, and the workflow reuse (optimization / walk-forward).
"""

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import accounting, execution, optimization, run_errors, run_request  # noqa: E402


def _payload(symbol="XAUUSD", execution=None):
    return {
        "strategy": {"selector": "candle_direction"},
        "market": {"symbol": symbol, "data_source": "cache:x"},
        "execution": execution if execution is not None else {},
    }


class PipSizeModelTest(unittest.TestCase):
    def test_catalog_pip_sizes(self):
        self.assertEqual(accounting.INSTRUMENT_CATALOG["XAUUSD"].pip_size, 0.01)
        self.assertEqual(accounting.INSTRUMENT_CATALOG["EURUSD"].pip_size, 0.0001)
        self.assertEqual(accounting.INSTRUMENT_CATALOG["GBPUSD"].pip_size, 0.0001)
        self.assertEqual(accounting.INSTRUMENT_CATALOG["USDJPY"].pip_size, 0.01)

    def test_catalog_exposes_pip_size_to_frontend(self):
        entry = next(e for e in accounting.instrument_catalog() if e["symbol"] == "XAUUSD")
        self.assertEqual(entry["pip_size"], 0.01)

    def test_pip_size_never_inferred_from_tick(self):
        # XAUUSD: pip 0.01 == tick 0.01; EURUSD: pip 0.0001 != tick 0.00001.
        # The model must read pip_size explicitly, not derive it.
        spec = accounting.InstrumentSpec("ZZZ", "USD", 1.0, tick_size=0.5, pip_size=None)
        with self.assertRaises(run_errors.InvalidExecutionError):
            execution.require_pip_size(spec, "ZZZ")


class ConversionTest(unittest.TestCase):
    def test_price_distance_from_pips(self):
        self.assertAlmostEqual(execution.price_distance_from_pips(10, 0.01), 0.10, places=12)
        self.assertAlmostEqual(execution.price_distance_from_pips(10, 0.0001), 0.0010, places=12)
        self.assertAlmostEqual(execution.price_distance_from_pips(100, 0.01), 1.00, places=12)

    def test_xauusd_pips(self):
        req = run_request.parse_request_payload(
            _payload("XAUUSD", {"stop_loss_pips": 10, "take_profit_pips": 100}))
        self.assertEqual(req.execution.pip_size, 0.01)
        self.assertAlmostEqual(req.execution.stop_distance, 0.10, places=12)
        self.assertAlmostEqual(req.execution.take_profit_distance, 1.00, places=12)

    def test_eurusd_pips(self):
        req = run_request.parse_request_payload(
            _payload("EURUSD", {"stop_loss_pips": 10, "take_profit_pips": 100}))
        self.assertEqual(req.execution.pip_size, 0.0001)
        self.assertAlmostEqual(req.execution.stop_distance, 0.0010, places=12)
        self.assertAlmostEqual(req.execution.take_profit_distance, 0.0100, places=12)

    def test_usdjpy_pips(self):
        req = run_request.parse_request_payload(
            _payload("USDJPY", {"stop_loss_pips": 10, "take_profit_pips": 100}))
        self.assertEqual(req.execution.pip_size, 0.01)
        self.assertAlmostEqual(req.execution.stop_distance, 0.10, places=12)
        self.assertAlmostEqual(req.execution.take_profit_distance, 1.00, places=12)

    def test_legacy_price_distance_unchanged(self):
        req = run_request.parse_request_payload(
            _payload("XAUUSD", {"stop_distance": 10, "take_profit_distance": 100}))
        self.assertIsNone(req.execution.pip_size)
        self.assertIsNone(req.execution.stop_loss_pips)
        self.assertEqual(req.execution.stop_distance, 10.0)
        self.assertEqual(req.execution.take_profit_distance, 100.0)

    def test_conversion_occurs_once(self):
        # A pip request yields the effective price distance exactly once; a
        # second resolution pass must not multiply again.
        req = run_request.parse_request_payload(
            _payload("XAUUSD", {"stop_loss_pips": 10, "take_profit_pips": 10}))
        self.assertAlmostEqual(req.execution.stop_distance, 0.10, places=12)
        again = execution.resolve_distances(
            stop_loss_pips=None, take_profit_pips=None,
            stop_distance=req.execution.stop_distance,
            take_profit_distance=req.execution.take_profit_distance,
            instrument=accounting.INSTRUMENT_CATALOG["XAUUSD"], symbol="XAUUSD")
        self.assertAlmostEqual(again[0], 0.10, places=12)

    def test_explicit_instrument_pip_size(self):
        req = run_request.parse_request_payload({
            "strategy": {"selector": "s"},
            "market": {"symbol": "BTCUSD", "data_source": "cache:x"},
            "execution": {"stop_loss_pips": 50, "take_profit_pips": 100},
            "instrument": {"symbol": "BTCUSD", "quote_currency": "USD",
                           "contract_size": 1.0, "tick_size": 0.01, "pip_size": 1.0},
        })
        self.assertEqual(req.execution.pip_size, 1.0)
        self.assertAlmostEqual(req.execution.stop_distance, 50.0, places=12)


class ValidationTest(unittest.TestCase):
    def test_invalid_pip_values_rejected(self):
        for bad in (0, -5, "abc"):
            with self.assertRaises(run_errors.RunError):
                run_request.parse_request_payload(
                    _payload("XAUUSD", {"stop_loss_pips": bad, "take_profit_pips": 10}))

    def test_unknown_pip_size_fails_clearly(self):
        with self.assertRaises(run_errors.InvalidExecutionError) as ctx:
            run_request.parse_request_payload(
                _payload("NOTREAL", {"stop_loss_pips": 10, "take_profit_pips": 10}))
        self.assertIn("pip size is unknown", str(ctx.exception))

    def test_mixing_pip_and_price_rejected(self):
        with self.assertRaises(run_errors.InvalidExecutionError):
            run_request.parse_request_payload(
                _payload("XAUUSD", {"stop_loss_pips": 10, "take_profit_pips": 10,
                                    "stop_distance": 5}))

    def test_pip_mode_requires_both_sides(self):
        with self.assertRaises(run_errors.InvalidExecutionError):
            run_request.parse_request_payload(
                _payload("XAUUSD", {"stop_loss_pips": 10}))

    def test_pip_size_must_align_with_tick_grid(self):
        with self.assertRaises(run_errors.InvalidExecutionError):
            execution.validate_pip_size(0.003, 0.01)  # 0.3 ticks -> not whole
        self.assertEqual(execution.validate_pip_size(0.01, 0.01), 0.01)


class ArchiveTest(unittest.TestCase):
    def test_pip_run_archives_intent_and_effective(self):
        req = run_request.parse_request_payload(
            _payload("XAUUSD", {"stop_loss_pips": 10, "take_profit_pips": 100}))
        config = run_request.build_run_config(
            req, run_id="run_x", metadata={"name": "S", "version": "1"},
            effective_parameters={}, data_fingerprint="fp", start="s", end="e")
        ex = config["execution"]
        self.assertEqual(ex["stop_loss_pips"], 10)
        self.assertEqual(ex["take_profit_pips"], 100)
        self.assertEqual(ex["pip_size"], 0.01)
        self.assertAlmostEqual(ex["effective_stop_distance"], 0.10, places=12)
        self.assertAlmostEqual(ex["effective_take_profit_distance"], 1.00, places=12)

    def test_legacy_run_archive_unchanged(self):
        req = run_request.parse_request_payload(
            _payload("XAUUSD", {"stop_distance": 10, "take_profit_distance": 100}))
        config = run_request.build_run_config(
            req, run_id="run_y", metadata={"name": "S", "version": "1"},
            effective_parameters={}, data_fingerprint="fp", start="s", end="e")
        self.assertEqual(config["execution"], {"stop_distance": 10.0, "take_profit_distance": 100.0})


class WorkflowConversionTest(unittest.TestCase):
    def test_optimization_pip_grid_uses_shared_conversion(self):
        opt = optimization.OptimizationRequest(
            strategy="candle_direction",
            market=run_request.MarketRequest(symbol="XAUUSD", timeframe="15min", data_source="cache:x"),
            execution=run_request.ExecutionRequest(stop_distance=0.1, take_profit_distance=1.0,
                                                   pip_size=0.01),
            strategy_grid={},
            execution_grid={"stop_loss_pips": [10, 20]},
        )
        combos = optimization.build_combinations(opt, {})
        stops = sorted(c["execution"]["stop_distance"] for c in combos)
        self.assertEqual(stops, [0.1, 0.2])
        # Never a pip key leaks into the engine-facing execution dict.
        for c in combos:
            self.assertNotIn("stop_loss_pips", c["execution"])

    def test_optimization_price_grid_unchanged(self):
        opt = optimization.OptimizationRequest(
            strategy="candle_direction",
            market=run_request.MarketRequest(symbol="XAUUSD", timeframe="15min", data_source="cache:x"),
            execution=run_request.ExecutionRequest(stop_distance=5.0, take_profit_distance=10.0),
            strategy_grid={},
            execution_grid={"stop_distance": [3.0, 5.0]},
        )
        combos = optimization.build_combinations(opt, {})
        self.assertEqual(sorted(c["execution"]["stop_distance"] for c in combos), [3.0, 5.0])

    def test_optimization_rejects_mixed_grid_keys(self):
        opt = optimization.OptimizationRequest(
            strategy="candle_direction",
            market=run_request.MarketRequest(symbol="XAUUSD", timeframe="15min", data_source="cache:x"),
            execution=run_request.ExecutionRequest(stop_distance=5.0, take_profit_distance=10.0),
            strategy_grid={},
            execution_grid={"stop_loss_pips": [10], "stop_distance": [5]},
        )
        with self.assertRaises(run_errors.InvalidOptimizationError):
            optimization.build_combinations(opt, {})

    def test_walk_forward_reuses_optimization_conversion(self):
        import inspect
        from src import walk_forward
        src = inspect.getsource(walk_forward)
        self.assertIn("optimization.build_combinations", src)


if __name__ == "__main__":
    unittest.main()
