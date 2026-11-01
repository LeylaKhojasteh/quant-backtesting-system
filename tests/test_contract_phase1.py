"""Phase 1: strategy-owned execution contract.

Covers: closed exit_reason enum with STRATEGY + raw detail, execution_model
defaulting to 'engine' for old archives, contract 1.1 round-trip, and
rejection of unknown exit reasons.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import result_contract as rc  # noqa: E402


def _result(**kwargs):
    payload = {
        "contract_version": "1.1",
        "run_id": "run_phase1",
        "created_at": "2025-01-01T00:00:00Z",
        "strategy": {"name": "S", "version": "1.0"},
        "config": {
            "symbol": "XAUUSD", "timeframe": "15min",
            "start": "2025-01-01T00:00:00Z", "end": "2025-01-02T00:00:00Z",
            "parameters": {},
        },
        "trades": [], "equity": [],
        "metrics": {
            "total_trades": 0, "wins": 0, "losses": 0, "breakeven": 0,
            "win_rate": 0.0, "net_pnl": 0.0, "gross_profit": 0.0,
            "gross_loss": 0.0, "profit_factor": None, "expectancy": 0.0,
            "avg_win": 0.0, "avg_loss": 0.0, "max_drawdown": 0.0,
            "avg_duration_seconds": 0.0,
        },
        "data_quality": {"data_source": "x"},
        "execution": {"engine": "src.backtest"},
    }
    payload.update(kwargs)
    return rc.BacktestResult.from_dict(payload)


class ExitReasonTest(unittest.TestCase):
    def test_engine_tokens_map_without_detail(self):
        for raw, expected in [
            ("TP", "TP"), ("SL", "SL"), ("EOD", "EOD"),
            ("END_OF_DATA", "EOD"), ("end_of_data", "EOD"),
            ("stop_loss", "SL"), ("stop_loss_gap", "SL"),
            ("take_profit", "TP"), ("take_profit_gap", "TP"),
        ]:
            with self.subTest(raw=raw):
                reason, detail = rc.canonicalize_strategy_exit_reason(raw)
                self.assertEqual(reason, expected)
                self.assertIsNone(detail)

    def test_unknown_strategy_reason_becomes_strategy_with_detail(self):
        reason, detail = rc.canonicalize_strategy_exit_reason("opposite_breakout")
        self.assertEqual(reason, "STRATEGY")
        self.assertEqual(detail, "opposite_breakout")
        record = rc.TradeRecord(
            trade_id=1, direction="LONG", signal_candle_start="",
            signal_available_time="", entry_time="", entry_price=1.0,
            stop_loss=None, take_profit=None, exit_time="", exit_price=2.0,
            exit_reason=reason, exit_reason_detail=detail, pnl_price=1.0,
            duration_seconds=1.0, spread_at_entry=0.0, spread_at_exit=0.0,
        )
        from dataclasses import asdict
        result = _result(
            trades=[asdict(record)],
            equity=[{"timestamp": "", "cumulative_pnl": 1.0}],
        )
        self.assertEqual(result.validate(), [])
        self.assertEqual(result.trades[0].exit_reason_detail, "opposite_breakout")

    def test_empty_strategy_reason_rejected(self):
        with self.assertRaises(ValueError):
            rc.canonicalize_strategy_exit_reason("")
        with self.assertRaises(ValueError):
            rc.canonicalize_strategy_exit_reason(None)

    def test_validate_rejects_unknown_reason(self):
        trade = {
            "trade_id": 1, "direction": "LONG", "signal_candle_start": "",
            "signal_available_time": "", "entry_time": "", "entry_price": 1.0,
            "stop_loss": None, "take_profit": None, "exit_time": "",
            "exit_price": 2.0, "exit_reason": "MAGIC", "pnl_price": 1.0,
            "duration_seconds": 1.0, "spread_at_entry": 0.0,
            "spread_at_exit": 0.0,
        }
        errors = _result(trades=[trade]).validate()
        self.assertTrue(any("exit_reason" in e for e in errors))


class ExecutionModelTest(unittest.TestCase):
    def test_legacy_execution_dict_defaults_to_engine(self):
        result = _result()
        self.assertEqual(result.execution.execution_model, "engine")
        self.assertEqual(result.validate(), [])

    def test_strategy_model_round_trips(self):
        result = _result(execution={"engine": "external", "execution_model": "strategy"})
        self.assertEqual(result.execution.execution_model, "strategy")
        back = rc.BacktestResult.from_dict(result.to_dict())
        self.assertEqual(back.execution.execution_model, "strategy")
        self.assertEqual(back.validate(), [])

    def test_invalid_model_rejected(self):
        errors = _result(execution={"execution_model": "manual"}).validate()
        self.assertTrue(any("execution_model" in e for e in errors))

    def test_contract_version_is_1_1(self):
        self.assertEqual(rc.CONTRACT_VERSION, "1.1")


if __name__ == "__main__":
    unittest.main()
