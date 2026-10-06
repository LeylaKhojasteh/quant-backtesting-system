"""Optimization foundation: deterministic parameter-grid studies.

Reference: ARCHITECTURE (OptimizationRequest -> Grid Builder -> multiple
BacktestRunRequests -> existing execute_run() -> normal Run Archives ->
Optimization Study Summary). This is orchestration only: it does not touch the
engine, does not duplicate signal execution/report generation, and produces
NORMAL archived runs as candidates.

Sequential execution only (no multiprocessing / queues / workers).
"""

from __future__ import annotations

import itertools
import json
import math
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from src import publish_run, run_backtest, run_errors, run_request
from src.result_contract import utc_now_iso

DEFAULT_MAX_CANDIDATES = 100
HARD_MAX_CANDIDATES = 500
DEFAULT_STUDIES = "studies"

#: Metrics that actually exist in BacktestResult and may be ranked.
OBJECTIVE_METRICS = (
    "net_pnl",
    "profit_factor",
    "win_rate",
    "max_drawdown",
    "expectancy",
    "total_trades",
)
DIRECTIONS = ("maximize", "minimize")

_STUDY_ID_RE = re.compile(r"^study_[A-Za-z0-9_]+$")


@dataclass
class Objective:
    metric: str
    direction: str

    def to_dict(self) -> dict:
        return {"metric": self.metric, "direction": self.direction}


@dataclass
class OptimizationRequest:
    """Inputs for one deterministic grid study (separate from run/result)."""

    strategy: str
    market: run_request.MarketRequest
    execution: run_request.ExecutionRequest
    strategy_grid: dict = field(default_factory=dict)
    execution_grid: dict = field(default_factory=dict)
    objective: Objective = field(default_factory=lambda: Objective("net_pnl", "maximize"))
    max_candidates: int = DEFAULT_MAX_CANDIDATES


def parse_optimization_payload(payload) -> OptimizationRequest:
    """Validate an HTTP/JSON optimization payload into OptimizationRequest."""
    if not isinstance(payload, dict):
        raise run_errors.InvalidOptimizationError("request body must be a JSON object")
    allowed = {"strategy", "market", "execution", "strategy_grid", "execution_grid", "objective", "max_candidates"}
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise run_errors.InvalidOptimizationError(f"unknown request field(s): {unknown}")

    strategy = payload.get("strategy")
    if not isinstance(strategy, str) or not strategy.strip():
        raise run_errors.InvalidOptimizationError("strategy is required")

    base = run_request.parse_request_payload({
        "strategy": {"selector": strategy},
        "market": payload.get("market"),
        "execution": payload.get("execution") or {},
        "publish": False,
    })

    strategy_grid = payload.get("strategy_grid") or {}
    execution_grid = payload.get("execution_grid") or {}
    if not isinstance(strategy_grid, dict) or not isinstance(execution_grid, dict):
        raise run_errors.InvalidOptimizationError("grids must be objects of value lists")

    objective_raw = payload.get("objective") or {}
    if not isinstance(objective_raw, dict):
        raise run_errors.InvalidOptimizationError("objective must be an object")
    metric = objective_raw.get("metric")
    direction = objective_raw.get("direction")
    if metric not in OBJECTIVE_METRICS:
        raise run_errors.InvalidOptimizationError(
            f"objective.metric must be one of {list(OBJECTIVE_METRICS)}"
        )
    if direction not in DIRECTIONS:
        raise run_errors.InvalidOptimizationError(
            f"objective.direction must be one of {list(DIRECTIONS)}"
        )

    max_candidates = payload.get("max_candidates", DEFAULT_MAX_CANDIDATES)
    if max_candidates is not None:
        if isinstance(max_candidates, bool) or not isinstance(max_candidates, int) or max_candidates < 1:
            raise run_errors.InvalidOptimizationError("max_candidates must be a positive integer")

    return OptimizationRequest(
        strategy=strategy.strip(),
        market=base.market,
        execution=base.execution,
        strategy_grid=dict(strategy_grid),
        execution_grid=dict(execution_grid),
        objective=Objective(metric, direction),
        max_candidates=max_candidates if max_candidates is not None else DEFAULT_MAX_CANDIDATES,
    )


def _validate_grid_lists(grid: dict, *, kind: str) -> None:
    for key, values in grid.items():
        if not isinstance(key, str) or not key:
            raise run_errors.InvalidOptimizationError(f"{kind} grid keys must be strings")
        if not isinstance(values, (list, tuple)) or len(values) == 0:
            raise run_errors.InvalidOptimizationError(
                f"{kind} grid value for {key!r} must be a non-empty list"
            )


