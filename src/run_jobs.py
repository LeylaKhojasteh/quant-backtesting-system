"""Async real-backtest jobs: Bridge -> Run Job Registry -> OS worker process.

Reference: v1.0.1 live-run-control milestone. Each real backtest executes in
a separate OS *process* (never a thread) that calls the existing
:func:`src.run_backtest.execute_run`; the bridge and UI stay alive
independently of it. No Celery, Redis, DB, WebSockets, or queues.

Progress and heartbeat travel through a worker-owned status file
(``runs/jobs/<job_id>/job.json``); the bridge only reads it, so a browser
reload reconnects to the same job from the same HTTP process.

Job ``state`` (queued/running/stopping/stopped/completed/failed) is separate
from ``stage`` (resolving_dataset, loading_ticks, loading_candles,
generating_signals, executing_engine, building_result, writing_archive,
generating_report, publishing). Stages are real runner milestones reported by
the worker wrapper; no percentage, ETA, or tick-level progress is fabricated.

Stop semantics: mark ``stopping``, terminate the worker OS process, wait
briefly, escalate only as a fallback. The bridge and frontend survive.
A stopped run is never published and leaves no valid-looking archive.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

TERMINAL_STATES = ("completed", "failed", "stopped")
ACTIVE_STATES = ("queued", "running", "stopping")

STAGE_LABELS = {
    "queued": "Queued",
    "resolving_dataset": "Resolving dataset",
    "loading_ticks": "Loading ticks",
    "loading_candles": "Loading candles",
    "generating_signals": "Generating signals",
    "executing_engine": "Engine execution",
    "building_result": "Building result",
    "writing_archive": "Writing archive",
    "generating_report": "Generating report",
    "publishing": "Publishing",
    "finalizing": "Finalizing",
}

# Heartbeat fresher than this means the worker is alive. It is written by the
# worker process wrapper (including a watchdog thread), never by the bridge.
HEARTBEAT_FRESH_SECONDS = 10.0
HEARTBEAT_STALE_SECONDS = 30.0
_STOP_GRACE_SECONDS = 3.0
_MAX_EVENTS = 200


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class JobRecord:
    """In-memory registry entry. Truth on disk lives in job.json."""

    job_id: str
    state: str = "queued"
    run_id: str | None = None
    pid: int | None = None
    created_at: str = field(default_factory=_utcnow)
    finished_at: str | None = None
    exit_code: int | None = None
    error: str | None = None
    events: list = field(default_factory=list)


class JobRegistry:
    """Owns at most one active real-backtest job. Thread-safe for the bridge."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, JobRecord] = {}
        self._active_job_id: str | None = None
        self._proc: subprocess.Popen | None = None
        self._runs_dir: str | None = None
        self._public_dir: str | None = None

    # ------------------------------------------------------------------
    # configuration + introspection
    # ------------------------------------------------------------------
    def configure(self, *, runs_dir: str, public_dir: str) -> None:
        with self._lock:
            self._runs_dir = runs_dir
            self._public_dir = public_dir

    def job_dir(self, job_id: str) -> Path:
        if self._runs_dir is None:  # pragma: no cover - always configured via bridge
            raise RuntimeError("job registry is not configured")
        return Path(self._runs_dir) / "jobs" / job_id

    def status_path(self, job_id: str) -> Path:
        return self.job_dir(job_id) / "job.json"

    def _read_status(self, job_id: str) -> dict | None:
        try:
            return json.loads(self.status_path(job_id).read_text(encoding="utf-8"))
        except OSError:
            return None

    def active_job_id(self) -> str | None:
        with self._lock:
            active = self._active_job_id
            if active is None:
                return None
            record = self._jobs.get(active)
            if record is None or record.state in TERMINAL_STATES:
                self._active_job_id = None
                return None
            return active

    def get(self, job_id: str) -> JobRecord | None:
        with self._lock:
            return self._jobs.get(job_id)

    def _append_event_locked(self, job_id: str, stage: str, event: str, state: str | None = None) -> None:
        path = self.status_path(job_id)
        try:
            status = json.loads(path.read_text(encoding="utf-8"))
        except OSError:
            return
        events = list(status.get("events", []))
        events.append({"t": _utcnow(), "stage": stage, "event": event})
        status["events"] = events[-_MAX_EVENTS:]
        if state is not None:
            status["state"] = state
        status["stage"] = stage
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(status, indent=2), encoding="utf-8")
        os.replace(tmp, path)

    def _status_run_id_locked(self, job_id: str) -> str | None:
        try:
            status = json.loads(self.status_path(job_id).read_text(encoding="utf-8"))
        except OSError:
            return None
        return status.get("run_id")

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    def start(self, payload: dict, *, runs_dir: str, public_dir: str,
              strategy_dir: str, timeout: float = 600.0) -> JobRecord:
        """Validate, reserve the single active slot, and spawn the worker."""
        from src import run_request

        with self._lock:
            if self._active_job_id is not None:
                record = self._jobs.get(self._active_job_id)
                if record is not None and record.state in ACTIVE_STATES:
                    from src import run_errors
                    raise run_errors.RunConflictError(
                        "a real backtest is already running; stop it before starting another")
                self._active_job_id = None
            job_id = f"job_{uuid.uuid4().hex[:12]}"
            run_request.parse_request_payload(payload)
            directory = Path(runs_dir) / "jobs" / job_id
            directory.mkdir(parents=True, exist_ok=True)
            spec_path = directory / "request.json"
            spec_path.write_text(json.dumps({
                "payload": payload,
                "runs_dir": str(runs_dir),
                "public_dir": str(public_dir),
                "strategy_dir": str(strategy_dir),
                "timeout": timeout,
            }, indent=2), encoding="utf-8")
            self._write_status_new(job_id, runs_dir)
            record = JobRecord(job_id=job_id, state="queued")
            self._jobs[job_id] = record
            self._runs_dir = str(runs_dir)
            self._public_dir = str(public_dir)
            cmd = [sys.executable, "-m", "src.run_jobs_worker", str(spec_path)]
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                cwd=str(Path(__file__).resolve().parent.parent),
            )
            record.pid = proc.pid
            self._proc = proc
            self._active_job_id = job_id
            return record

    def _write_status_new(self, job_id: str, runs_dir: str) -> None:
        path = Path(runs_dir) / "jobs" / job_id / "job.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        doc = {
            "job_id": job_id,
            "state": "queued",
            "stage": "queued",
            "pid": None,
            "run_id": None,
            "created_at": _utcnow(),
            "started_at": None,
            "finished_at": None,
            "heartbeat_at": None,
            "events": [{"t": _utcnow(), "stage": "queued", "event": "Run queued"}],
        }
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(doc, indent=2), encoding="utf-8")
        os.replace(tmp, path)

    def poll(self, job_id: str) -> dict:
        """Merge worker-owned status file into the registry record."""
        record = self.get(job_id)
        if record is None:
            from src import run_errors
            raise run_errors.RunJobNotFoundError(f"unknown run job: {job_id}")
        status = self._read_status(job_id) or {}
        with self._lock:
            state = str(status.get("state") or record.state)
            if record.state not in TERMINAL_STATES and state in TERMINAL_STATES:
                # A bridge-side stop always wins over a racing worker write.
                if record.state != "stopping":
                    record.state = state
                record.run_id = status.get("run_id") or record.run_id
                if record.state in TERMINAL_STATES:
                    record.finished_at = status.get("finished_at") or record.finished_at
                    if record.state == "stopped":
                        record.exit_code = record.exit_code
                        record.error = None
                    else:
                        record.exit_code = status.get("exit_code")
                        record.error = status.get("error")
                    if self._active_job_id == job_id:
                        self._active_job_id = None
                    if record.state == "failed":
                        run_id = status.get("run_id")
                        if run_id:
                            cleanup_incomplete_run(self._runs_dir or "runs",
                                                   self._public_dir or "", str(run_id))
            elif record.state in ACTIVE_STATES and self._proc is not None and self._proc.poll() is not None:
                # Worker died without writing a terminal state: report honestly.
                record.state = "failed"
                record.finished_at = _utcnow()
                record.exit_code = self._proc.poll()
                record.error = {"code": "worker_died",
                                "message": f"worker process exited unexpectedly (exit code {record.exit_code})"}
                if self._active_job_id == job_id:
                    self._active_job_id = None
                run_id = status.get("run_id")
                if run_id:
                    cleanup_incomplete_run(self._runs_dir or "runs",
                                           self._public_dir or "", str(run_id))
            record.events = list(status.get("events", record.events or []))[-_MAX_EVENTS:]
            return self.describe_locked(job_id)

    def describe(self, job_id: str) -> dict:
        with self._lock:
            return self.describe_locked(job_id)

    def describe_locked(self, job_id: str) -> dict:
        record = self._jobs.get(job_id)
        if record is None:
            from src import run_errors
            raise run_errors.RunJobNotFoundError(f"unknown run job: {job_id}")
        try:
            status = json.loads(self.status_path(job_id).read_text(encoding="utf-8"))
        except OSError:
            status = {}
        heartbeat_at = status.get("heartbeat_at")
        heartbeat_age = _age_seconds(heartbeat_at)
        heartbeat = heartbeat_status(record.state, heartbeat_at)
        started_at = status.get("started_at")
        created_at = status.get("created_at") or record.created_at
        stage = str(status.get("stage", "queued"))
        return {
            "job_id": job_id,
            "state": record.state,
            "stage": stage,
            "stage_label": STAGE_LABELS.get(stage, stage),
            "run_id": record.run_id or status.get("run_id"),
            "pid": record.pid,
            "created_at": created_at,
            "started_at": started_at,
            "finished_at": record.finished_at or status.get("finished_at"),
            "elapsed_seconds": _elapsed_seconds(started_at or created_at, record.finished_at or status.get("finished_at")),
            "heartbeat_at": heartbeat_at,
            "heartbeat_age_seconds": heartbeat_age,
            "heartbeat": heartbeat,
            "exit_code": record.exit_code if record.exit_code is not None else status.get("exit_code"),
            "error": record.error or status.get("error"),
            "events": list(status.get("events", record.events or []))[-_MAX_EVENTS:],
        }

    def active(self) -> dict | None:
        job_id = self.active_job_id()
        if job_id is None:
            return None
        return self.poll(job_id)

    # ------------------------------------------------------------------
    # stop / cleanup
    # ------------------------------------------------------------------
    def stop(self, job_id: str) -> dict:
        record = self.get(job_id)
        if record is None:
            from src import run_errors
            raise run_errors.RunJobNotFoundError(f"unknown run job: {job_id}")
        with self._lock:
            if record.state in TERMINAL_STATES:
                return self.describe_locked(job_id)
            record.state = "stopping"
            proc = self._proc
            self._append_event_locked(job_id, "stopping", "Stop requested", state="stopping")
        exit_code = self._terminate_worker(proc)
        with self._lock:
            record.state = "stopped"
            record.finished_at = _utcnow()
            record.exit_code = exit_code
            record.error = None
            self._append_event_locked(job_id, "stopping", f"Worker stopped (exit code {exit_code})", state="stopped")
            if self._active_job_id == job_id:
                self._active_job_id = None
            run_id = self._status_run_id_locked(job_id)
        if run_id:
            cleanup_incomplete_run(self._runs_dir or "runs", self._public_dir or "", run_id)
        return self.describe(job_id)

    def _terminate_worker(self, proc: subprocess.Popen | None) -> int | None:
        if proc is None or proc.poll() is not None:
            return proc.poll() if proc is not None else None
        try:
            proc.terminate()
        except OSError:
            pass
        try:
            return proc.wait(timeout=_STOP_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            pass
        try:
            proc.kill()
        except OSError:
            pass
        try:
            return proc.wait(timeout=_STOP_GRACE_SECONDS)
        except subprocess.TimeoutExpired:  # pragma: no cover - defensive
            return proc.poll()

    def cleanup_incomplete(self, job_id: str) -> dict:
        """Public cleanup helper used by tests: remove partial artifacts."""
        record = self.get(job_id)
        run_id = (record.run_id if record else None) or self._status_run_id_locked(job_id)
        if run_id:
            cleanup_incomplete_run(self._runs_dir or "runs", self._public_dir or "", run_id)
        return self.describe(job_id) if record else {"job_id": job_id}


def cleanup_incomplete_run(runs_dir: str, public_dir: str, run_id: str) -> dict:
    """Remove everything a stopped/failed run may have left behind.

    A stopped run never publishes: remove the partial run directory, the public
    artifact, and any manifest entry. Returns what was removed.
    """
    from src import publish_run

    removed: dict = {"run_dir": False, "public_artifact": False, "manifest_entry": False}
    directory = Path(runs_dir) / run_id
    if directory.is_dir():
        import shutil
        shutil.rmtree(directory, ignore_errors=True)
        removed["run_dir"] = True
    artifact = Path(public_dir) / publish_run.public_result_name(run_id)
    if artifact.is_file():
        artifact.unlink()
        removed["public_artifact"] = True
    manifest_path = Path(public_dir) / publish_run.MANIFEST_NAME
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except OSError:
        return removed
    entries = [e for e in data.get("runs", []) if e.get("runId") != run_id]
    if len(entries) != len(data.get("runs", [])):
        data["runs"] = entries
        tmp = manifest_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, manifest_path)
        removed["manifest_entry"] = True
    return removed


