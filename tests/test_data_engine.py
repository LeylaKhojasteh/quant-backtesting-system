"""Data Engine v1 (Phase 1) tests.

Uses small deterministic synthetic Dukascopy-format fixtures under
tests/fixtures/data_engine (never real market data). Real-market January golden
parity is covered by a test that skips when data/ or the derived cache is absent.
"""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import data_engine as de  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "data_engine"
JAN_CSV = ROOT / "data" / "XAUUSD_DUKAS_2025_01_ticks.csv"


class FixtureCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name) / "data"
        self.data.mkdir()
        for src in FIXTURES.glob("*.csv"):
            shutil.copy(src, self.data / src.name)
        self.root = Path(self.tmp.name) / "cache"

    def tearDown(self):
        self.tmp.cleanup()

    def _pid(self, month=1):
        return de.PartitionId("DUKAS", "TESTSYM", 2099, month)


class ContractTest(FixtureCase):
    def test_filename_mapping(self):
        m = de.parse_source_filename("TESTSYM_DUKAS_2099_01_ticks.csv")
        self.assertEqual((m["symbol"], m["provider"], m["year"], m["month"], m["kind"]),
                         ("TESTSYM", "DUKAS", 2099, 1, "month"))
        agg = de.parse_source_filename("XAUUSD_DUKAS_ALL_ticks.csv")
        self.assertEqual((agg["symbol"], agg["kind"]), ("XAUUSD", "aggregate"))
        self.assertIsNone(de.parse_source_filename("notes.txt"))

    def test_canonical_chunks_preserve_values_and_order(self):
        frame = next(de.canonical_chunks(FIXTURES / "TESTSYM_DUKAS_2099_01_ticks.csv"))
        self.assertEqual(list(frame.columns), list(de.CANONICAL_COLUMNS))
        self.assertEqual(frame["bid"].tolist(), [100.00, 100.10, 100.20, 100.50, 101.00])
        # equal-timestamp rows keep source order (source_row ascending)
        head = frame[frame["timestamp"] == frame["timestamp"].iloc[0]]
        self.assertEqual(head["bid"].tolist(), [100.00, 100.10])
        self.assertEqual(frame["source_row"].tolist(), [0, 1, 2, 3, 4])

    def test_malformed_schema_rejected(self):
        bad = self.data / "TESTSYM2_DUKAS_2099_03_ticks.csv"
        bad.write_text("1,2\n3,4\n", encoding="utf-8")
        with self.assertRaises(de.MalformedSchemaError):
            list(de.canonical_chunks(bad))


class BuildVerifyTest(FixtureCase):
    def test_build_then_exact_verify_and_metadata(self):
        raw = self.data / "TESTSYM_DUKAS_2099_01_ticks.csv"
        res = de.build_partition(raw, self._pid(1), root=self.root)
        self.assertEqual(res["status"], "BUILT")
        parquet = de.partition_parquet(self.root, self._pid(1))
        meta = de.partition_metadata(self.root, self._pid(1))
        self.assertTrue(parquet.is_file() and meta.is_file())
        import json
        doc = json.loads(meta.read_text(encoding="utf-8"))
        self.assertEqual(doc["rows"], 5)
        self.assertEqual(doc["schema_version"], de.DATA_CACHE_SCHEMA_VERSION)
        self.assertEqual(doc["source_sha256"], de.sha256_file(raw))
        result = de.verify_partition(raw, self._pid(1), root=self.root)
        self.assertTrue(result["row_count_parity"] and result["value_parity"] and result["order_parity"])

    def test_idempotent_cache_hit(self):
        raw = self.data / "TESTSYM_DUKAS_2099_01_ticks.csv"
        de.build_partition(raw, self._pid(1), root=self.root)
        again = de.build_partition(raw, self._pid(1), root=self.root)
        self.assertEqual(again["status"], "CACHE HIT")

    def test_source_change_detected(self):
        raw = self.data / "TESTSYM_DUKAS_2099_01_ticks.csv"
        de.build_partition(raw, self._pid(1), root=self.root)
        raw.write_text(raw.read_text(encoding="utf-8") + "2099.01.31,23:59:59.500,101.1,101.6,0,0\n", encoding="utf-8")
        with self.assertRaises(de.SourceChangedError):
            de.build_partition(raw, self._pid(1), root=self.root)

    def test_atomic_no_temp_left_behind(self):
        raw = self.data / "TESTSYM_DUKAS_2099_01_ticks.csv"
        de.build_partition(raw, self._pid(1), root=self.root)
        leftovers = list(de.partition_dir(self.root, self._pid(1)).glob("*.tmp-*"))
        self.assertEqual(leftovers, [])

    def test_corrupted_partition_fails_verification(self):
        raw = self.data / "TESTSYM_DUKAS_2099_01_ticks.csv"
        de.build_partition(raw, self._pid(1), root=self.root)
        # overwrite the parquet with a valid-but-different frame
        import pandas as pd
        import pyarrow as pa
        import pyarrow.parquet as pq
        frame = next(de.canonical_chunks(raw)).copy()
        frame.loc[0, "bid"] = 999.99
        pq.write_table(pa.Table.from_pandas(frame[list(de.CANONICAL_COLUMNS)], schema=de.ARROW_SCHEMA, preserve_index=False),
                       de.partition_parquet(self.root, self._pid(1)))
        result = de.verify_partition(raw, self._pid(1), root=self.root)
        self.assertFalse(result["value_parity"])