def build_combinations(request: OptimizationRequest, declared: dict) -> list[dict]:
    """Validate grids and deterministically build every candidate combination."""
    _validate_grid_lists(request.strategy_grid, kind="strategy")
    _validate_grid_lists(request.execution_grid, kind="execution")

    declared = dict(declared or {})
    # strategy grid must reference declared parameters (never engine settings)
    for key in request.strategy_grid:
        if key in run_request.ENGINE_SETTING_NAMES:
            raise run_errors.InvalidOptimizationError(
                f"{key!r} is an execution setting; use execution_grid"
            )
        if key not in declared:
            raise run_errors.InvalidOptimizationError(
                f"unknown strategy parameter {key!r}; declared: {sorted(declared)}"
            )
    for key in request.execution_grid:
        if key not in run_request.ENGINE_SETTING_NAMES:
            raise run_errors.InvalidOptimizationError(
                f"unknown execution setting {key!r}; allowed: {list(run_request.ENGINE_SETTING_NAMES)}"
            )

    strategy_keys = sorted(request.strategy_grid)
    exec_keys = sorted(request.execution_grid)

    strategy_values = {
        key: [run_request.coerce_parameter_value(key, v, declared[key]) for v in request.strategy_grid[key]]
        for key in strategy_keys
    }
    exec_values = {
        key: [_positive_exec(key, v) for v in request.execution_grid[key]] for key in exec_keys
    }

    total = 1
    for key in strategy_keys:
        total *= len(strategy_values[key])
    for key in exec_keys:
        total *= len(exec_values[key])
    limit = min(max(request.max_candidates, 1), HARD_MAX_CANDIDATES)
    if total > limit:
        raise run_errors.CandidateLimitError(
            f"grid produces {total} candidates which exceeds the limit of {limit}; "
            "narrow the grid or raise max_candidates"
        )

    combos = []
    strategy_axes = [strategy_values[k] for k in strategy_keys]
    exec_axes = [exec_values[k] for k in exec_keys]
    strategy_iter = itertools.product(*strategy_axes) if strategy_axes else [()]
    exec_iter_factory = (lambda: itertools.product(*exec_axes)) if exec_axes else (lambda: [()])
    for s_vals in strategy_iter:
        overrides = dict(zip(strategy_keys, s_vals))
        for e_vals in exec_iter_factory():
            execution = {
                "stop_distance": request.execution.stop_distance,
                "take_profit_distance": request.execution.take_profit_distance,
            }
            execution.update(dict(zip(exec_keys, e_vals)))
            combos.append({"overrides": overrides, "execution": execution})
    return combos