def latest_successful_run(runs_dir: str, public_dir: str) -> dict | None:
    """Newest valid archived run (Run Library truth), or None.

    A run counts when its ``result.json`` parses with the fields the frontend
    needs (run id, created_at, trades). Stopped/incomplete jobs leave no
    archive, so they can never be returned here. If the newest was deleted, the
    next newest valid run is used.
    """
    from src import run_library

    best: dict | None = None
    try:
        summaries = run_library.list_summaries(runs_dir, public_dir)
    except OSError:
        return None
    for summary in summaries:
        run_id = summary.get("run_id")
        created = summary.get("created_at")
        if not run_id:
            continue
        result_path = Path(runs_dir) / str(run_id) / "result.json"
        try:
            result = run_library._load_json(result_path)
        except OSError:
            continue
        if not isinstance(result, dict) or not isinstance(result.get("trades"), list):
            continue
        # Newest = result created_at, then archive mtime, then run id.
        # created_at ties (same-second publications) must not collapse to a
        # name comparison that prefers the OLDER run.
        try:
            mtime = result_path.stat().st_mtime
        except OSError:
            mtime = 0.0
        key = (str(created or ""), float(mtime), str(run_id))
        if best is None:
            best = summary
            best_key = key
        elif key > best_key:
            best = summary
            best_key = key
    return best