class ReaderTest(FixtureCase):
    def _build(self):
        de.build_all(self.data, self.root)

    def test_partition_pruning(self):
        self._build()
        self.assertEqual(de.select_partitions("TESTSYM", "2099-02-01", "2099-02-28", self.root), ["2099-02"])
        self.assertEqual(de.select_partitions("TESTSYM", "2099-01-10", "2099-02-10", self.root),
                         ["2099-01", "2099-02"])
        self.assertEqual(de.select_partitions("TESTSYM", "2099-01-01", "2099-01-31", self.root), ["2099-01"])

    def test_load_inclusive_end_and_canonical_columns(self):
        self._build()
        frame = de.load_ticks_frame("cache:", symbol="TESTSYM", start="2099-01-01", end="2099-01-31", root=self.root)
        self.assertEqual(list(frame.columns), ["timestamp", "bid", "ask", "bid_volume", "ask_volume"])
        # last row of January included because the external end date is inclusive
        self.assertIn(101.00, frame["bid"].tolist())
        # equal-timestamp order preserved
        eq = frame[frame["timestamp"] == frame["timestamp"].iloc[0]]
        self.assertEqual(eq["bid"].tolist(), [100.00, 100.10])

    def test_range_outside_coverage(self):
        self._build()
        with self.assertRaises(de.CoverageError):
            de.load_ticks_frame("cache:", symbol="TESTSYM", start="2100-01-01", end="2100-02-01", root=self.root)

    def test_missing_partition(self):
        self._build()
        de.partition_parquet(self.root, self._pid(2)).unlink()
        with self.assertRaises(de.PartitionMissingError):
            de.load_ticks_frame("cache:", symbol="TESTSYM", start="2099-02-01", end="2099-02-28", root=self.root)

    def test_cache_fingerprint_deterministic(self):
        self._build()
        a = de.cache_fingerprint("cache:", symbol="TESTSYM", start="2099-01-01", end="2099-01-31", root=self.root)
        b = de.cache_fingerprint("cache:", symbol="TESTSYM", start="2099-01-01", end="2099-01-31", root=self.root)
        self.assertEqual(a, b)
        self.assertTrue(a.startswith("sha256:"))

    def test_catalog_coverage(self):
        self._build()
        cat = de.load_catalog(self.root)["datasets"]
        ds = next(iter(cat.values()))
        self.assertEqual(ds["symbol"], "TESTSYM")
        self.assertEqual(ds["partitions"], 2)
        self.assertEqual(ds["coverage_start"][:10], "2099-01-01")


