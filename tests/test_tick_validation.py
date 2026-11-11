"""Read-only tick validation: report-only vs fatal, never mutating data."""

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import run_backtest, run_errors, run_request  # noqa: E402
from src.tick_validation import validate_ticks  # noqa: E402


def _frame(times, bids, asks) -> pd.DataFrame:
    return pd.DataFrame({
        "timestamp": pd.to_datetime(times, utc=True),
        "bid": bids,
        "ask": asks,
    })


class TickValidationTest(unittest.TestCase):
    def test_gap_is_reported_without_stopping_or_filling(self):
        frame = _frame(
            ["2025-01-02T00:00:00Z", "2025-01-02T00:01:00Z", "2025-01-02T00:10:00Z"],
            [100.0, 100.0, 100.0],
            [100.5, 100.5, 100.5],
        )
        before = list(frame["timestamp"])
        report = validate_ticks(frame)
        self.assertFalse(report.fatal)
        self.assertEqual(report.validation["gap_count"], 2)
        self.assertEqual(report.validation["gap_max_seconds"], 540.0)
        self.assertTrue(any("inter-tick intervals reported" in w for w in report.warnings))
        self.assertEqual(list(frame["timestamp"]), before)
        self.assertEqual(len(report.samples["gap"]), 2)
        self.assertEqual(report.samples["gap"][0]["seconds"], 540.0)

    def test_duplicate_timestamp_with_different_quotes_is_kept(self):
        frame = _frame(
            ["2025-01-02T00:00:00Z", "2025-01-02T00:00:00Z"],
            [100.0, 100.2],
            [100.5, 100.7],
        )
        before = frame.copy(deep=True)
        report = validate_ticks(frame)
        self.assertFalse(report.fatal)
        self.assertEqual(report.validation["duplicate_timestamp_groups"], 1)
        self.assertEqual(report.validation["duplicate_timestamp_rows"], 2)
        self.assertTrue(any("duplicate timestamps kept" in w for w in report.warnings))
        pd.testing.assert_frame_equal(frame, before)

    def test_identical_rows_are_counted_not_removed(self):
        frame = _frame(
            ["2025-01-02T00:00:00Z", "2025-01-02T00:00:00Z"],
            [100.0, 100.0],
            [100.5, 100.5],
        )
        report = validate_ticks(frame)
        self.assertFalse(report.fatal)
        self.assertEqual(report.validation["identical_row_duplicates"], 1)
        self.assertEqual(len(frame), 2)
        self.assertTrue(any("identical duplicate rows" in w for w in report.warnings))

    def test_unsorted_timestamps_are_reported_and_order_is_preserved(self):
        frame = _frame(
            ["2025-01-02T00:02:00Z", "2025-01-02T00:01:00Z", "2025-01-02T00:03:00Z"],
            [100.0, 100.0, 100.0],
            [100.5, 100.5, 100.5],
        )
        before = list(frame["timestamp"])
        report = validate_ticks(frame)
        self.assertFalse(report.fatal)
        self.assertGreaterEqual(report.validation["unsorted_pairs"], 1)
        self.assertTrue(any("unsorted timestamps" in w for w in report.warnings))
        self.assertEqual(list(frame["timestamp"]), before)

    def test_non_finite_prices_are_fatal_and_keep_source_frame(self):
        frame = _frame(
            ["2025-01-02T00:00:00Z", "2025-01-02T00:01:00Z"],
            [100.0, float("nan")],
            [100.5, float("inf")],
        )
        before = frame.copy(deep=True)
        report = validate_ticks(frame)
        self.assertTrue(report.fatal)
        self.assertFalse(report.validation["ticks_finite"])
        self.assertEqual(report.validation["non_finite_bid"], 1)
        self.assertEqual(report.validation["non_finite_ask"], 1)
        self.assertTrue(any("non-finite bid/ask" in e for e in report.errors))
        pd.testing.assert_frame_equal(frame, before)

    def test_nat_timestamp_is_fatal(self):
        frame = _frame(["2025-01-02T00:00:00Z"], [100.0], [100.5])
        frame.loc[0, "timestamp"] = pd.NaT
        report = validate_ticks(frame)
        self.assertTrue(report.fatal)
        self.assertFalse(report.validation["timestamps_valid"])
        self.assertEqual(report.validation["invalid_timestamps"], 1)

    def test_bid_greater_than_ask_is_fatal(self):
        frame = _frame(["2025-01-02T00:00:00Z"], [101.0], [100.0])
        report = validate_ticks(frame)
        self.assertTrue(report.fatal)
        self.assertFalse(report.validation["bid_le_ask"])
        self.assertEqual(report.validation["bid_gt_ask_count"], 1)
        self.assertTrue(any("bid > ask" in e for e in report.errors))

    def test_validation_never_changes_raw_file_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ticks.csv"
            path.write_text(
                "timestamp,bid,ask\n"
                "2025-01-02T00:00:00Z,100,100.5\n"
                "2025-01-02T00:01:00Z,100.1,100.6\n",
                encoding="utf-8",
            )
            before = hashlib.sha256(path.read_bytes()).hexdigest()
            frame = run_backtest._load_ticks(path)
            validate_ticks(frame)
            after = hashlib.sha256(path.read_bytes()).hexdigest()
            self.assertEqual(before, after)

    def test_nonfatal_validation_is_archived_and_rendered_in_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "ticks.csv"
            start = pd.Timestamp("2025-01-02T00:00:00Z")
            rows = []
            for i in range(60):
                t = start + pd.Timedelta(minutes=i)
                price = 100.0 + (i % 15)
                rows.append({"timestamp": t, "bid": price, "ask": price + 0.5})
            rows.insert(31, dict(rows[30]))
            pd.DataFrame(rows).to_csv(path, index=False)
            request = run_request.parse_request_payload({
                "strategy": {"selector": "candle_direction"},
                "market": {
                    "symbol": "XAUUSD",
                    "timeframe": "15min",
                    "start": "2025-01-02",
                    "end": "2025-01-02",
                    "data_source": str(path),
                },
                "execution": {"stop_distance": 5, "take_profit_distance": 10},
            })
            info = run_backtest.execute_run(
                request, out=str(root / "runs"), report=True,
                public=str(root / "public"),
            )
            result = info["result"].to_dict()
            self.assertEqual(
                result["data_quality"]["validation"]["duplicate_timestamp_rows"], 2
            )
            self.assertTrue(any(
                "duplicate timestamps kept" in w
                for w in result["data_quality"]["warnings"]
            ))
            html = (Path(info["directory"]) / "report.html").read_text(encoding="utf-8")
            self.assertIn("duplicate timestamps kept", html)

    def test_fatal_validation_stops_before_engine(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "ticks.csv"
            rows = []
            start = pd.Timestamp("2025-01-02T00:00:00Z")
            for i in range(240):
                t = start + pd.Timedelta(minutes=i)
                bid = 100.0 + (i % 10)
                ask = bid + 0.5
                if i == 120:
                    bid = float("nan")
                rows.append({"timestamp": t, "bid": bid, "ask": ask})
            pd.DataFrame(rows).to_csv(path, index=False)
            request = run_request.parse_request_payload({
                "strategy": {"selector": "candle_direction"},
                "market": {
                    "symbol": "XAUUSD",
                    "timeframe": "15min",
                    "start": "2025-01-02",
                    "end": "2025-01-02",
                    "data_source": str(path),
                },
                "execution": {"stop_distance": 5, "take_profit_distance": 10},
            })
            with mock.patch.object(run_backtest.engine, "run_backtest") as engine:
                with self.assertRaises(run_errors.InvalidDatasetError):
                    run_backtest.execute_run(
                        request, out=str(root / "runs"), report=False,
                        public=str(root / "public"),
                    )
                engine.assert_not_called()
            self.assertEqual(list((root / "runs").glob("run_*")) if (root / "runs").is_dir() else [], [])


if __name__ == "__main__":
    unittest.main()