def heartbeat_status(state: str, heartbeat_at: str | None) -> str:
    """Fresh heartbeat -> alive. Stale -> delayed. Dead/errored -> failed path."""
    if state in TERMINAL_STATES:
        return state
    age = _age_seconds(heartbeat_at)
    if age is None:
        return "starting"
    if age <= HEARTBEAT_FRESH_SECONDS:
        return "alive"
    if age <= HEARTBEAT_STALE_SECONDS:
        return "delayed"
    return "stale"


def _age_seconds(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        moment = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        return max(0.0, (datetime.now(timezone.utc) - moment).total_seconds())
    except ValueError:
        return None


def _elapsed_seconds(started_at: str | None, finished_at: str | None) -> float | None:
    if not started_at:
        return None
    try:
        start = datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
    except ValueError:
        return None
    end = datetime.now(timezone.utc)
    if finished_at:
        try:
            end = datetime.fromisoformat(str(finished_at).replace("Z", "+00:00"))
        except ValueError:
            pass
    return max(0.0, (end - start).total_seconds())


# Process-wide singleton owned by the bridge module.
registry = JobRegistry()


def shutdown() -> None:
    """Bridge shutdown hook: terminate the active worker, never orphan it."""
    proc = registry._proc
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=_STOP_GRACE_SECONDS)
    except Exception:  # noqa: BLE001 - shutdown is best-effort
        pass
    if proc.poll() is None:
        try:
            proc.kill()
            proc.wait(timeout=_STOP_GRACE_SECONDS)
        except Exception:  # noqa: BLE001
            pass
