"""OS worker process for one real backtest job (v1.0.1 live-run-control).

Spawned by :class:`src.run_jobs.JobRegistry` as::

    python -m src.run_jobs_worker <spec_path>

``spec_path`` is a JSON file written by the registry with the validated run
payload plus ``runs_dir`` / ``public_dir`` / ``strategy_dir``. The worker:

1. upgrades the worker-owned status file (``runs/jobs/<job_id>/job.json``)
   to ``running`` and starts a heartbeat thread (1 s cadence) so the UI can
   tell the worker is alive while :mod:`src.backtest` runs synchronously;
2. calls the existing :func:`src.run_backtest.execute_run` — the same runner
   the synchronous ``POST /runs`` path uses — with stage-reporting wrappers
   around the real milestones (no duplicated backtest logic, no fake stages);
3. writes a terminal status (``completed`` / ``failed``) with the ``run_id``;
4. keeps the full traceback in ``worker.log`` (developer detail); the status
   file exposed to the product UI carries only a short ``code: message``.

Windows-safe: ``main()`` runs only under ``if __name__ == "__main__"`` so
spawning never recurses; the child is launched with an argv list (paths with
spaces are fine); the exit code is observable by the bridge via the process
handle. The bridge owns stopping (terminate -> kill); this module never kills
itself except to enforce its own startup failures.
"""

from __future__ import annotations

import functools
import json
import os
import sys
import threading
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

