"""Filesystem run architecture — the Report Archive foundation.

Reference: ``ARCHITECTURE.md`` Section 6.

Layout per run::

    runs/<run_id>/
        config.json      # effective run configuration (strategy, params, data)
        result.json      # full BacktestResult contract payload
        trades.csv       # per-trade table
        equity.csv       # equity-curve table
        report.html      # (future) rendered report
        strategy.py      # (optional) strategy snapshot

No database, no cloud, no auth. A saved run is reproducible: config +
strategy snapshot + data fingerprint describe exactly what was tested.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Iterable


RUN_FILES = ("config.json", "result.json", "trades.csv", "equity.csv")


def run_dir(root: str | Path, run_id: str) -> Path:
    return Path(root) / run_id


def save_run(
    root: str | Path,
    run_id: str,
    *,
    config: dict,
    result: dict,
    trades: Iterable[dict],
    equity: Iterable[dict],
    strategy_source: str | None = None,
) -> Path:
    """Persist one run to ``runs/<run_id>/``. Returns the run directory."""
    directory = run_dir(root, run_id)
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "config.json").write_text(json.dumps(config, indent=2), encoding="utf-8")
    (directory / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    _write_csv(directory / "trades.csv", trades)
    _write_csv(directory / "equity.csv", equity)
    if strategy_source is not None:
        (directory / "strategy.py").write_text(strategy_source, encoding="utf-8")
    return directory


def load_result(root: str | Path, run_id: str) -> dict:
    """Load the stored ``result.json`` contract payload for a run."""
    return json.loads((run_dir(root, run_id) / "result.json").read_text(encoding="utf-8"))


def load_config(root: str | Path, run_id: str) -> dict:
    """Load the stored ``config.json`` for a run."""
    return json.loads((run_dir(root, run_id) / "config.json").read_text(encoding="utf-8"))


def list_runs(root: str | Path) -> list[str]:
    """Return sorted run ids that contain a ``result.json``."""
    base = Path(root)
    if not base.is_dir():
        return []
    return sorted(p.name for p in base.iterdir() if p.is_dir() and (p / "result.json").is_file())


def _write_csv(path: Path, rows: Iterable[dict]) -> None:
    rows = list(rows)
    with path.open("w", newline="", encoding="utf-8") as fh:
        if not rows:
            return
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
