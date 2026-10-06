"""Run Library — manage archived backtest runs.

Reference: ARCHITECTURE (Run Archive -> Run Library Service -> Local Bridge ->
Frontend Research Workspace). The filesystem archive is the source of truth.

Operates ONLY inside the approved runs directory; run ids are validated so no
path traversal or arbitrary reads are possible. This module performs no
backtest or metric computation — it reads archived contract payloads and
reuses ``src.publish_run`` for publishing.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from src import publish_run, run_errors, run_store

DEFAULT_RUNS = "runs"
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]*$")
_METRIC_KEYS = ("total_trades", "win_rate", "net_pnl", "profit_factor")
_ENGINE_PARAM_KEYS = ("stop_distance", "take_profit_distance", "timeframe", "symbol")


def validate_run_id(run_id) -> str:
    """Return a safe run id or raise ``InvalidRunIdError``."""
    if not isinstance(run_id, str) or not run_id.strip():
        raise run_errors.InvalidRunIdError("run id is required")
    rid = run_id.strip()
    if len(rid) > 128 or rid in (".", "..") or not _NAME_RE.match(rid):
        raise run_errors.InvalidRunIdError(f"invalid run id: {run_id!r}")
    return rid


def _run_dir(runs_dir: str | Path, run_id: str) -> Path:
    base = Path(runs_dir)
    target = base / validate_run_id(run_id)
    try:
        target.resolve().relative_to(base.resolve())
    except ValueError as exc:  # defensive: validate_run_id already blocks separators
        raise run_errors.InvalidRunIdError(f"invalid run id: {run_id!r}") from exc
    return target


def _load_json(path: Path):
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _manifest_run_ids(public_dir: str | Path) -> set[str]:
    data = _load_json(Path(public_dir) / publish_run.MANIFEST_NAME)
    if not isinstance(data, dict) or not isinstance(data.get("runs"), list):
        return set()
    return {str(r.get("runId")) for r in data["runs"] if isinstance(r, dict)}


def is_published(run_id: str, public_dir: str | Path) -> bool:
    artifact = Path(public_dir) / publish_run.public_result_name(run_id)
    return artifact.is_file() and run_id in _manifest_run_ids(public_dir)


def _strategy_fields(config: dict) -> tuple[str | None, str | None]:
    """Return ``(selector, file)`` from nested or legacy flat run config."""
    strategy = config.get("strategy")
    if isinstance(strategy, dict):
        return strategy.get("selector"), strategy.get("file")
    return config.get("strategy"), config.get("strategy_file")


def run_summary(runs_dir: str | Path, run_id: str, public_dir: str | Path) -> dict:
    """Compact, deterministic summary for one archived run."""
    directory = _run_dir(runs_dir, run_id)
    result = _load_json(directory / "result.json")
    if not isinstance(result, dict):
        raise run_errors.RunNotFoundError(f"archived run not found: {run_id}")
    config = _load_json(directory / "config.json") or {}
    metrics = result.get("metrics") or {}
    strategy = result.get("strategy") or {}
    conf = result.get("config") or {}
    selector, file = _strategy_fields(config)
    return {
        "run_id": run_id,
        "created_at": result.get("created_at"),
        "strategy": {"name": strategy.get("name", ""), "version": strategy.get("version", "")},
        "strategy_selector": selector,
        "strategy_file": file,
        "symbol": conf.get("symbol"),
        "timeframe": conf.get("timeframe"),
        "start": conf.get("start"),
        "end": conf.get("end"),
        "total_trades": metrics.get("total_trades"),
        "win_rate": metrics.get("win_rate"),
        "net_pnl": metrics.get("net_pnl"),
        "profit_factor": metrics.get("profit_factor"),
        "report": (directory / "report.html").is_file(),
        "published": is_published(run_id, public_dir),
    }


def list_summaries(runs_dir: str | Path, public_dir: str | Path) -> list[dict]:
    """All runs (published or not), newest first, deterministic."""
    base = Path(runs_dir)
    if not base.is_dir():
        return []
    summaries = []
    for name in run_store.list_runs(base):
        try:
            summaries.append(run_summary(base, name, public_dir))
        except run_errors.RunError:
            continue
    # Newest first, then run id descending for a stable order.
    summaries.sort(key=lambda s: (str(s.get("created_at") or ""), s["run_id"]), reverse=True)
    return summaries


def run_detail(runs_dir: str | Path, run_id: str, public_dir: str | Path) -> dict:
    """Summary plus the archived configuration needed to reuse the run.

    Missing dependencies are reported honestly: the caller matches the archived
    strategy selector/dataset against its live catalogs and warns when absent.
    """
    summary = run_summary(runs_dir, run_id, public_dir)
    directory = _run_dir(runs_dir, run_id)
    result = _load_json(directory / "result.json") or {}
    config = _load_json(directory / "config.json") or {}
    conf = result.get("config") or {}

    strategy = config.get("strategy")
    if isinstance(strategy, dict):
        parameters = dict(strategy.get("parameters") or {})
        execution = config.get("execution") or {}
        overrides = dict(strategy.get("overrides") or {})
        selector, file = strategy.get("selector"), strategy.get("file")
    else:
        raw = dict(config.get("parameters") or {})
        parameters = {k: v for k, v in raw.items() if k not in _ENGINE_PARAM_KEYS}
        execution = {
            "stop_distance": raw.get("stop_distance"),
            "take_profit_distance": raw.get("take_profit_distance"),
        }
        overrides = parameters
        selector, file = config.get("strategy"), config.get("strategy_file")

    data_source = conf.get("data_source") or ""
    detail = dict(summary)
    detail.update(
        {
            "description": (result.get("strategy") or {}).get("description", ""),
            "parameters": parameters,
            "overrides": overrides,
            "execution": {
                "stop_distance": execution.get("stop_distance"),
                "take_profit_distance": execution.get("take_profit_distance"),
            },
            "market": {
                "symbol": conf.get("symbol"),
                "timeframe": conf.get("timeframe"),
                "start": conf.get("start"),
                "end": conf.get("end"),
                "data_source": data_source,
                "dataset": Path(data_source).name if data_source else None,
            },
            "selector": selector,
            "file": file,
        }
    )
    return detail


def report_path(runs_dir: str | Path, run_id: str) -> Path:
    """Return the existing report.html path, or raise ``ReportUnavailableError``."""
    path = _run_dir(runs_dir, run_id) / "report.html"
    if not path.is_file():
        raise run_errors.ReportUnavailableError(f"no report available for run {run_id}")
    return path


def publish_archived(run_id: str, runs_dir: str | Path, public_dir: str | Path) -> dict:
    """Publish an existing archived run (idempotent). Reuses publish_run."""
    directory = _run_dir(runs_dir, run_id)
    if not (directory / "result.json").is_file():
        raise run_errors.RunNotFoundError(f"archived run not found: {run_id}")
    try:
        return publish_run.publish_run(run_id, runs=str(runs_dir), public=str(public_dir))
    except run_errors.RunError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise run_errors.PublishFailedError(f"publish failed: {exc}") from exc


def delete_run(runs_dir: str | Path, run_id: str, public_dir: str | Path) -> dict:
    """Delete one local run and, if published, its public artifact + manifest entry."""
    directory = _run_dir(runs_dir, run_id)
    if not directory.is_dir():
        raise run_errors.RunNotFoundError(f"archived run not found: {run_id}")

    try:
        shutil.rmtree(directory)
    except OSError as exc:
        raise run_errors.RunDeleteError(f"failed to delete run {run_id}: {exc}") from exc

    removed_artifact = None
    removed_manifest = False
    public = Path(public_dir)
    artifact = public / publish_run.public_result_name(run_id)
    if artifact.is_file():
        artifact.unlink()
        removed_artifact = str(artifact)

    manifest_path = public / publish_run.MANIFEST_NAME
    data = _load_json(manifest_path)
    if isinstance(data, dict) and isinstance(data.get("runs"), list):
        kept = [r for r in data["runs"] if str(r.get("runId")) != run_id]
        if len(kept) != len(data["runs"]):
            data["runs"] = kept
            manifest_path.write_text(
                json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )
            removed_manifest = True

    return {
        "run_id": run_id,
        "deleted": True,
        "removed_artifact": removed_artifact,
        "removed_manifest_entry": removed_manifest,
    }
