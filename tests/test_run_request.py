"""Tests for the run request contract (``src.run_request``) and the strategy
parameter override mechanism (``src.strategies.base.resolve_parameters``).

Covers: declared defaults, valid overrides, unknown/invalid rejection, typed
CLI parsing, the engine/execution boundary, and the archived config split.
"""

import sys
import unittest
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.run_request import (  # noqa: E402
    BacktestRunRequest,
    ExecutionRequest,
    MarketRequest,
    StrategyRequest,
    build_run_config,
    coerce_parameter,
    parse_parameter_overrides,
)
from src.strategies.base import resolve_parameters  # noqa: E402


class ResolveParametersTest(unittest.TestCase):
    def test_defaults_remain_when_no_override(self):
        self.assertEqual(
            resolve_parameters({"fast_period": 20, "slow_period": 50}, {}),
            {"fast_period": 20, "slow_period": 50},
        )

    def test_valid_override_applied(self):
        self.assertEqual(
            resolve_parameters({"fast_period": 20, "slow_period": 50}, {"fast_period": 10}),
            {"fast_period": 10, "slow_period": 50},
        )

    def test_unknown_override_rejected(self):
        with self.assertRaises(ValueError):
            resolve_parameters({"fast_period": 20}, {"nope": 1})


class CoerceParameterTest(unittest.TestCase):
    def test_int_float_bool_string(self):
        self.assertEqual(coerce_parameter("n", "20", 0), 20)
        self.assertEqual(coerce_parameter("x", "1.5", 0.0), 1.5)
        self.assertIs(coerce_parameter("b", "true", False), True)
        self.assertIs(coerce_parameter("b", "False", True), False)
        self.assertEqual(coerce_parameter("s", "both", "both"), "both")

    def test_malformed_values_rejected(self):
        with self.assertRaises(ValueError):
            coerce_parameter("n", "1.5", 0)  # int expects int
        with self.assertRaises(ValueError):
            coerce_parameter("x", "abc", 0.0)
        with self.assertRaises(ValueError):
            coerce_parameter("b", "maybe", False)


class ParseOverridesTest(unittest.TestCase):
    declared = {"threshold": 0.0, "side": "both", "enabled": True}

    def test_typed_repeatable_overrides(self):
        self.assertEqual(
            parse_parameter_overrides(
                ["threshold=0.5", "side=long", "enabled=false"], self.declared
            ),
            {"threshold": 0.5, "side": "long", "enabled": False},
        )

    def test_malformed_entry_rejected(self):
        with self.assertRaises(ValueError):
            parse_parameter_overrides(["threshold"], self.declared)

    def test_unknown_parameter_rejected(self):
        with self.assertRaises(ValueError):
            parse_parameter_overrides(["nope=1"], self.declared)

    def test_execution_settings_rejected_with_guidance(self):
        with self.assertRaises(ValueError) as ctx:
            parse_parameter_overrides(["stop_distance=5"], self.declared)
        self.assertIn("--stop-distance", str(ctx.exception))
        with self.assertRaises(ValueError) as ctx2:
            parse_parameter_overrides(["take_profit_distance=5"], self.declared)
        self.assertIn("--take-profit-distance", str(ctx2.exception))

    def test_invalid_value_rejected(self):
        with self.assertRaises(ValueError):
            parse_parameter_overrides(["enabled=maybe"], self.declared)


class BuildRunConfigTest(unittest.TestCase):
    def test_separates_strategy_market_execution(self):
        request = BacktestRunRequest(
            strategy=StrategyRequest(
                selector="threshold_direction.py",
                parameters={"threshold": 0.5},
                file="tests/fixtures/strategies/threshold_direction.py",
            ),
            market=MarketRequest(
                symbol="XAUUSD",
                timeframe="15min",
                start="2025-01-02",
                end="2025-01-02",
                data_source="ticks.csv",
            ),
            execution=ExecutionRequest(stop_distance=6.0, take_profit_distance=12.0),
            publish=True,
        )
        config = build_run_config(
            request,
            run_id="run_x",
            metadata={"name": "Threshold Direction", "version": "test-1.0", "description": "d"},
            effective_parameters={"threshold": 0.5, "side": "both", "enabled": True},
            data_fingerprint="sha256:abc",
            start="2025-01-02T00:00:00Z",
            end="2025-01-02T23:59:59Z",
        )
        self.assertEqual(config["run_id"], "run_x")
        self.assertEqual(config["strategy"]["name"], "Threshold Direction")
        self.assertEqual(config["strategy"]["parameters"]["threshold"], 0.5)
        self.assertEqual(config["strategy"]["overrides"], {"threshold": 0.5})
        self.assertEqual(config["market"]["symbol"], "XAUUSD")
        self.assertEqual(config["market"]["timeframe"], "15min")
        self.assertEqual(config["market"]["data_fingerprint"], "sha256:abc")
        self.assertEqual(config["execution"], {"stop_distance": 6.0, "take_profit_distance": 12.0})
        # publish is orchestration-only and never archived.
        self.assertNotIn("publish", config)
        self.assertNotIn("publish", asdict(request)["market"])


class TimeframeContractTest(unittest.TestCase):
    """The timeframe at the request boundary is a controlled, canonical value."""

    def _base(self, timeframe):
        return {
            "strategy": {"selector": "candle_direction"},
            "market": {
                "symbol": "XAUUSD", "data_source": "x.csv",
                "start": "2025-01-02", "end": "2025-01-02", "timeframe": timeframe,
            },
        }

    # UI label -> canonical backend value (no alias collision).
    EXPECTED_MAPPING = (
        ("1min", "1 minute (M1)"),
        ("5min", "5 minutes (M5)"),
        ("15min", "15 minutes (M15)"),
        ("30min", "30 minutes (M30)"),
        ("1h", "1 hour (H1)"),
        ("4h", "4 hours (H4)"),
    )

    def test_ui_label_to_canonical_mapping(self):
        from src import run_request
        self.assertEqual(run_request.SUPPORTED_TIMEFRAMES, self.EXPECTED_MAPPING)
        values = [v for v, _ in run_request.SUPPORTED_TIMEFRAMES]
        self.assertEqual(len(values), len(set(values)), "canonical values must be unique")

    def test_backend_accepts_all_six_canonical_values(self):
        from src import run_request
        for value, _ in self.EXPECTED_MAPPING:
            with self.subTest(value=value):
                req = run_request.parse_request_payload(self._base(value))
                self.assertEqual(req.market.timeframe, value)

    def test_backend_rejects_unsupported_and_aliases(self):
        from src import run_request, run_errors
        for bad in ("44", "abc", "15minutes", "foo", "H4", "4hours", "m15"):
            with self.subTest(bad=bad):
                with self.assertRaises(run_errors.UnsupportedTimeframeError) as ctx:
                    run_request.parse_request_payload(self._base(bad))
                self.assertEqual(ctx.exception.code, "unsupported_timeframe")
                # No raw parser detail leaks.
                self.assertNotIn("Invalid frequency", str(ctx.exception))
                self.assertNotIn("ValueError", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