class CandleCacheTest(FixtureCase):
    def _ticks_built(self):
        de.build_all(self.data, self.root)

    def test_timeframes_match_engine_supported(self):
        from src import run_request
        self.assertEqual(tuple(de.SUPPORTED_CANDLE_TIMEFRAMES), tuple(run_request.SUPPORTED_TIMEFRAME_VALUES))

    def test_build_verify_all_six_timeframes(self):
        self._ticks_built()
        for tf in de.SUPPORTED_CANDLE_TIMEFRAMES:
            r = de.build_candle_partition(self._pid(1), tf, root=self.root)
            self.assertEqual(r["status"], "BUILT")
            v = de.verify_candle_partition(self._pid(1), tf, root=self.root)
            self.assertTrue(v["row_count_parity"] and v["value_parity"], tf)
            cols = de.pq.read_table(de.candle_partition_parquet(self.root, self._pid(1), tf)).column_names
            self.assertEqual(cols, list(de.CANDLE_COLUMNS))

    def test_candle_idempotency(self):
        self._ticks_built()
        de.build_candle_partition(self._pid(1), "15min", root=self.root)
        again = de.build_candle_partition(self._pid(1), "15min", root=self.root)
        self.assertEqual(again["status"], "CACHE HIT")

    def test_candle_invalidation_on_force(self):
        self._ticks_built()
        de.build_candle_partition(self._pid(1), "15min", root=self.root)
        forced = de.build_candle_partition(self._pid(1), "15min", root=self.root, force=True)
        self.assertEqual(forced["status"], "BUILT")

    def test_corrupted_candle_detected(self):
        self._ticks_built()
        de.build_candle_partition(self._pid(1), "15min", root=self.root)
        import pandas as pd
        import pyarrow as pa
        path = de.candle_partition_parquet(self.root, self._pid(1), "15min")
        frame = de.pq.read_table(path).to_pandas()
        frame.loc[0, "open"] = 999.99
        de.pq.write_table(pa.Table.from_pandas(frame[list(de.CANDLE_COLUMNS)], schema=de.CANDLE_ARROW_SCHEMA, preserve_index=False), path)
        v = de.verify_candle_partition(self._pid(1), "15min", root=self.root)
        self.assertFalse(v["value_parity"])

    def test_candle_cache_missing_structured(self):
        self._ticks_built()
        with self.assertRaises(de.PartitionMissingError):
            de.load_candles_frame("cache:", symbol="TESTSYM", timeframe="15min",
                                  start="2099-01-01", end="2099-01-31", root=self.root)

    def test_month_boundary_4h_no_dup_or_missing(self):
        self._ticks_built()
        for pid in (self._pid(1), self._pid(2)):
            de.build_candle_partition(pid, "4h", root=self.root)
        # combined on-the-fly over both months == concatenation of cached months
        t1 = de.pq.read_table(de.partition_parquet(self.root, self._pid(1)), columns=["timestamp", "bid", "ask"]).to_pandas()
        t2 = de.pq.read_table(de.partition_parquet(self.root, self._pid(2)), columns=["timestamp", "bid", "ask"]).to_pandas()
        combined = de.build_candles_from_ticks(pd_concat(t1, t2), "4h")
        c1 = de.pq.read_table(de.candle_partition_parquet(self.root, self._pid(1), "4h")).to_pandas()
        c2 = de.pq.read_table(de.candle_partition_parquet(self.root, self._pid(2), "4h")).to_pandas()
        joined = pd_concat(c1, c2).sort_values("timestamp", kind="stable").reset_index(drop=True)
        self.assertEqual(de._digest_candles(combined), de._digest_candles(joined))
        ts = joined["timestamp"].tolist()
        self.assertEqual(len(ts), len(set(ts)), "no duplicate candle at month boundary")
        self.assertEqual(ts, sorted(ts), "monotonic timestamps")


def pd_concat(a, b):
    import pandas as pd
    return pd.concat([a, b], ignore_index=True)


class LogicalRequestTest(unittest.TestCase):
    def test_dataset_id_derives_cache_source(self):
        from src import run_request
        req = run_request.parse_request_payload({
            "strategy": {"selector": "candle_direction"},
            "market": {"dataset_id": "xauusd_dukas_ticks", "symbol": "XAUUSD",
                       "timeframe": "15min", "start": "2025-01-01", "end": "2025-12-31"},
        })
        self.assertEqual(req.market.dataset_id, "xauusd_dukas_ticks")
        self.assertEqual(req.market.data_source, "cache:xauusd_dukas_ticks")

    def test_missing_dataset_id_and_source_rejected(self):
        from src import run_request, run_errors
        with self.assertRaises(run_errors.InvalidDatasetError):
            run_request.parse_request_payload({
                "strategy": {"selector": "candle_direction"},
                "market": {"symbol": "XAUUSD", "timeframe": "15min"},
            })


@unittest.skipUnless(JAN_CSV.is_file() and de.partition_parquet(de.DEFAULT_CACHE_ROOT, de.PartitionId("DUKAS", "XAUUSD", 2025, 1)).is_file(),
                     "January raw data and derived cache required")
