"""Phase 3/4: external adapter + single shared runner pipeline."""

import sys
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import (  # noqa: E402
    external_strategy,
    optimization,
    run_request,
    walk_forward,
)
from src.run_errors import InvalidOptimizationError, InvalidWalkForwardError  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "strategies"


def _candles(n=6):
    index = pd.to_datetime(
        [f"2025-01-02T00:{i:02d}:00Z" for i in range(n)], utc=True
    )
    return pd.DataFrame(
        {
            "open": [100.0 + i for i in range(n)],
            "high": [100.5 + i for i in range(n)],
            "low": [99.5 + i for i in range(n)],
            "close": [100.2 + i for i in range(n)],
        },
        index=index,
    )


def _ticks(n=6):
    stamps = pd.to_datetime(
        [f"2025-01-02T00:{i:02d}:00Z" for i in range(n)], utc=True
    )
    return pd.DataFrame(
        {"timestamp": stamps,
         "bid": [100.0 + i for i in range(n)],
         "ask": [100.2 + i for i in range(n)]}
    )


class _Instrument:
    contract_size = 100.0


class _Account:
    position_size_lots = 0.01
    starting_balance = 10000.0


class ExternalAdapterTest(unittest.TestCase):
    def test_normalize_ema_shaped_trades(self):
        candles = _candles()
        trades = pd.DataFrame([{
            "direction": "SELL", "entry_time": candles.index[2],
            "exit_time": candles.index[4], "entry_price": 102.2,
            "exit_price": 100.2, "exit_reason": "opposite_ema_cross",
            "gross_pnl": 200.0, "commission": 7.0,
        }])
        norm = external_strategy.normalize_external_trades(
            trades, contract_size=100.0, lots=0.01,
        )
        row = norm.iloc[0]
        self.assertEqual(row["direction"], "SHORT")
        self.assertEqual(row["exit_reason"], "STRATEGY")
        self.assertEqual(row["_exit_reason_detail"], "opposite_ema_cross")
        # Price move only: 200 / (100 * 0.01) = 200. Commission excluded.
        self.assertEqual(row["pnl_price"], 200.0)
        self.assertIsNone(row["stop_loss"])
        self.assertIsNone(row["take_profit"])
        self.assertIsNone(row["mae"])
        self.assertIsNone(row["mfe"])

    def test_missing_columns_and_bad_direction_rejected(self):
        candles = _candles()
        bad = pd.DataFrame([{"direction": "HOLD"}])
        with self.assertRaises(ValueError):
            external_strategy.normalize_external_trades(
                bad, contract_size=100.0, lots=0.01,
            )

    def test_range_validation_rejects_early_entry(self):
        norm = external_strategy.normalize_external_trades(
            pd.DataFrame([{
                "direction": "BUY", "entry_time": _candles().index[0],
                "exit_time": _candles().index[1], "entry_price": 1.0,
                "exit_price": 2.0, "exit_reason": "EOD", "gross_pnl": 1.0,
            }]),
            contract_size=100.0, lots=0.01,
        )
        with self.assertRaises(ValueError):
            external_strategy.validate_external_trades(
                norm, range_start="2025-06-01", range_end="2025-06-02",
            )

    def test_ctx_carries_parameters_and_run_engine(self):
        ctx = external_strategy.build_context(
            parameters={"a": 1}, instrument=_Instrument(), account=_Account(),
            symbol="XAUUSD", timeframe="15min", seed=3, ticks=_ticks(),
        )
        self.assertEqual(ctx.parameters, {"a": 1})
        self.assertEqual(ctx.seed, 3)
        self.assertTrue(callable(ctx.run_engine))
        trades_df = ctx.run_engine(pd.DataFrame(
            columns=["signal_candle_start", "signal_available_time", "direction"]
        ))
        self.assertTrue(trades_df.empty)


class _External:
    kind = "external"

    def __init__(self, fn):
        self._fn = fn

    def run_strategy(self, data, ctx):
        return self._fn(data, ctx)


class StudyGateTest(unittest.TestCase):
    def test_optimization_rejects_external(self):
        from unittest import mock

        with mock.patch(
            "src.run_backtest.resolve_strategy",
            return_value=(_External(lambda d, c: pd.DataFrame()), None),
        ):
            request = optimization.OptimizationRequest(
                strategy="x",
                market=run_request.MarketRequest(
                    symbol="XAUUSD", timeframe="15min", start="2025-01-01",
                    end="2025-01-02", data_source="x", dataset_id=None,
                ),
                execution=run_request.ExecutionRequest(),
                objective=optimization.Objective(metric="net_pnl", direction="maximize"),
            )
            with self.assertRaises(InvalidOptimizationError):
                optimization.run_study(request, runs="runs", studies="studies")

    def test_walk_forward_rejects_external(self):
        from unittest import mock

        with mock.patch(
            "src.run_backtest.resolve_strategy",
            return_value=(_External(lambda d, c: pd.DataFrame()), None),
        ):
            request = walk_forward.WalkForwardRequest(
                strategy="x",
                market=run_request.MarketRequest(
                    symbol="XAUUSD", timeframe="15min", start="2025-01-01",
                    end="2025-03-01", data_source="x", dataset_id=None,
                ),
                execution=run_request.ExecutionRequest(),
                window=walk_forward.WindowConfig(
                    train_days=30, test_days=7, step_days=7,
                ),
            )
            with self.assertRaises(InvalidWalkForwardError):
                walk_forward.run_walk_forward(request, runs="runs")


if __name__ == "__main__":
    unittest.main()
