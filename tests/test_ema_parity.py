"""EMA parity: standalone script vs Quant strategy-owned run, same candles.

Calls the file's own ``run_backtest`` and its new ``run_strategy`` shim on an
identical candle frame with identical economics, then compares trade
direction/entry/exit prices, gross PnL and the summary.
"""

import importlib.util
import sys
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import external_strategy  # noqa: E402

EMA = ROOT / "strategies" / "ema_9_20_backtest.py"


def _load_ema():
    spec = importlib.util.spec_from_file_location("ema_parity_mod", str(EMA))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _candles(n=120):
    import datetime as _dt

    base = _dt.datetime(2025, 1, 2, tzinfo=_dt.timezone.utc)
    index = pd.DatetimeIndex(
        [base + _dt.timedelta(minutes=i) for i in range(n)], tz="UTC"
    )
    import math

    closes = [100.0 + 3.0 * math.sin(i / 6.0) + 0.02 * i for i in range(n)]
    opens = [closes[0]] + closes[:-1]
    return pd.DataFrame(
        {
            "open": opens,
            "high": [c + 0.3 for c in closes],
            "low": [c - 0.3 for c in closes],
            "close": closes,
        },
        index=index,
    )


class _Instrument:
    contract_size = 100.0


class _Account:
    position_size_lots = 0.01
    starting_balance = 10000.0


class EmaParityTest(unittest.TestCase):
    def test_shim_matches_standalone_call(self):
        module = _load_ema()
        candles = _candles()
        frame = candles.reset_index().rename(columns={"index": "time"})
        lots, size, balance = 0.01, 100.0, 10000.0
        expected, _, summary = module.run_backtest(
            df=frame, lots=lots, contract_size=size, spread=0.2,
            slippage=0.05, commission_per_lot_side=3.5,
            initial_balance=balance, warmup_bars=30,
        )
        data = external_strategy.StrategyData(
            candles=candles, symbol="XAUUSD", timeframe="15min",
            start="", end="",
        )
        ctx = external_strategy.build_context(
            parameters={
                "warmup_bars": 30, "spread": 0.2, "slippage": 0.05,
                "commission_per_lot_side": 3.5,
            },
            instrument=_Instrument(), account=_Account(),
            symbol="XAUUSD", timeframe="15min", seed=0, ticks=None,
        )
        output = module.run_strategy(data, ctx)
        got = output[0] if isinstance(output, tuple) else output["trades"]
        self.assertEqual(len(got), len(expected))
        for key in ("direction", "entry_price", "exit_price", "exit_reason"):
            self.assertEqual(
                list(got[key].astype(str)), list(expected[key].astype(str)),
                msg=f"column {key} differs",
            )
        for key in ("gross_pnl",):
            for a, b in zip(got[key], expected[key]):
                self.assertAlmostEqual(float(a), float(b), places=9)

    def test_cli_standalone_logic_untouched(self):
        module = _load_ema()
        self.assertTrue(callable(module.main))
        self.assertTrue(callable(module.load_ohlc))
        self.assertEqual(module.NAME, "EMA 9/20 Cross")
        self.assertEqual(
            module.PARAMETERS,
            {"warmup_bars": 200, "spread": 0.0, "slippage": 0.0,
             "commission_per_lot_side": 0.0},
        )


if __name__ == "__main__":
    unittest.main()
