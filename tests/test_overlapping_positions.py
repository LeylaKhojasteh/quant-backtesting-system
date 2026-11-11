"""Overlapping strategy-owned trades: allowed by default, cap only if declared."""

import sys
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import external_strategy  # noqa: E402


def _trade(entry, exit_, direction="BUY", price=100.0, pnl=10.0):
    return {
        "direction": direction,
        "entry_time": pd.Timestamp(entry, tz="UTC"),
        "exit_time": pd.Timestamp(exit_, tz="UTC"),
        "entry_price": price,
        "exit_price": price + 1,
        "exit_reason": "EOD",
        "gross_pnl": pnl,
    }


def _frame(*rows):
    return external_strategy.normalize_external_trades(
        pd.DataFrame(rows), contract_size=100.0, lots=0.01,
    )


class OverlapTest(unittest.TestCase):
    def test_overlapping_trades_are_valid_without_a_cap(self):
        frame = _frame(
            _trade("2025-01-02T00:00:00Z", "2025-01-02T02:00:00Z"),
            _trade("2025-01-02T01:00:00Z", "2025-01-02T03:00:00Z"),
            _trade("2025-01-02T01:30:00Z", "2025-01-02T04:00:00Z"),
        )
        # No system default: nothing enforced when the strategy is silent.
        external_strategy.validate_external_trades(
            frame, range_start="2025-01-02T00:00:00Z", range_end="2025-01-02T05:00:00Z",
        )
        self.assertEqual(external_strategy.max_concurrent_positions(frame), 3)

    def test_same_tick_exit_and_entry_are_not_concurrent(self):
        frame = _frame(
            _trade("2025-01-02T00:00:00Z", "2025-01-02T01:00:00Z"),
            _trade("2025-01-02T01:00:00Z", "2025-01-02T02:00:00Z"),
        )
        self.assertEqual(external_strategy.max_concurrent_positions(frame), 1)

    def test_declared_cap_is_enforced_and_measured(self):
        frame = _frame(
            _trade("2025-01-02T00:00:00Z", "2025-01-02T02:00:00Z"),
            _trade("2025-01-02T01:00:00Z", "2025-01-02T03:00:00Z"),
        )
        with self.assertRaises(ValueError) as ctx:
            external_strategy.validate_external_trades(
                frame, range_start=None, range_end=None, max_concurrent=1,
            )
        self.assertIn("max_concurrent=1", str(ctx.exception))

    def test_entries_must_still_be_chronological(self):
        frame = _frame(
            _trade("2025-01-02T02:00:00Z", "2025-01-02T03:00:00Z"),
            _trade("2025-01-02T01:00:00Z", "2025-01-02T04:00:00Z"),
        )
        with self.assertRaises(ValueError):
            external_strategy.validate_external_trades(
                frame, range_start=None, range_end=None,
            )


if __name__ == "__main__":
    unittest.main()