HEARTBEAT_INTERVAL_SECONDS = 1.0
ENGINE_RUNNING_EVENT_AFTER_SECONDS = 5.0


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class StatusFile:
    """Atomic read-modify-write access to one job's ``job.json``."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()

    def read(self) -> dict:
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except OSError:
            return {}

    def update(self, **fields) -> dict:
        with self._lock:
            doc = self.read()
            doc.update(fields)
            tmp = self._path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(doc, indent=2), encoding="utf-8")
            os.replace(tmp, self._path)
            return doc

    def add_event(self, stage: str, event: str, *, state: str | None = None,
                  extra: dict | None = None) -> dict:
        with self._lock:
            doc = self.read()
            events = list(doc.get("events", []))
            events.append({"t": _utcnow(), "stage": stage, "event": event})
            doc["events"] = events[-200:]
            if state is not None:
                doc["state"] = state
            if extra:
                doc.update(extra)
            doc["stage"] = stage
            doc["heartbeat_at"] = _utcnow()
            tmp = self._path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(doc, indent=2), encoding="utf-8")
            os.replace(tmp, self._path)
            return doc

    def heartbeat(self) -> None:
        self.update(heartbeat_at=_utcnow())


def _generate_run_id(runs_dir: str) -> str:
    for _ in range(25):
        candidate = f"run_{uuid.uuid4().hex[:8]}"
        if not (Path(runs_dir) / candidate).exists():
            return candidate
    raise RuntimeError("could not allocate a unique run id")


def _short_error(exc: BaseException) -> dict:
    code = getattr(exc, "code", None) or "backtest_failed"
    message = getattr(exc, "message", None) or str(exc) or type(exc).__name__
    return {"code": str(code), "message": str(message)[:500]}


def run_spec(spec_path: str) -> dict:
    """Execute one job spec file. Returns ``{"job_id", "run_id", "state"}``."""
    spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    payload = spec["payload"]
    runs_dir = str(spec["runs_dir"])
    public_dir = str(spec["public_dir"])
    job_id = str(Path(spec_path).parent.name)
    status = StatusFile(Path(runs_dir) / "jobs" / job_id / "job.json")
    log_path = Path(runs_dir) / "jobs" / job_id / "worker.log"

    from src import run_request

    request = run_request.parse_request_payload(payload)
    run_id = _generate_run_id(runs_dir)

    stop_heartbeat = threading.Event()
    stage_holder: dict = {"stage": "resolving_dataset"}
    engine_running_announced = threading.Event()

    def heartbeat_loop() -> None:
        started = datetime.now(timezone.utc)
        while not stop_heartbeat.wait(HEARTBEAT_INTERVAL_SECONDS):
            try:
                status.heartbeat()
                if (stage_holder["stage"] == "executing_engine"
                        and not engine_running_announced.is_set()
                        and (datetime.now(timezone.utc) - started).total_seconds()
                        >= ENGINE_RUNNING_EVENT_AFTER_SECONDS):
                    # The engine is a synchronous long loop; one honest
                    # "still running" marker, not a progress percentage.
                    engine_running_announced.set()
                    status.add_event("executing_engine", "Engine running")
            except OSError:
                pass

    status.add_event("resolving_dataset", "Run started", state="running", extra={
        "started_at": _utcnow(),
        "pid": os.getpid(),
        "run_id": run_id,
    })
    status.add_event("resolving_dataset", "Dataset resolved")
    pulse = threading.Thread(target=heartbeat_loop, name=f"qbl-heartbeat-{job_id}",
                             daemon=True)
    pulse.start()

    def stage(stage_name: str, done_event: str):
        def decorator(fn):
            @functools.wraps(fn)
            def wrapper(*args, **kwargs):
                stage_holder["stage"] = stage_name
                status.add_event(stage_name, _START_EVENTS.get(stage_name, stage_name))
                try:
                    result = fn(*args, **kwargs)
                except Exception:
                    raise
                status.add_event(stage_name, done_event)
                return result
            return wrapper
        return decorator

    try:
        from src import backtest as engine
        from src import data_engine
        from src import publish_run
        from src import report as report_mod
        from src import run_backtest
        from src import run_store
        from src import strategy_loader

        data_engine.load_ticks_frame = stage(  # type: ignore[method-assign]
            "loading_ticks", "Tick partitions resolved")(data_engine.load_ticks_frame)
        data_engine.load_candles_frame = stage(  # type: ignore[method-assign]
            "loading_candles", "Candles loaded")(data_engine.load_candles_frame)
        run_backtest._load_ticks = stage(  # type: ignore[attr-defined]
            "loading_ticks", "Tick partitions resolved")(run_backtest._load_ticks)
        strategy_loader.generate_validated_signals = stage(  # type: ignore[method-assign]
            "generating_signals", "Signals generated")(
                strategy_loader.generate_validated_signals)
        engine.run_backtest = stage(  # type: ignore[method-assign]
            "executing_engine", "Engine completed")(engine.run_backtest)
        # NOTE: run_backtest binds trades_from_engine via `from ... import`
        # at module load, so the wrapper must patch the runner's own
        # reference (patching src.result_contract would be a no-op here).
        run_backtest.trades_from_engine = stage(  # type: ignore[attr-defined]
            "building_result", "Result built")(run_backtest.trades_from_engine)
        run_store.save_run = stage(  # type: ignore[method-assign]
            "writing_archive", "Result archived")(run_store.save_run)
        report_mod.generate_report = stage(  # type: ignore[method-assign]
            "generating_report", "Report generated")(report_mod.generate_report)
        publish_run.publish_run = stage(  # type: ignore[method-assign]
            "publishing", "Run published")(publish_run.publish_run)

        info = run_backtest.execute_run(
            request,
            out=runs_dir,
            report=True,
            public=public_dir,
            run_id=run_id,
        )
    except Exception as exc:  # noqa: BLE001 - worker must report, never crash silently
        try:
            with log_path.open("a", encoding="utf-8") as fh:
                fh.write(f"[{_utcnow()}] job {job_id} failed:\n")
                traceback.print_exc(file=fh)
        except OSError:
            pass
        try:
            from src import run_jobs
            run_jobs.cleanup_incomplete_run(runs_dir, public_dir, run_id)
        except Exception:  # noqa: BLE001 - cleanup is best-effort; status still lands
            pass
        finally:
            stop_heartbeat.set()
        status.add_event("finalizing", "Run failed", state="failed", extra={
            "finished_at": _utcnow(),
            "exit_code": 1,
            "error": _short_error(exc),
        })
        return {"job_id": job_id, "run_id": run_id, "state": "failed"}
    else:
        stop_heartbeat.set()
        final_run_id = str(info.get("run_id") or run_id)
        status.add_event("finalizing", "Run completed", state="completed", extra={
            "finished_at": _utcnow(),
            "exit_code": 0,
            "error": None,
            "run_id": final_run_id,
        })
        return {"job_id": job_id, "run_id": final_run_id, "state": "completed"}


_START_EVENTS = {
    "loading_ticks": "Loading ticks",
    "loading_candles": "Loading candles",
    "generating_signals": "Generating signals",
    "executing_engine": "Engine execution started",
    "building_result": "Building result",
    "writing_archive": "Writing archive",
    "generating_report": "Generating report",
    "publishing": "Publishing",
}


def main(argv=None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if len(args) != 1:
        print("usage: python -m src.run_jobs_worker <spec_path>", file=sys.stderr)
        return 2
    try:
        outcome = run_spec(args[0])
    except Exception:  # noqa: BLE001 - startup failure before status exists
        traceback.print_exc()
        return 1
    return 0 if outcome.get("state") == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
