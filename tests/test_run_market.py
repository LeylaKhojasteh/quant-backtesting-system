"""Run-scoped real market APIs (v1.0.2) — candles + ticks.

Uses small synthetic Dukascopy fixtures under tests/fixtures/data_engine (never
real market data). Builds a temp derived cache, a temp archived run referencing
that dataset, and exercises the bridge routes end to end.

Guarantees under test: real cache values (no synthesis), exact run
symbol/timeframe resolution, bounded responses, structured errors for bad
runs/missing cache, and that no demo/raw-CSV fallback exists.
"""

import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

os.environ.setdefault("no_proxy", "127.0.0.1,localhost")
os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")
urllib.request.install_opener(urllib.request.build_opener(urllib.request.ProxyHandler({})))

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import bridge, data_engine as de, run_market  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "data_engine"


class RunMarketTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.data = base / "data"
        self.data.mkdir()
        for src in FIXTURES.glob("*.csv"):
            shutil.copy(src, self.data / src.name)
        self.root = base / "cache"
        # Build the derived cache the run will reference.
        for month in (1, 2):
            pid = de.PartitionId("DUKAS", "TESTSYM", 2099, month)
            de.build_partition(self.data / f"TESTSYM_DUKAS_2099_{month:02d}_ticks.csv", pid, root=self.root)
        for tf in de.SUPPORTED_CANDLE_TIMEFRAMES:
            for month in (1, 2):
                de.build_candle_partition(de.PartitionId("DUKAS", "TESTSYM", 2099, month), tf, root=self.root)

        self.runs = base / "runs"
        self.public = base / "public"
        self._make_run("run_ok", start="2099-01-01T00:00:00Z", end="2099-01-31T23:59:59Z")

        # Point the Data Engine at the temp cache for the duration of the test.
        self._orig_cache = de.DEFAULT_CACHE_ROOT
        self._orig_run_market_cache = None
        de.DEFAULT_CACHE_ROOT = self.root
        # run_market calls data_engine functions without a root arg, so the module
        # default above is what governs. Patch the bound defaults via wrappers.
        self.server = bridge.create_server(
            "127.0.0.1", 0, runs=str(self.runs), public=str(self.public),
            strategy_dir=str(self.data), data_dir=str(self.data))
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.port}"

    def tearDown(self):
        de.DEFAULT_CACHE_ROOT = self._orig_cache
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.tmp.cleanup()

    # ---- helpers --------------------------------------------------------

    def _make_run(self, run_id, *, start, end, dataset_id="testsym_dukas_ticks",
                  timeframe="15min", symbol="TESTSYM", requested_start=None, requested_end=None):
        d = self.runs / run_id
        d.mkdir(parents=True, exist_ok=True)
        (d / "config.json").write_text(json.dumps({
            "run_id": run_id, "strategy": "candle_direction", "symbol": symbol,
            "timeframe": timeframe, "start": start, "end": end,
        }), encoding="utf-8")
        config = {"symbol": symbol, "timeframe": timeframe, "start": start, "end": end,
                  "dataset_id": dataset_id}
        if requested_start is not None:
            config["requested_start"] = requested_start
        if requested_end is not None:
            config["requested_end"] = requested_end
        (d / "result.json").write_text(json.dumps({
            "run_id": run_id,
            "created_at": "2099-02-01T00:00:00Z",
            "strategy": {"name": "Test", "version": "1.0"},
            "metrics": {"total_trades": 0, "win_rate": 0.0, "net_pnl": 0.0, "profit_factor": 0.0},
            "config": config,
            "trades": [],
        }), encoding="utf-8")

    def _get(self, path):
        try:
            with urllib.request.urlopen(self.base + path, timeout=60) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            return exc.code, (json.loads(raw) if raw else {})

    # ---- candles --------------------------------------------------------

    def test_candles_route_returns_real_cached_values(self):
        status, body = self._get(
            "/runs/run_ok/candles?start=2099-01-01T00:00:00Z&end=2099-01-31T23:59:59Z&limit=200")
        self.assertEqual(status, 200, body)
        self.assertEqual(body["symbol"], "TESTSYM")
        self.assertEqual(body["timeframe"], "15min")
        self.assertTrue(body["candles"], body)
        # Compare to the canonical candle cache for the SAME window — no synthesis.
        expected = de.load_candles_window(
            "cache:testsym_dukas_ticks", timeframe="15min", symbol="TESTSYM",
            start="2099-01-01T00:00:00Z", end="2099-01-31T23:59:59Z",
            limit=200, root=self.root)
        self.assertEqual(body["count"], len(expected))
        self.assertGreaterEqual(body["count"], 2)
        # Exact per-candle value parity, matched by timestamp.
        import pandas as pd
        by_ts = {pd.Timestamp(r.timestamp): r for r in expected.itertuples(index=False)}
        for candle in body["candles"]:
            row = by_ts[pd.Timestamp(candle["timestamp"])]
            self.assertAlmostEqual(candle["bid_open"], float(row.open), places=6)
            self.assertAlmostEqual(candle["bid_high"], float(row.high), places=6)
            self.assertAlmostEqual(candle["bid_low"], float(row.low), places=6)
            self.assertAlmostEqual(candle["bid_close"], float(row.close), places=6)
            self.assertAlmostEqual(candle["ask_open"], float(row.ask_open), places=6)
            self.assertAlmostEqual(candle["ask_close"], float(row.ask_close), places=6)

    def test_candles_exact_timeframe_and_symbol_from_run(self):
        self._make_run("run_h4", start="2099-01-01T00:00:00Z", end="2099-01-31T23:59:59Z",
                       timeframe="4h")
        status, body = self._get("/runs/run_h4/candles?limit=50")
        self.assertEqual(status, 200, body)
        self.assertEqual(body["timeframe"], "4h")
        self.assertEqual(body["symbol"], "TESTSYM")

    def test_candles_bounded_by_limit(self):
        for limit in (1, 2, 3):
            status, body = self._get(f"/runs/run_ok/candles?limit={limit}")
            self.assertEqual(status, 200, body)
            self.assertLessEqual(len(body["candles"]), limit)

    def test_candles_limit_is_capped(self):
        status, body = self._get("/runs/run_ok/candles?limit=999999")
        self.assertEqual(status, 200, body)
        self.assertLessEqual(len(body["candles"]), de.MAX_CANDLE_WINDOW)

    def test_candles_invalid_run_is_structured_404(self):
        status, body = self._get("/runs/nope/candles")
        self.assertEqual(status, 404)
        self.assertEqual(body["error"]["code"], "run_not_found")

    def test_candles_default_window_nonempty_for_h1_multi_month(self):
        # Regression: a multi-month H1 real run must return a non-empty bounded
        # candle window from the default (no explicit range) request.
        self._make_run("run_h1", start="2099-01-01T00:00:00Z", end="2099-02-28T23:59:59Z",
                       timeframe="1h")
        status, body = self._get("/runs/run_h1/candles")
        self.assertEqual(status, 200, body)
        self.assertEqual(body["timeframe"], "1h")
        self.assertGreater(body["count"], 0, body)
        self.assertLessEqual(body["count"], de.MAX_CANDLE_WINDOW)
        stamps = [c["timestamp"] for c in body["candles"]]
        self.assertEqual(stamps, sorted(stamps))

    def test_requested_range_preferred_over_observed_coverage(self):
        self._make_run("run_req", start="2099-01-05T00:00:03Z", end="2099-02-20T23:59:58Z",
                       timeframe="15min",
                       requested_start="2099-01-01T00:00:00Z", requested_end="2099-02-28T00:00:00Z")
        market = run_market.resolve_run_market(str(self.runs), "run_req", str(self.public))
        self.assertEqual(market["start"], "2099-01-01T00:00:00Z")
        self.assertEqual(market["end"], "2099-02-28T00:00:00Z")
        self.assertEqual(market["observed_start"], "2099-01-05T00:00:03Z")
        self.assertEqual(market["observed_end"], "2099-02-20T23:59:58Z")

    def test_legacy_run_falls_back_to_observed_coverage(self):
        market = run_market.resolve_run_market(str(self.runs), "run_ok", str(self.public))
        self.assertEqual(market["start"], market["observed_start"])
        self.assertEqual(market["end"], market["observed_end"])

    def test_candles_missing_cache_is_structured_error(self):
        # A run whose dataset exists but whose candle cache was never built.
        shutil.rmtree(self.root / "candles")
        status, body = self._get("/runs/run_ok/candles")
        self.assertEqual(status, 400, body)
        self.assertEqual(body["error"]["code"], "data_not_prepared")

    def test_candles_deleted_run_is_404(self):
        shutil.rmtree(self.runs / "run_ok")
        status, body = self._get("/runs/run_ok/candles")
        self.assertEqual(status, 404)
        self.assertEqual(body["error"]["code"], "run_not_found")

    # ---- ticks ----------------------------------------------------------

    def test_ticks_route_returns_real_bid_ask(self):
        status, body = self._get(
            "/runs/run_ok/ticks?start=2099-01-01T00:00:00Z&end=2099-01-01T00:00:02Z&limit=50")
        self.assertEqual(status, 200, body)
        self.assertEqual(body["symbol"], "TESTSYM")
        self.assertTrue(body["ticks"], body)
        for t in body["ticks"]:
            self.assertAlmostEqual(t["spread"], t["ask"] - t["bid"], places=9)
        # Exact fixture values, not synthesized.
        self.assertAlmostEqual(body["ticks"][0]["bid"], 100.00, places=6)
        self.assertAlmostEqual(body["ticks"][0]["ask"], 100.50, places=6)

    def test_ticks_around_window_is_bounded_and_real(self):
        status, body = self._get(
            "/runs/run_ok/ticks?around=2099-01-01T00:00:01Z&before_ms=2000&after_ms=2000&limit=6")
        self.assertEqual(status, 200, body)
        self.assertLessEqual(len(body["ticks"]), 6)
        for t in body["ticks"]:
            self.assertAlmostEqual(t["spread"], t["ask"] - t["bid"], places=9)

    def test_ticks_limit_is_capped(self):
        status, body = self._get(
            "/runs/run_ok/ticks?start=2099-01-01T00:00:00Z&end=2099-01-31T23:59:59Z&limit=999999")
        self.assertEqual(status, 200, body)
        self.assertLessEqual(len(body["ticks"]), de.MAX_TICK_WINDOW)

    def test_ticks_requires_window(self):
        status, body = self._get("/runs/run_ok/ticks")
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["code"], "invalid_market_request")

    def test_ticks_outside_coverage_is_structured_error(self):
        status, body = self._get(
            "/runs/run_ok/ticks?start=2099-05-01T00:00:00Z&end=2099-05-02T00:00:00Z")
        self.assertEqual(status, 400, body)
        self.assertEqual(body["error"]["code"], "data_not_prepared")

    # ---- resolver unit --------------------------------------------------

    def test_resolve_run_market_uses_run_archive(self):
        market = run_market.resolve_run_market(str(self.runs), "run_ok", str(self.public))
        self.assertEqual(market["symbol"], "TESTSYM")
        self.assertEqual(market["timeframe"], "15min")
        self.assertEqual(market["dataset_id"], "testsym_dukas_ticks")
        self.assertEqual(market["source"], "cache:testsym_dukas_ticks")

    def test_no_demo_or_raw_csv_fallback_import(self):
        # Guard: the market module must not import demo machinery or read raw CSVs.
        src = (ROOT / "src" / "run_market.py").read_text(encoding="utf-8")
        self.assertNotIn("canonical_chunks", src)
        self.assertNotIn("read_csv", src)
        self.assertNotIn("import demo", src)
        self.assertNotIn("from src.demo", src)
        self.assertNotIn("mock", src.lower())
        # Only the sanctioned cache readers may appear.
        self.assertIn("data_engine.load_candles_window", src)
        self.assertIn("data_engine.load_ticks_window", src)


if __name__ == "__main__":
    unittest.main()
