"""Tests for async real-backtest jobs (v1.0.1 live-run-control).

Reference: the milestone requires one active worker PROCESS per real backtest
(never a thread), worker-owned heartbeat, stop-during-engine semantics, no
partial publication, and Run Library truth for the latest successful run.

Uses the small synthetic tick CSV so no git-ignored ``data/`` is required.
"""

import json
import math
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

import pandas as pd

os.environ.setdefault("no_proxy", "127.0.0.1,localhost")
os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")
urllib.request.install_opener(urllib.request.build_opener(urllib.request.ProxyHandler({})))

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import bridge, run_errors, run_jobs  # noqa: E402

STRATEGY_FIXTURES = ROOT / "tests" / "fixtures" / "strategies"
THRESHOLD = STRATEGY_FIXTURES / "threshold_direction.py"


def _write_ticks(path: Path, minutes: int = 240) -> None:
    start = pd.Timestamp("2025-01-02T00:00:00Z")
    rows = []
    for i in range(minutes):
        t = start + pd.Timedelta(minutes=i)
        price = 2600.0 + 40.0 * math.sin(i / 7.0)
        rows.append({"timestamp": t, "bid": round(price, 2), "ask": round(price + 0.5, 2)})
    pd.DataFrame(rows).to_csv(path, index=False)


def _slow_strategy(path: Path, seconds: int = 25) -> Path:
    target = path / "slow_signal.py"
    target.write_text(
        "import time\nNAME='Slow'\nVERSION='1.0'\nDESCRIPTION=''\nPARAMETERS={}\n"
        "def generate_signals(candles, parameters=None):\n"
        "    import pandas as pd\n"
        f"    time.sleep({seconds})\n"
        "    idx = list(candles.index if hasattr(candles, 'index') else [])[:1]\n"
        "    rows=[{'signal_candle_start': i, 'signal_available_time': i, 'direction': 'LONG'} for i in idx]\n"
        "    return pd.DataFrame(rows, columns=['signal_candle_start','signal_available_time','direction'])\n",
        encoding="utf-8",
    )
    return target


class RunJobsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.data = self.root / "ticks.csv"
        _write_ticks(self.data)
        self.runs = self.root / "runs"
        self.public = self.root / "public"
        self.strategy_dir = self.root / "strategies"
        self.strategy_dir.mkdir()
        shutil.copy(THRESHOLD, self.strategy_dir / "threshold_direction.py")
        self.server = bridge.create_server(
            "127.0.0.1", 0,
            runs=self.runs, public=self.public, strategy_dir=self.strategy_dir,
            data_dir=self.root, studies=self.root / "studies",
            walkforwards=self.root / "walkforwards", montecarlo=self.root / "montecarlo",
        )
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.port}"

    def tearDown(self):
        run_jobs.shutdown()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.tmp.cleanup()

    def _get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _post(self, path, payload):
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(self.base + path, data=body,
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def _payload(self, selector=None, publish=True, data_source=None):
        return {
            "strategy": {"selector": selector or str(self.strategy_dir / "threshold_direction.py"),
                         "parameters": {}},
            "market": {"symbol": "XAUUSD", "timeframe": "15min", "start": "2025-01-02",
                       "end": "2025-01-02", "data_source": str(data_source or self.data)},
            "execution": {"stop_distance": 5, "take_profit_distance": 10},
            "publish": publish,
        }

    def _wait_state(self, job_id, states=("completed", "failed", "stopped"), timeout=90):
        end = time.time() + timeout
        last = {}
        while time.time() < end:
            last = self._get(f"/run-jobs/{job_id}")["job"]
            if last["state"] in states:
                return last
            time.sleep(0.5)
        self.fail(f"job {job_id} did not reach {states}; last={last}")

    # ---- lifecycle ------------------------------------------------------

    def test_run_job_completes_reports_stage_and_heartbeat(self):
        status, body = self._post("/run-jobs", self._payload())
        self.assertEqual(status, 201, body)
        job = body["job"]
        self.assertTrue(job["job_id"].startswith("job_"))
        self.assertIn(job["state"], ("queued", "running"))
        self.assertNotIn("progress", job)
        self.assertNotIn("eta", job)

        mid = None
        for _ in range(30):
            mid = self._get(f"/run-jobs/{job['job_id']}")["job"]
            if mid["heartbeat"] == "alive" or mid["state"] in ("completed", "failed"):
                break
            time.sleep(0.5)
        self.assertIn(mid["heartbeat"], ("alive", "completed", "starting"))

        final = self._wait_state(job["job_id"])
        self.assertEqual(final["state"], "completed")
        self.assertTrue(final["run_id"])
        self.assertIsNotNone(final["elapsed_seconds"])
        stages = [e["stage"] for e in final["events"]]
        self.assertIn("executing_engine", stages)
        self.assertIn("finalizing", stages)
        # No stdout/traceback leaks into the product status.
        for event in final["events"]:
            self.assertNotIn("Traceback", event["event"])
        self.assertTrue((self.runs / final["run_id"] / "result.json").is_file())
        self.assertTrue((self.runs / final["run_id"] / "report.html").is_file())
        self.assertTrue((self.public / f"runs-{final['run_id']}-result.json").is_file())
        # Active slot is released and the Run Library sees the run.
        self.assertIsNone(self._get("/run-jobs/active")["job"])
        self.assertIn(final["run_id"], [r["run_id"] for r in self._get("/runs")["runs"]])
        latest = self._get("/run-jobs/latest")["run"]
        self.assertIsNotNone(latest)
        self.assertEqual(latest["run_id"], final["run_id"])

    def test_duplicate_run_rejected_with_409(self):
        slow = _slow_strategy(self.root)
        first_status, first = self._post("/run-jobs", self._payload(selector=str(slow)))
        self.assertEqual(first_status, 201, first)
        try:
            dup_status, dup = self._post("/run-jobs", self._payload(selector=str(slow)))
            self.assertEqual(dup_status, 409)
            self.assertEqual(dup["error"]["code"], "run_in_progress")
            active = self._get("/run-jobs/active")["job"]
            self.assertEqual(active["job_id"], first["job"]["job_id"])
        finally:
            self._post(f"/run-jobs/{first['job']['job_id']}/stop", {})

    def test_stop_during_run_kills_worker_keeps_bridge_and_cleans_up(self):
        self.runs.mkdir(parents=True, exist_ok=True)
        shutil.copytree(ROOT / "tests" / "fixtures" / "run_0001", self.runs / "run_0001")
        slow = _slow_strategy(self.root)
        _, started = self._post("/run-jobs", self._payload(selector=str(slow)))
        job_id = started["job"]["job_id"]
        time.sleep(3)
        mid = self._get(f"/run-jobs/{job_id}")["job"]
        self.assertIn(mid["state"], ("queued", "running"))
        status, stopped = self._post(f"/run-jobs/{job_id}/stop", {})
        self.assertEqual(status, 200, stopped)
        self.assertEqual(stopped["job"]["state"], "stopped")
        # Bridge stays alive.
        self.assertEqual(self._get("/health")["status"], "ok")
        # No valid-looking archive/report/publication remains for the stopped run.
        stopped_run = stopped["job"]["run_id"]
        if stopped_run:
            self.assertFalse((self.runs / stopped_run).exists())
            self.assertFalse((self.public / f"runs-{stopped_run}-result.json").exists())
        self.assertEqual([r["run_id"] for r in self._get("/runs")["runs"]], ["run_0001"])
        latest = self._get("/run-jobs/latest")["run"]
        self.assertEqual(latest["run_id"], "run_0001")
        self.assertIsNone(self._get("/run-jobs/active")["job"])

    def test_failed_run_leaves_no_partial_archive(self):
        payload = self._payload()
        payload["market"]["data_source"] = str(self.root / "missing.csv")
        status, body = self._post("/run-jobs", payload)
        # Dataset existence is a worker-stage failure: the job is accepted,
        # then fails honestly with cleanup and no partial publication.
        self.assertEqual(status, 201, body)
        job_id = body["job"]["job_id"]
        final = self._wait_state(job_id, states=("failed",))
        self.assertEqual(final["state"], "failed")
        self.assertIsNotNone(final["error"])
        stopped_run = final["run_id"]
        if stopped_run:
            self.assertFalse((self.runs / stopped_run).exists())
            self.assertFalse((self.public / f"runs-{stopped_run}-result.json").exists())
        self.assertEqual(self._get("/runs")["runs"], [])
        self.assertIsNone(self._get("/run-jobs/active")["job"])

    def test_invalid_payload_rejected_synchronously(self):
        payload = self._payload()
        payload["execution"]["stop_distance"] = 0
        status, body = self._post("/run-jobs", payload)
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["code"], "invalid_execution")
        self.assertIsNone(self._get("/run-jobs/active")["job"])

    def test_unknown_job_is_structured_404(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(self.base + "/run-jobs/job_missing", timeout=10)
        self.assertEqual(ctx.exception.code, 404)
        body = json.loads(ctx.exception.read().decode("utf-8"))
        self.assertEqual(body["error"]["code"], "job_not_found")

    def test_heartbeat_comes_from_worker_not_bridge(self):
        _, started = self._post("/run-jobs", self._payload())
        final = self._wait_state(started["job"]["job_id"])
        raw = json.loads((self.runs / "jobs" / started["job"]["job_id"] / "job.json")
                         .read_text(encoding="utf-8"))
        self.assertTrue(raw.get("heartbeat_at"))
        self.assertTrue(raw.get("pid"))
        self.assertEqual(final["heartbeat"], "completed")

    # ---- unit-level -----------------------------------------------------

    def test_heartbeat_status_thresholds(self):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        self.assertEqual(run_jobs.heartbeat_status("running", now), "alive")
        self.assertEqual(run_jobs.heartbeat_status("running", None), "starting")
        self.assertEqual(run_jobs.heartbeat_status("completed", None), "completed")

    def test_cleanup_removes_partial_artifacts(self):
        self.runs.mkdir(parents=True, exist_ok=True)
        self.public.mkdir(parents=True, exist_ok=True)
        target = self.runs / "run_partial"
        target.mkdir()
        (target / "result.json").write_text("{}", encoding="utf-8")
        (self.public / "runs-run_partial-result.json").write_text("{}", encoding="utf-8")
        (self.public / "adapters-run-manifest.json").write_text(
            json.dumps({"version": 1, "runs": [{"runId": "run_partial", "file": "x", "label": "x"}]}),
            encoding="utf-8")
        removed = run_jobs.cleanup_incomplete_run(str(self.runs), str(self.public), "run_partial")
        self.assertTrue(removed["run_dir"])
        self.assertTrue(removed["public_artifact"])
        self.assertTrue(removed["manifest_entry"])
        self.assertFalse(target.exists())

    def test_latest_successful_run_skips_invalid_and_deleted(self):
        self.runs.mkdir(parents=True, exist_ok=True)
        shutil.copytree(ROOT / "tests" / "fixtures" / "run_0001", self.runs / "run_0001")
        (self.runs / "run_broken").mkdir()
        (self.runs / "run_broken" / "result.json").write_text("{\"run_id\": \"run_broken\"}", encoding="utf-8")
        best = run_jobs.latest_successful_run(str(self.runs), str(self.public))
        self.assertEqual(best["run_id"], "run_0001")
        shutil.rmtree(self.runs / "run_0001")
        self.assertIsNone(run_jobs.latest_successful_run(str(self.runs), str(self.public)))

    def test_latest_successful_run_prefers_newest_and_skips_deleted(self):
        """run_new wins while present; run_old wins after run_new is deleted."""
        self.runs.mkdir(parents=True, exist_ok=True)
        shutil.copytree(ROOT / "tests" / "fixtures" / "run_0001", self.runs / "run_old")
        shutil.copytree(ROOT / "tests" / "fixtures" / "run_0001", self.runs / "run_new")
        (self.runs / "run_old" / "result.json").write_text(
            (self.runs / "run_old" / "result.json").read_text(encoding="utf-8")
            .replace('"run_id": "run_0001"', '"run_id": "run_old"'), encoding="utf-8")
        (self.runs / "run_new" / "result.json").write_text(
            (self.runs / "run_new" / "result.json").read_text(encoding="utf-8")
            .replace('"run_id": "run_0001"', '"run_id": "run_new"'), encoding="utf-8")
        os.utime(self.runs / "run_old" / "result.json", (1000000000, 1000000000))
        os.utime(self.runs / "run_new" / "result.json", (1000000100, 1000000100))
        best = run_jobs.latest_successful_run(str(self.runs), str(self.public))
        self.assertEqual(best["run_id"], "run_new")
        shutil.rmtree(self.runs / "run_new")
        fallback = run_jobs.latest_successful_run(str(self.runs), str(self.public))
        self.assertEqual(fallback["run_id"], "run_old")

    def test_terminal_job_states_never_count_as_successful(self):
        """Stopped/failed/incomplete jobs leave no valid archive behind."""
        self.runs.mkdir(parents=True, exist_ok=True)
        self.public.mkdir(parents=True, exist_ok=True)
        shutil.copytree(ROOT / "tests" / "fixtures" / "run_0001", self.runs / "run_0001")
        # Incomplete: result.json without contract fields is not successful.
        (self.runs / "run_half").mkdir()
        (self.runs / "run_half" / "result.json").write_text("{\"run_id\": \"run_half\"}", encoding="utf-8")
        best = run_jobs.latest_successful_run(str(self.runs), str(self.public))
        self.assertEqual(best["run_id"], "run_0001")
        # Cleanup (the stop path) removes every trace of the incomplete run.
        removed = run_jobs.cleanup_incomplete_run(str(self.runs), str(self.public), "run_half")
        self.assertTrue(removed["run_dir"])
        self.assertFalse((self.runs / "run_half").exists())

    def test_active_clears_after_unpolled_completion(self):
        """GET /run-jobs/active must not return a completed job.

        Regression: the worker can finish while nobody polls (closed tab).
        The in-memory record then stays non-terminal and /active keeps
        returning the finished job, blocking boot and new starts.
        """
        _, started = self._post("/run-jobs", self._payload())
        job_id = started["job"]["job_id"]
        final = self._wait_state(job_id)
        self.assertEqual(final["state"], "completed")
        # Fresh bridge view without polling first: active must reconcile
        # against the worker-owned status file and report idle.
        self.assertIsNone(self._get("/run-jobs/active")["job"])
        # And a new job can start immediately (no stale 409).
        status, second = self._post("/run-jobs", self._payload())
        self.assertEqual(status, 201, second)
        try:
            self._wait_state(second["job"]["job_id"])
        finally:
            self._post(f"/run-jobs/{second['job']['job_id']}/stop", {})

    def test_conflict_and_job_not_found_errors(self):
        self.assertEqual(run_errors.RunConflictError.code, "run_in_progress")
        self.assertEqual(run_errors.RunConflictError.http_status, 409)
        self.assertEqual(run_errors.RunJobNotFoundError.code, "job_not_found")
        self.assertEqual(run_errors.RunJobNotFoundError.http_status, 404)


if __name__ == "__main__":
    unittest.main()
