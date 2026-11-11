"""Source-timezone metadata: naive wall clocks are localized, not assumed UTC."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import data_engine as de  # noqa: E402


class SourceTimezoneTest(unittest.TestCase):
    def test_naive_wall_clock_uses_source_timezone(self):
        parsed = de.parse_timestamps(
            pd.Series(["2025-01-02 00:00:00"]), timezone_name="America/New_York"
        )
        self.assertEqual(str(parsed.iloc[0]), "2025-01-02 05:00:00+00:00")

    def test_offset_aware_timestamp_wins_over_source_timezone(self):
        parsed = de.parse_timestamps(
            pd.Series(["2025-01-02T00:00:00+02:00"]), timezone_name="America/New_York"
        )
        self.assertEqual(str(parsed.iloc[0]), "2025-01-01 22:00:00+00:00")

    def test_mixed_awareness_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "mixed timezone"):
            de.parse_timestamps(
                pd.Series(["2025-01-02 00:00:00", "2025-01-02T00:00:00Z"])
            )

    def test_dukascopy_without_microseconds_uses_source_timezone(self):
        parsed = de._timestamp_from_strings(
            pd.Series(["2025.01.02"]),
            pd.Series(["00:00:00"]),
            timezone_name="America/New_York",
        )
        self.assertEqual(str(parsed.iloc[0]), "2025-01-02 05:00:00+00:00")

    def test_provider_and_file_overrides(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "source_timezones.json"
            path.write_text(json.dumps({
                "providers": {"BINANCE": "America/New_York"},
                "files": {"BTCUSDT_BINANCE_2025_01_ticks.csv": "Europe/London"},
            }), encoding="utf-8")
            self.assertEqual(
                de.source_timezone_for("OTHER_BINANCE_2025_01_ticks.csv", path=path),
                "America/New_York",
            )
            self.assertEqual(
                de.source_timezone_for("BTCUSDT_BINANCE_2025_01_ticks.csv", path=path),
                "Europe/London",
            )
            self.assertEqual(
                de.source_timezone_for("UNKNOWN_DUKAS_2025_01_ticks.csv", path=path),
                "UTC",
            )

    def test_bad_timezone_fails_loudly(self):
        with self.assertRaisesRegex(ValueError, "unknown source timezone"):
            de.parse_timestamps(pd.Series(["2025-01-02 00:00:00"]), timezone_name="Not/AZone")


if __name__ == "__main__":
    unittest.main()
