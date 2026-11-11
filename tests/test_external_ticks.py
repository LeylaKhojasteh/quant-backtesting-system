"""Strategy-owned runs get engine-grade execution facts from real ticks."""

import sys
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import external_strategy  # noqa: E402


def _ticks():
    return pd.DataFrame({
        "timestamp": pd.to_datetime(
            [f"2025-01-02T00:0{i}:00Z" for i in range(6)], utc=True),
        "bid": [100.0, 100.5, 101.0, 100.2, 99.8, 100.4],
        "ask": [100.4, 100.9, 101.4, 100.6, 100.2, 100.8],
    })


def _trades(**over):
    row = {
        "direction": "BUY",
        "entry_time": pd.Timestamp("2025-01-02T00:01:00Z"),
        "exit_time": pd.Timestamp("2025-01-02T00:04:00Z"),
        "entry_price": 100.9,
        "exit_price": 100.2,
        "exit_reason": "EOD",
        "gross_pnl": -70.0,
    }
    row.update(over)
    return external_strategy.normalize_external_trades(
        pd.DataFrame([row]), contract_size=100.0, lots=0.01,
    )


class TickEnrichmentTest(unittest.TestCase):
    def test_fill_price_uses_the_correct_side(self):
        ctx = external_strategy.build_context(
            parameters={}, instrument=None, account=None,
            symbol="XAUUSD", timeframe="1min", ticks=_ticks(),
        )
        self.assertAlmostEqual(ctx.fill_price("2025-01-02T00:01:00Z", "LONG"), 100.9)
        self.assertAlmostEqual(ctx.fill_price("2025-01-02T00:01:00Z", "SHORT"), 100.5)
        self.assertIsNone(ctx.fill_price("2025-01-03T00:00:00Z", "LONG"))

    def test_spread_and_excursions_come_from_ticks(self):
        frame = _trades()
        self.assertEqual(frame.iloc[0]["spread_at_entry"], 0.0)
        self.assertIsNone(frame.iloc[0]["mae"])

        enriched = external_strategy.enrich_with_ticks(frame, _ticks())
        row = enriched.iloc[0]
        self.assertAlmostEqual(row["spread_at_entry"], 0.4)
        self.assertAlmostEqual(row["spread_at_exit"], 0.4)
        # LONG entry 100.9; worst bid 99.8 -> MAE -1.1; best bid 101.0 -> MFE 0.1
        self.assertAlmostEqual(row["mae"], -1.1)
        self.assertAlmostEqual(row["mfe"], 0.1)

    def test_short_excursions_use_ask_side(self):
        frame = _trades(
            direction="SELL", entry_price=100.5, exit_price=101.4,
        )
        row = external_strategy.enrich_with_ticks(frame, _ticks()).iloc[0]
        # SHORT entry 100.5; worst ask 101.4 -> MAE -0.9; best ask 100.2 -> MFE 0.3
        self.assertAlmostEqual(row["mae"], -0.9)
        self.assertAlmostEqual(row["mfe"], 0.3)

    def test_no_ticks_leaves_fields_untouched(self):
        frame = _trades()
        same = external_strategy.enrich_with_ticks(frame, None)
        row = same.iloc[0]
        self.assertEqual(row["spread_at_entry"], 0.0)
        self.assertIsNone(row["mae"])


if __name__ == "__main__":
    unittest.main()
