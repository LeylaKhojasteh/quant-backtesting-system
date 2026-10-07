"""Tests for the local HTTP bridge (``src.bridge``).

Starts a real bridge on an ephemeral 127.0.0.1 port and exercises /health,
/strategies and /runs end-to-end using the synthetic tick CSV, so no network
access or git-ignored ``data/`` is required.
"""

import json
import math
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

import pandas as pd

# Python's urllib reads the Windows registry proxy (unlike WinHTTP), which can
# route 127.0.0.1 through a proxy and break these localhost tests. Force direct.
os.environ.setdefault("no_proxy", "127.0.0.1,localhost")
os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")
urllib.request.install_opener(urllib.request.build_opener(urllib.request.ProxyHandler({})))

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import bridge  # noqa: E402
from src import run_backtest  # noqa: E402

STRATEGY_FIXTURES = ROOT / "tests" / "fixtures" / "strategies"
IMPORT_FIXTURES = ROOT / "tests" / "fixtures" / "import"
THRESHOLD = STRATEGY_FIXTURES / "threshold_direction.py"
EMA_SOURCE = (IMPORT_FIXTURES / "ema_cross.py").read_text(encoding="utf-8")
SYNTAX_ERROR = (IMPORT_FIXTURES / "syntax_error.py").read_text(encoding="utf-8")
NOT_A_STRATEGY = (STRATEGY_FIXTURES / "not_a_strategy.py").read_text(encoding="utf-8")
MANIFEST = "adapters-run-manifest.json"


def _write_ticks(path: Path) -> None:
    start = pd.Timestamp("2025-01-02T00:00:00Z")
    rows = []
    for i in range(240):
        t = start + pd.Timedelta(minutes=i)
        price = 2600.0 + 40.0 * math.sin(i / 7.0)
        rows.append({"timestamp": t, "bid": round(price, 2), "ask": round(price + 0.5, 2)})
    pd.DataFrame(rows).to_csv(path, index=False)


class BridgeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.data = self.root / "ticks.csv"
        _write_ticks(self.data)
        self.runs = self.root / "runs"
        self.public = self.root / "public"
        self.datadir = self.root / "data"
        self.datadir.mkdir()
        (self.datadir / "XAUUSD_DUKAS_2025_01_ticks.csv").write_text(
            "timestamp,bid,ask\n", encoding="utf-8"
        )
        (self.datadir / "XAUUSD_DUKAS_ALL_ticks.csv").write_text(
            "timestamp,bid,ask\n", encoding="utf-8"
        )
        # A temp strategy dir so import tests never touch the repo fixtures.
        self.strategy_dir = self.root / "strategies"
        self.strategy_dir.mkdir()
        for name in ("threshold_direction.py", "class_threshold.py", "trend_direction.py", "not_a_strategy.py"):
            shutil.copy(STRATEGY_FIXTURES / name, self.strategy_dir / name)
        self.server = bridge.create_server(
            "127.0.0.1", 0,
            runs=self.runs, public=self.public, strategy_dir=self.strategy_dir,
            data_dir=self.datadir, studies=self.root / "studies",
        )
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.tmp.cleanup()

    def _get(self, path, headers=None):
        req = urllib.request.Request(self.base + path, headers=headers or {}, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8")), dict(resp.headers)
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8")), dict(exc.headers)

    def _post(self, path, payload, headers=None):
        body = json.dumps(payload).encode("utf-8")
        head = {"Content-Type": "application/json"}
        head.update(headers or {})
        req = urllib.request.Request(self.base + path, data=body, headers=head, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def _delete(self, path):
        req = urllib.request.Request(self.base + path, method="DELETE")
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def _get_raw(self, path):
        req = urllib.request.Request(self.base + path, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.status, resp.read(), dict(resp.headers)
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read(), dict(exc.headers)

    def _seed_run(self, run_id="run_0001"):
        self.runs.mkdir(parents=True, exist_ok=True)
        shutil.copytree(ROOT / "tests" / "fixtures" / "run_0001", self.runs / run_id)
        return self.runs / run_id

    def _run_payload(self, *, selector, parameters=None, publish=False, data_source=None):
        return {
            "strategy": {"selector": selector, "parameters": parameters or {}},
            "market": {
                "symbol": "XAUUSD",
                "timeframe": "15min",
                "start": "2025-01-02",
                "end": "2025-01-02",
                "data_source": str(data_source or self.data),
            },
            "execution": {"stop_distance": 5, "take_profit_distance": 10},
            "publish": publish,
        }

    # ---- health + catalog -------------------------------------------------

    def test_health(self):
        status, body, _ = self._get("/health")
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["service"], "quant-backtest-bridge")
        self.assertTrue(body["version"])

    def test_strategy_catalog_includes_candle_direction_and_parameters(self):
        status, body, _ = self._get("/strategies")
        self.assertEqual(status, 200)
        entries = {e["id"]: e for e in body["strategies"]}
        self.assertIn("candle_direction", entries)
        candle = entries["candle_direction"]
        self.assertEqual(candle["name"], "Candle Direction")
        self.assertEqual(candle["parameters"], {})

        threshold = next(e for e in body["strategies"] if e["name"] == "Threshold Direction")
        self.assertEqual(threshold["version"], "test-1.0")
        self.assertEqual(
            threshold["parameters"],
            {
                "threshold": {"type": "float", "default": 0.0},
                "side": {"type": "string", "default": "both"},
                "enabled": {"type": "bool", "default": True},
            },
        )

    def test_dataset_catalog_is_lightweight(self):
        status, body, _ = self._get("/datasets")
        self.assertEqual(status, 200)
        by_id = {d["id"]: d for d in body["datasets"]}
        monthly = by_id["XAUUSD_DUKAS_2025_01_ticks.csv"]
        self.assertEqual(monthly["symbol"], "XAUUSD")
        self.assertEqual(monthly["start"], "2025-01-01")
        self.assertEqual(monthly["end"], "2025-01-31")
        self.assertEqual(monthly["format"], "csv")
        # No deterministic range from an "ALL" filename.
        allfile = by_id["XAUUSD_DUKAS_ALL_ticks.csv"]
        self.assertIsNone(allfile["start"])
        self.assertIsNone(allfile["end"])

    def test_unknown_route_is_structured_404(self):
        status, body, _ = self._get("/nope")
        self.assertEqual(status, 404)
        self.assertEqual(body["error"]["code"], "not_found")

    # ---- runs -------------------------------------------------------------

    def test_run_candle_direction(self):
        status, body = self._post("/runs", self._run_payload(selector="candle_direction"))
        self.assertEqual(status, 200, body)
        self.assertEqual(body["status"], "completed")
        self.assertEqual(body["strategy"]["name"], "Candle Direction")
        archive = Path(body["archive"]["result"])
        report = Path(body["archive"]["report"])
        self.assertTrue(archive.is_file())
        self.assertTrue(report.is_file())
        self.assertFalse(body["published"])
        self.assertIn("total_trades", body["summary"])

    def test_run_generic_strategy_with_publish(self):
        status, body = self._post("/runs", self._run_payload(
            selector=str(THRESHOLD), parameters={"side": "long"}, publish=True,
        ))
        self.assertEqual(status, 200, body)
        self.assertEqual(body["strategy"]["name"], "Threshold Direction")
        self.assertEqual(body["parameters"]["side"], "long")
        self.assertTrue(body["published"])
        published = Path(body["published_file"])
        self.assertTrue(published.is_file())
        manifest = json.loads((self.public / MANIFEST).read_text(encoding="utf-8"))
        self.assertEqual(len(manifest["runs"]), 1)
        self.assertEqual(manifest["runs"][0]["runId"], body["run_id"])

    def test_parameter_override_changes_run(self):
        _, base = self._post("/runs", self._run_payload(selector=str(THRESHOLD)))
        _, long_only = self._post("/runs", self._run_payload(
            selector=str(THRESHOLD), parameters={"side": "long"},
        ))
        self.assertGreater(base["summary"]["signals_received"], 0)
        self.assertLess(long_only["summary"]["signals_received"], base["summary"]["signals_received"])
        # Execution settings are unchanged by a strategy parameter override.
        self.assertEqual(base["execution"], long_only["execution"])

    def test_publish_false_does_not_touch_public(self):
        status, body = self._post("/runs", self._run_payload(selector="candle_direction"))
        self.assertEqual(status, 200, body)
        self.assertFalse(body["published"])
        self.assertFalse(self.public.exists())

    # ---- error contract ---------------------------------------------------

    def test_invalid_parameter_rejected(self):
        status, body = self._post("/runs", self._run_payload(
            selector=str(THRESHOLD), parameters={"nope": 1},
        ))
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["code"], "invalid_parameters")
        self.assertFalse(self.runs.exists())

    def test_wrong_parameter_type_rejected(self):
        status, body = self._post("/runs", self._run_payload(
            selector=str(THRESHOLD), parameters={"threshold": "abc"},
        ))
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["code"], "invalid_parameters")

    def test_unknown_strategy_rejected(self):
        status, body = self._post("/runs", self._run_payload(selector="does_not_exist.py"))
        self.assertEqual(status, 404)
        self.assertEqual(body["error"]["code"], "unknown_strategy")

    def test_invalid_dataset_rejected(self):
        status, body = self._post("/runs", self._run_payload(
            selector="candle_direction", data_source=self.root / "missing.csv",
        ))
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["code"], "invalid_dataset")

    def test_invalid_execution_rejected(self):
        payload = self._run_payload(selector="candle_direction")
        payload["execution"]["stop_distance"] = 0
        status, body = self._post("/runs", payload)
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["code"], "invalid_execution")

    def test_malformed_json_rejected(self):
        req = urllib.request.Request(
            self.base + "/runs", data=b"{not json",
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=10)
            self.fail("expected HTTPError")
        except urllib.error.HTTPError as exc:
            body = json.loads(exc.read().decode("utf-8"))
            self.assertEqual(exc.code, 400)
            self.assertEqual(body["error"]["code"], "invalid_request")

    # ---- boundary ---------------------------------------------------------

    # ---- strategy import --------------------------------------------------

    def _import(self, filename, source):
        return self._post("/strategies/import", {"filename": filename, "source": source})

    def test_import_valid_strategy_and_catalog_refresh(self):
        status, body = self._import("ema_cross.py", EMA_SOURCE)
        self.assertEqual(status, 200, body)
        entry = body["strategy"]
        self.assertEqual(entry["name"], "EMA Cross")
        self.assertEqual(entry["version"], "import-1.0")
        self.assertTrue(entry["id"].endswith("ema_cross.py"))
        self.assertEqual(entry["source"], "file")
        self.assertEqual(
            entry["parameters"],
            {"fast_period": {"type": "int", "default": 3}, "slow_period": {"type": "int", "default": 8}},
        )
        self.assertTrue((self.strategy_dir / "ema_cross.py").is_file())
        _, catalog, _ = self._get("/strategies")
        self.assertIn("EMA Cross", [e["name"] for e in catalog["strategies"]])

    def test_import_duplicate_conflict(self):
        self._import("ema_cross.py", EMA_SOURCE)
        status, body = self._import(
            "ema_cross.py", EMA_SOURCE.replace('"import-1.0"', '"import-2.0"')
        )
        self.assertEqual(status, 409)
        self.assertEqual(body["error"]["code"], "strategy_conflict")
        self.assertIn("import-1.0", (self.strategy_dir / "ema_cross.py").read_text(encoding="utf-8"))

    def test_import_rejects_invalid_files(self):
        before = sorted(p.name for p in self.strategy_dir.iterdir())
        for filename, source in [
            ("broken.py", SYNTAX_ERROR),
            ("no_interface.py", NOT_A_STRATEGY),
            ("../evil.py", EMA_SOURCE),
            ("evil.txt", EMA_SOURCE),
            ("empty.py", ""),
        ]:
            with self.subTest(filename=filename):
                status, body = self._import(filename, source)
                self.assertEqual(status, 400)
                self.assertEqual(body["error"]["code"], "invalid_strategy_file")
        self.assertEqual(sorted(p.name for p in self.strategy_dir.iterdir()), before)

    def test_import_rejects_unknown_fields(self):
        status, body = self._post(
            "/strategies/import", {"filename": "x.py", "source": EMA_SOURCE, "extra": 1}
        )
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["code"], "invalid_strategy_file")

    def test_imported_strategy_runs_through_existing_pipeline(self):
        status, body = self._import("ema_cross.py", EMA_SOURCE)
        self.assertEqual(status, 200, body)
        selector = body["strategy"]["id"]
        run_status, run = self._post("/runs", self._run_payload(
            selector=selector, parameters={"fast_period": 2, "slow_period": 5}, publish=True,
        ))
        self.assertEqual(run_status, 200, run)
        self.assertEqual(run["strategy"]["name"], "EMA Cross")
        self.assertEqual(run["parameters"], {"fast_period": 2, "slow_period": 5})
        archive_dir = Path(run["archive"]["directory"])
        # strategy.py snapshot matches the imported source exactly
        self.assertEqual((archive_dir / "strategy.py").read_text(encoding="utf-8"), EMA_SOURCE)
        self.assertTrue(Path(run["archive"]["report"]).is_file())
        self.assertTrue(run["published"])

    # ---- run library + strategy management --------------------------------

    def test_runs_library_lists_and_detail(self):
        self._seed_run("run_0001")
        status, body, _ = self._get("/runs")
        self.assertEqual(status, 200)
        self.assertEqual([r["run_id"] for r in body["runs"]], ["run_0001"])
        self.assertFalse(body["runs"][0]["published"])
        status, detail, _ = self._get("/runs/run_0001")
        self.assertEqual(status, 200)
        self.assertEqual(detail["run"]["strategy"]["name"], "Candle Direction")
        self.assertIn("parameters", detail["run"])
        self.assertIn("execution", detail["run"])

    def test_run_report_serving_and_missing(self):
        directory = self._seed_run("run_0001")
        status, raw, headers = self._get_raw("/runs/run_0001/report")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers.get("Content-Type", ""))
        self.assertIn(b"run_0001", raw)
        (directory / "report.html").unlink()
        status, body, _ = self._get("/runs/run_0001/report")
        self.assertEqual(status, 404)
        self.assertEqual(body["error"]["code"], "report_unavailable")

    def test_publish_existing_run_is_idempotent(self):
        self._seed_run("run_0001")
        status, body = self._post("/runs/run_0001/publish", {})
        self.assertEqual(status, 200, body)
        self.assertTrue((self.public / "runs-run_0001-result.json").is_file())
        self.assertTrue(body["wrote_manifest"])
        again_status, again = self._post("/runs/run_0001/publish", {})
        self.assertEqual(again_status, 200)
        self.assertFalse(again["wrote_manifest"])
        _, catalog, _ = self._get("/runs")
        self.assertTrue(catalog["runs"][0]["published"])

    def test_delete_run_removes_artifacts(self):
        self._seed_run("run_0001")
        self._post("/runs/run_0001/publish", {})
        status, body = self._delete("/runs/run_0001")
        self.assertEqual(status, 200, body)
        self.assertFalse((self.runs / "run_0001").exists())
        self.assertFalse((self.public / "runs-run_0001-result.json").exists())
        manifest = json.loads((self.public / MANIFEST).read_text(encoding="utf-8"))
        self.assertEqual(manifest["runs"], [])

    def test_delete_run_invalid_and_unknown(self):
        status, body = self._delete("/runs/%2e%2e%2fetc")
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["code"], "invalid_run_id")
        status, body = self._delete("/runs/nope")
        self.assertEqual(status, 404)
        self.assertEqual(body["error"]["code"], "run_not_found")

    def test_delete_imported_strategy_and_protect_builtin(self):
        self._import("ema_cross.py", EMA_SOURCE)
        status, body = self._delete("/strategies/ema_cross.py")
        self.assertEqual(status, 200, body)
        self.assertTrue(body["deleted"])
        self.assertFalse((self.strategy_dir / "ema_cross.py").exists())
        status, body = self._delete("/strategies/candle_direction")
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["code"], "builtin_strategy_protected")
        status, body = self._delete("/strategies/%2e%2e%2foutside.py")
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["code"], "strategy_delete_failed")

    def test_comparisons_endpoint_and_validation(self):
        self._seed_run("run_0001")
        shutil.copytree(ROOT / "tests" / "fixtures" / "run_0001", self.runs / "run_0002")
        result = json.loads((self.runs / "run_0002" / "result.json").read_text(encoding="utf-8"))
        result["run_id"] = "run_0002"
        (self.runs / "run_0002" / "result.json").write_text(json.dumps(result), encoding="utf-8")

        status, body = self._post("/comparisons", {"run_ids": ["run_0001", "run_0002"]})
        self.assertEqual(status, 200, body)
        self.assertEqual(len(body["runs"]), 2)
        self.assertIn("warnings", body)
        self.assertIn("equity", body)

        status, body = self._post("/comparisons", {"run_ids": ["run_0001"]})
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["code"], "invalid_parameters")
        status, body = self._post("/comparisons", {"run_ids": ["run_0001", "missing"]})
        self.assertEqual(status, 404)
        self.assertEqual(body["error"]["code"], "run_not_found")

    def test_optimization_endpoint_and_history(self):
        payload = {
            "strategy": str(self.strategy_dir / "threshold_direction.py"),
            "market": {"symbol": "XAUUSD", "timeframe": "15min", "start": "2025-01-02",
                       "end": "2025-01-02", "data_source": str(self.data)},
            "execution": {"stop_distance": 5, "take_profit_distance": 10},
            "strategy_grid": {"side": ["both", "long"]},
            "execution_grid": {"stop_distance": [3, 5]},
            "objective": {"metric": "net_pnl", "direction": "maximize"},
        }
        status, body = self._post("/optimizations", payload)
        self.assertEqual(status, 200, body)
        study = body["study"]
        self.assertEqual(study["candidate_count"], 4)
        self.assertTrue((self.root / "studies" / study["study_id"] / "study.json").is_file())
        for c in study["candidates"]:
            if c["status"] == "ok":
                self.assertTrue((self.runs / c["run_id"] / "result.json").is_file())

        _, history, _ = self._get("/optimizations")
        self.assertEqual([s["study_id"] for s in history["studies"]], [study["study_id"]])
        status, detail, _ = self._get(f"/optimizations/{study['study_id']}")
        self.assertEqual(status, 200)
        self.assertEqual(detail["study"]["study_id"], study["study_id"])

    def test_optimization_rejections(self):
        base = {
            "strategy": str(self.strategy_dir / "threshold_direction.py"),
            "market": {"symbol": "XAUUSD", "timeframe": "15min", "data_source": str(self.data)},
            "objective": {"metric": "net_pnl", "direction": "maximize"},
        }
        status, body = self._post("/optimizations", {**base, "strategy_grid": {"nope": [1]}})
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["code"], "invalid_optimization")
        status, body = self._post("/optimizations", {**base, "strategy_grid": {"threshold": [float(i) for i in range(150)]}})
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["code"], "candidate_limit_exceeded")
        status, body = self._post("/optimizations", {**base, "strategy_grid": {"side": ["both"]},
                                                     "objective": {"metric": "sharpe", "direction": "maximize"}})
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["code"], "invalid_optimization")

    def test_http_uses_shared_execute_run(self):
        with mock.patch.object(
            run_backtest, "execute_run", wraps=run_backtest.execute_run
        ) as spy:
            status, _ = self._post("/runs", self._run_payload(selector="candle_direction"))
        self.assertEqual(status, 200)
        self.assertEqual(spy.call_count, 1)

    def test_cors_preflight_for_local_origin(self):
        req = urllib.request.Request(
            self.base + "/runs", method="OPTIONS",
            headers={"Origin": "http://127.0.0.1:5173", "Access-Control-Request-Method": "POST"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            self.assertEqual(resp.status, 204)
            self.assertEqual(resp.headers.get("Access-Control-Allow-Origin"), "http://127.0.0.1:5173")

    def test_cors_blocks_non_local_origin(self):
        _, _, headers = self._get("/health", headers={"Origin": "http://evil.example"})
        self.assertIsNone(headers.get("Access-Control-Allow-Origin"))


if __name__ == "__main__":
    unittest.main()