class GoldenJanuaryTest(unittest.TestCase):
    def test_csv_vs_parquet_golden_parity(self):
        result = de.golden_january()
        self.assertTrue(result["parity"])
        self.assertTrue(result["csv_matches_reference"])
        self.assertEqual(result["csv"]["ticks"], 322548)
        self.assertEqual(result["csv"]["trades"], 7)
        self.assertAlmostEqual(result["csv"]["pnl"], -15.82, places=6)


class LineageRoleTest(FixtureCase):
    def _write_inventory(self):
        import json
        entries = de.inventory(self.data)
        de._atomic_write_text(self.root / "raw_inventory.json",
                              json.dumps({"schema_version": de.DATA_CACHE_SCHEMA_VERSION, "files": entries}))

    def _add_aggregate(self):
        (self.data / "TESTSYM_DUKAS_ALL_ticks.csv").write_text(
            (self.data / "TESTSYM_DUKAS_2099_01_ticks.csv").read_text(encoding="utf-8"), encoding="utf-8")

    def test_logical_id_year_independent(self):
        de.build_all(self.data, self.root)
        ds = next(iter(de.load_catalog(self.root)["datasets"].values()))
        self.assertEqual(ds["dataset_id"], "testsym_dukas_ticks")
        self.assertNotIn("2099", ds["dataset_id"])

    def test_aggregate_is_reference_and_excluded(self):
        self._add_aggregate()
        self._write_inventory()
        entries = de.inventory(self.data)
        roles = {e["relative_path"]: e["source_role"] for e in entries}
        self.assertEqual(roles["TESTSYM_DUKAS_2099_01_ticks.csv"], "monthly_partition")
        self.assertEqual(roles["TESTSYM_DUKAS_ALL_ticks.csv"], "aggregate_reference")
        agg = next(e for e in entries if e["source_role"] == "aggregate_reference")
        self.assertFalse(agg["included_in_logical_dataset"])
        self.assertIsNone(agg["derived_partition"])
        # ALL is not a partition and does not change the logical partition set
        names = [p.name for p, _ in de.discover_partitions(self.data)]
        self.assertNotIn("TESTSYM_DUKAS_ALL_ticks.csv", names)
        de.build_all(self.data, self.root)
        ds = next(iter(de.load_catalog(self.root)["datasets"].values()))
        self.assertEqual(ds["partitions"], 2)  # only the two monthly partitions

    def test_all_never_used_and_no_fallback(self):
        self._add_aggregate()
        self._write_inventory()
        de.build_all(self.data, self.root)
        summary = de.logical_summary(self.root, self.data)
        self.assertEqual(summary["aggregate_reference_files"], 1)
        self.assertEqual(summary["authoritative_monthly_partitions"], 2)
        self.assertFalse(summary["aggregate_included_in_logical_total"])
        # deleting a monthly partition must NOT fall back to the aggregate
        de.partition_parquet(self.root, self._pid(2)).unlink()
        with self.assertRaises(de.PartitionMissingError):
            de.load_ticks_frame("cache:", symbol="TESTSYM", start="2099-02-01", end="2099-02-28", root=self.root)

    def test_completeness_is_explicit_not_inferred(self):
        # synthetic 2099 sources span full calendar months but are NOT declared,
        # so they must be 'unknown' (no span/coverage inference).
        de.build_all(self.data, self.root)
        ds = next(iter(de.load_catalog(self.root)["datasets"].values()))
        by_month = {d["month"]: d for d in ds["month_details"]}
        self.assertEqual(by_month["2099-01"]["completeness"], "unknown")
        self.assertEqual(by_month["2099-02"]["completeness"], "unknown")
        # observed coverage metadata is still retained
        self.assertEqual(by_month["2099-01"]["rows"], 5)
        self.assertIn("first_timestamp", by_month["2099-01"])
        self.assertIn("last_timestamp", by_month["2099-01"])