def _positive_exec(name: str, value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or float(value) <= 0:
        raise run_errors.InvalidOptimizationError(f"{name} values must be positive numbers")
    return float(value)


def _candidate_metrics(metrics) -> dict:
    total = metrics.total_trades
    avg_trade = (metrics.net_pnl / total) if total else None
    return {
        "total_trades": total,
        "wins": metrics.wins,
        "losses": metrics.losses,
        "win_rate": metrics.win_rate,
        "net_pnl": metrics.net_pnl,
        "avg_trade": avg_trade,
        "profit_factor": metrics.profit_factor,
        "max_drawdown": metrics.max_drawdown,
        "expectancy": metrics.expectancy,
    }


def _objective_value(metrics: dict, objective: Objective):
    value = metrics.get(objective.metric)
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(float(value)):
        return None
    return float(value)


def _rank(candidates: list[dict], objective: Objective) -> list[str]:
    scored = [
        c for c in candidates
        if c["status"] == "ok" and c.get("objective_value") is not None
    ]
    reverse = objective.direction == "maximize"
    scored.sort(key=lambda c: c["objective_value"], reverse=reverse)
    return [c["run_id"] for c in scored]


def run_study(
    request: OptimizationRequest,
    *,
    runs: str | Path = "runs",
    studies: str | Path = DEFAULT_STUDIES,
    public: str | Path = publish_run.DEFAULT_PUBLIC,
) -> dict:
    """Execute a deterministic grid study sequentially. Returns the study dict."""
    strategy, strategy_path = run_backtest.resolve_strategy(request.strategy)
    declared = strategy.parameters()
    combos = build_combinations(request, declared)

    study_id = f"study_{uuid.uuid4().hex[:8]}"
    created_at = utc_now_iso()
    meta = strategy.metadata()

    candidates = []
    for index, combo in enumerate(combos):
        run_id = f"opt_{study_id}_{index:03d}"
        entry = {
            "index": index,
            "run_id": run_id,
            "parameters": dict(combo["overrides"]),
            "execution": dict(combo["execution"]),
            "status": "pending",
            "failure": None,
            "metrics": None,
            "objective_value": None,
        }
        candidate_request = run_request.BacktestRunRequest(
            strategy=run_request.StrategyRequest(
                selector=request.strategy,
                parameters=dict(combo["overrides"]),
                file=str(strategy_path) if strategy_path is not None else None,
            ),
            market=request.market,
            execution=run_request.ExecutionRequest(**combo["execution"]),
            publish=False,  # candidates stay local (Task 13)
        )
        try:
            info = run_backtest.execute_run(
                candidate_request, out=str(runs), report=True, public=str(public), run_id=run_id
            )
            metrics = _candidate_metrics(info["result"].metrics)
            entry.update({
                "status": "ok",
                "metrics": metrics,
                "objective_value": _objective_value(metrics, request.objective),
            })
        except run_errors.RunError as exc:
            entry.update({"status": "failed", "failure": {"code": exc.code, "message": exc.message}})
        except Exception as exc:  # noqa: BLE001
            entry.update({"status": "failed", "failure": {"code": "candidate_failed", "message": str(exc)}})
        candidates.append(entry)

    ranking = _rank(candidates, request.objective)
    any_ok = any(c["status"] == "ok" for c in candidates)
    best_run_id = ranking[0] if ranking else None

    study = {
        "study_id": study_id,
        "created_at": created_at,
        "status": "completed" if any_ok else "failed",
        "strategy": {
            "selector": request.strategy,
            "name": meta.get("name", ""),
            "version": meta.get("version", ""),
        },
        "dataset": {
            "symbol": request.market.symbol,
            "timeframe": request.market.timeframe,
            "start": request.market.start,
            "end": request.market.end,
            "data_source": request.market.data_source,
        },
        "base": {
            "parameters": declared,
            "execution": {
                "stop_distance": request.execution.stop_distance,
                "take_profit_distance": request.execution.take_profit_distance,
            },
        },
        "strategy_grid": dict(request.strategy_grid),
        "execution_grid": dict(request.execution_grid),
        "objective": request.objective.to_dict(),
        "candidate_count": len(candidates),
        "candidates": candidates,
        "ranking": ranking,
        "best_run_id": best_run_id,
    }
    save_study(studies, study)
    return study


# ---- study archive -------------------------------------------------------

def validate_study_id(study_id) -> str:
    if not isinstance(study_id, str) or not study_id.strip() or not _STUDY_ID_RE.match(study_id.strip()):
        raise run_errors.InvalidStudyIdError(f"invalid study id: {study_id!r}")
    return study_id.strip()


def _study_dir(studies_root, study_id: str) -> Path:
    base = Path(studies_root)
    target = base / validate_study_id(study_id)
    try:
        target.resolve().relative_to(base.resolve())
    except ValueError as exc:
        raise run_errors.InvalidStudyIdError(f"invalid study id: {study_id!r}") from exc
    return target


def save_study(studies_root, study: dict) -> Path:
    directory = _study_dir(studies_root, study["study_id"])
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "study.json"
    path.write_text(json.dumps(study, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def load_study(studies_root, study_id: str) -> dict:
    path = _study_dir(studies_root, study_id) / "study.json"
    if not path.is_file():
        raise run_errors.StudyNotFoundError(f"study not found: {study_id}")
    return json.loads(path.read_text(encoding="utf-8"))


def list_studies(studies_root) -> list[dict]:
    base = Path(studies_root)
    if not base.is_dir():
        return []
    summaries = []
    for path in base.glob("*/study.json"):
        try:
            study = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        best = next((c for c in study.get("candidates", []) if c.get("run_id") == study.get("best_run_id")), None)
        summaries.append({
            "study_id": study.get("study_id"),
            "created_at": study.get("created_at"),
            "status": study.get("status"),
            "strategy": study.get("strategy", {}).get("name", ""),
            "candidate_count": study.get("candidate_count", 0),
            "objective": study.get("objective", {}),
            "best_run_id": study.get("best_run_id"),
            "best_objective_value": (best or {}).get("objective_value"),
        })
    summaries.sort(key=lambda s: (str(s.get("created_at") or ""), str(s.get("study_id") or "")), reverse=True)
    return summaries
