"""Per-trade size multiplier: scales account math only, never price terms."""

import sys
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import accounting, result_contract  # noqa: E402
from src.accounting import AccountModel  # noqa: E402
from src.instruments import InstrumentSpec  # noqa: E402


def _frame(multiplier=None):
    row = {
        "trade_id": 1, "direction": "LONG",
        "signal_candle_start": "2025-01-02T00:00:00Z",
        "signal_available_time": "2025-01-02T00:00:00Z",
        "entry_time": "2025-01-02T00:00:00Z", "entry_price": 100.0,
        "stop_loss": 95.0, "take_profit": 110.0,
        "exit_time": "2025-01-02T01:00:00Z", "exit_price": 110.0,
        "exit_reason": "TP", "pnl_price": 10.0, "duration_seconds": 3600.0,
        "spread_at_entry": 0.2, "spread_at_exit": 0.2, "mae": -1.0, "mfe": 10.0,
    }
    if multiplier is not None:
        row["size_multiplier"] = multiplier
    return pd.DataFrame([row])


def _econ():
    return (
        InstrumentSpec(
            symbol="XAUUSD", contract_size=100.0, pip_size=0.1, digits=2,
            quote_currency="USD",
        ),
        AccountModel(
            account_currency="USD", starting_balance=10000.0,
            position_size_lots=0.01, commission_per_lot_round_turn=7.0,
        ),
    )


def _ticks(n=6):
    return pd.DataFrame({
        "timestamp": pd.to_datetime(
            [f"2025-01-02T00:0{i}:00Z" for i in range(n)], utc=True),
        "bid": [100.0 + i for i in range(n)],
        "ask": [100.2 + i for i in range(n)],
    })


def _signal(when, **extra):
    row = {
        "signal_candle_start": when,
        "signal_available_time": when,
        "direction": "LONG",
    }
    row.update(extra)
    return pd.DataFrame([row])


class SizeMultiplierTest(unittest.TestCase):
    def test_doubling_scales_account_math_exactly(self):
        instrument, account = _econ()
        base = result_contract.trades_from_engine(_frame())[0]
        doubled = result_contract.trades_from_engine(_frame(2.0))[0]
        # Price terms untouched.
        self.assertEqual(base.pnl_price, doubled.pnl_price)
        g1, c1, n1 = accounting.trade_account_pnl(
            base.pnl_price, instrument, account, base.size_multiplier)
        g2, c2, n2 = accounting.trade_account_pnl(
            doubled.pnl_price, instrument, account, doubled.size_multiplier)
        self.assertAlmostEqual(g2, 2 * g1)
        self.assertAlmostEqual(c2, 2 * c1)
        self.assertAlmostEqual(n2, 2 * n1)

    def test_missing_or_bad_multiplier_stays_one(self):
        self.assertEqual(result_contract.trades_from_engine(_frame())[0].size_multiplier, 1.0)
        for bad in (0.0, -2.0, None, "x"):
            with self.subTest(bad=bad):
                got = result_contract.trades_from_engine(_frame(bad))[0].size_multiplier
                self.assertEqual(got, 1.0)

    def test_engine_signal_column_reaches_account_math(self):
        from src.backtest import run_backtest

        ticks = _ticks()
        signals = _signal(ticks["timestamp"].iloc[0], size_multiplier=2.5)
        trades = run_backtest(ticks, signals, stop_distance=1.0, take_profit_distance=100.0)
        record = result_contract.trades_from_engine(trades)[0]
        self.assertEqual(record.size_multiplier, 2.5)

    def test_external_normalization_keeps_multiplier(self):
        from src.external_strategy import normalize_external_trades

        frame = pd.DataFrame([{
            "direction": "BUY",
            "entry_time": pd.Timestamp("2025-01-02T00:00:00Z"),
            "exit_time": pd.Timestamp("2025-01-02T01:00:00Z"),
            "entry_price": 100.0, "exit_price": 101.0,
            "exit_reason": "EOD", "gross_pnl": 100.0, "size_multiplier": 3.0,
        }])
        row = normalize_external_trades(frame, contract_size=100.0, lots=0.01).iloc[0]
        self.assertEqual(row["size_multiplier"], 3.0)

    def test_engine_rejects_non_positive_multiplier(self):
        from src.backtest import run_backtest

        ticks = _ticks()
        signals = _signal(ticks["timestamp"].iloc[0], size_multiplier=-1.0)
        with self.assertRaises(ValueError):
            run_backtest(ticks, signals)

    def test_report_uses_multiplier(self):
        instrument, account = _econ()
        report = accounting.build_account_report(
            result_contract.trades_from_engine(_frame(3.0)), account, instrument,
        )
        self.assertAlmostEqual(report["trades"][0]["net_account_pnl"], 3 * (10.0 * 100.0 * 0.01 - 7.0 * 0.01))


if __name__ == "__main__":
    unittest.main()