class CompletenessProvenanceTest(unittest.TestCase):
    """Completeness is explicit source provenance, never a coverage heuristic."""

    def test_explicit_registry_values(self):
        for m in range(1, 13):
            self.assertEqual(de.source_completeness(f"XAUUSD_DUKAS_2025_{m:02d}_ticks.csv"), "complete")
        self.assertEqual(de.source_completeness("XAUUSD_DUKAS_2026_01_ticks.csv"), "partial")

    def test_undeclared_source_defaults_to_unknown(self):
        self.assertEqual(de.source_completeness("SOME_FUTURE_DUKAS_2099_07_ticks.csv"), "unknown")
        self.assertEqual(de.source_completeness(None), "unknown")

    def test_high_coverage_source_not_complete_without_metadata(self):
        # A month covering ~60% or ~90% of the calendar must NOT auto-become complete.
        self.assertEqual(de.source_completeness("ZZZ_DUKAS_2099_06_ticks.csv"), "unknown")

    def test_heuristic_removed(self):
        self.assertFalse(hasattr(de, "COMPLETE_COVERAGE_RATIO"), "coverage ratio heuristic must be removed")
        self.assertFalse(hasattr(de, "_month_completeness"), "span-based completeness must be removed")


class RangeContractTest(unittest.TestCase):
    def test_half_open_translation(self):
        import pandas as pd
        start, endx = de.resolve_range("2025-03-17", "2025-05-08")
        self.assertEqual(start, pd.Timestamp("2025-03-17 00:00:00", tz="UTC"))
        self.assertEqual(endx, pd.Timestamp("2025-05-09 00:00:00", tz="UTC"))

    def test_datetime_end_is_already_exclusive(self):
        start, endx = de.resolve_range(None, "2025-05-08 22:00:00")
        import pandas as pd
        self.assertEqual(endx, pd.Timestamp("2025-05-08 22:00:00", tz="UTC"))

    def test_internal_filter_is_half_open(self):
        import pandas as pd
        months = de._months_between(
            pd.Timestamp("2099-02-01", tz="UTC"),
            pd.Timestamp("2099-02-02", tz="UTC"),
            ["2099-01", "2099-02", "2099-03"])
        self.assertEqual(months, ["2099-02"])
        # end-exclusive: a range ending exactly at a month start excludes that month
        months2 = de._months_between(
            pd.Timestamp("2099-01-01", tz="UTC"),
            pd.Timestamp("2099-02-01", tz="UTC"),
            ["2099-01", "2099-02"])
        self.assertEqual(months2, ["2099-01"])


@unittest.skipUnless(JAN_CSV.is_file() and de.catalog_path(de.DEFAULT_CACHE_ROOT).is_file(),
                     "real derived cache required")
class RealLineageTest(unittest.TestCase):
    def test_verified_real_totals_and_roles(self):
        s = de.logical_summary()
        self.assertEqual(s["logical_dataset_id"], "xauusd_dukas_ticks")
        self.assertEqual(s["raw_inventory_files"], 14)
        self.assertEqual(s["authoritative_monthly_partitions"], 13)
        self.assertEqual(s["monthly_2025_partitions"], 12)
        self.assertEqual(s["monthly_2026_partitions"], 1)
        self.assertEqual(s["rows_2025"], 71_413_977)
        self.assertEqual(s["rows_2026"], 7_245)
        self.assertEqual(s["total_logical_rows"], 71_421_222)
        self.assertEqual(s["aggregate_rows"], 71_413_966)
        self.assertFalse(s["aggregate_included_in_logical_total"])
        self.assertEqual(s["aggregate_missing_ticks_vs_monthly_2025"], 11)

    def test_2026_partial_coverage(self):
        ds = next(iter(de.load_catalog()["datasets"].values()))
        by_month = {d["month"]: d for d in ds["month_details"]}
        self.assertEqual(by_month["2026-01"]["completeness"], "partial")
        self.assertEqual(by_month["2026-01"]["rows"], 7_245)
        self.assertIn("2026-01", ds["partial_months"])
        # 2025 is known complete: 12/12 months complete, and the only partial is 2026-01
        complete_2025 = [m for m, d in by_month.items()
                         if m.startswith("2025-") and d["completeness"] == "complete"]
        self.assertEqual(len(complete_2025), 12)
        self.assertEqual(ds["partial_months"], ["2026-01"])

    def test_real_range_pruning_and_no_all(self):
        self.assertEqual(de.select_partitions("XAUUSD", "2025-03-17", "2025-05-08"), ["2025-03", "2025-04", "2025-05"])
        # aggregate is not a partition and cannot satisfy a missing month
        self.assertNotIn("ALL", de.select_partitions("XAUUSD", "2025-01-01", "2026-01-31"))


if __name__ == "__main__":
    unittest.main()
