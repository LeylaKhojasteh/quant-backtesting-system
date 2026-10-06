"""Walk-Forward testing: deterministic out-of-sample evaluation of optimized parameters.

Reference: ARCHITECTURE (Strategy Loader -> BacktestRunRequest -> execute_run() ->
BacktestResult -> Run Archive) and the Research Analysis optimization service.

For each chronological fold:

    TRAIN range -> existing OptimizationRequest -> run_study() -> best candidate
        -> selected effective parameters
    TEST range  -> existing BacktestRunRequest -> execute_run() -> normal archive

This module is orchestration only. It does NOT:
- touch ``src/backtest.py`` (the verified engine) or any strategy,
- duplicate optimization/backtest/report logic (it calls the existing services),
- auto-publish candidates or OOS runs (they stay local archives),
- use any future information: a fold's test period only ever receives parameters
  selected from that fold's own training optimization.

Sequential execution only (no workers / queues / parallelism).
"""

from __future__ import annotations

import json
import math
import re
import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from src import optimization, run_backtest, run_errors, run_request
from src.result_contract import utc_now_iso

DEFAULT_WALKFORWARDS = "walkforwards"

#: Safety limits (centralized + tested). Never silently truncated.
DEFAULT_MAX_FOLDS = 10
HARD_MAX_FOLDS = 50
DEFAULT_MAX_TOTAL_CANDIDATES = 500
HARD_MAX_TOTAL_CANDIDATES = 2000

WINDOW_MODES = ("rolling", "anchored")
MAX_EQUITY_POINTS = 200

_WF_ID_RE = re.compile(r"^wf_[A-Za-z0-9_]+$")


# --------------------------------------------------------------------------
# Contracts
# --------------------------------------------------------------------------


@dataclass
class WindowConfig:
    """Walk-forward window sizes (calendar days) and mode."""

    train_days: int
    test_days: int
    step_days: int
    mode: str = "rolling"

    def to_dict(self) -> dict:
        return {
            "train_days": self.train_days,
            "test_days": self.test_days,
            "step_days": self.step_days,
            "mode": self.mode,
        }


@dataclass
class Fold:
    """One chronological train/test split (never overlapping incorrectly)."""

    index: int
    train_start: str
    train_end: str
    test_start: str
    test_end: str

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "train_start": self.train_start,
            "train_end": self.train_end,
            "test_start": self.test_start,
            "test_end": self.test_end,
        }


@dataclass
class WalkForwardRequest:
    """Inputs for one deterministic walk-forward study.

    Separate from BacktestRunRequest, OptimizationRequest and BacktestResult.
    """

    strategy: str
    market: run_request.MarketRequest
    execution: run_request.ExecutionRequest
    window: WindowConfig
    strategy_grid: dict = field(default_factory=dict)
    execution_grid: dict = field(default_factory=dict)
    objective: optimization.Objective = field(
        default_factory=lambda: optimization.Objective("net_pnl", "maximize")
    )
    max_folds: int = DEFAULT_MAX_FOLDS
    max_candidates: int = optimization.DEFAULT_MAX_CANDIDATES
    max_total_candidates: int = DEFAULT_MAX_TOTAL_CANDIDATES
    #: Optional account/instrument settings carried through to every training
    #: candidate and OOS test run. ``None`` keeps them price-term only.
    account: "run_request.AccountModel | None" = None
    instrument: "run_request.InstrumentSpec | None" = None


def _positive_int(name: str, value) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise run_errors.InvalidWalkForwardError(f"{name} must be a positive integer")
    return value


def parse_walk_forward_payload(payload) -> WalkForwardRequest:
    """Validate an HTTP/JSON walk-forward payload into a WalkForwardRequest."""
    if not isinstance(payload, dict):
        raise run_errors.InvalidWalkForwardError("request body must be a JSON object")
    allowed = {
        "strategy", "market", "execution", "strategy_grid", "execution_grid",
        "objective", "window", "max_folds", "max_candidates", "max_total_candidates",
        "account", "instrument",
    }
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise run_errors.InvalidWalkForwardError(f"unknown request field(s): {unknown}")

    strategy = payload.get("strategy")
    if not isinstance(strategy, str) or not strategy.strip():
        raise run_errors.InvalidWalkForwardError("strategy is required")

    base = run_request.parse_request_payload({
        "strategy": {"selector": strategy},
        "market": payload.get("market"),
        "execution": payload.get("execution") or {},
        "publish": False,
    })
    if not base.market.start or not base.market.end:
        raise run_errors.InvalidWalkForwardError(
            "market.start and market.end are required to build walk-forward folds"
        )

    raw_window = payload.get("window")
    if not isinstance(raw_window, dict):
        raise run_errors.InvalidWalkForwardError("window is required")
    window_unknown = sorted(set(raw_window) - {"train_days", "test_days", "step_days", "mode"})
    if window_unknown:
        raise run_errors.InvalidWalkForwardError(f"unknown window field(s): {window_unknown}")
    mode = raw_window.get("mode", "rolling")
    if mode not in WINDOW_MODES:
        raise run_errors.InvalidWalkForwardError(f"window.mode must be one of {list(WINDOW_MODES)}")
    window = WindowConfig(
        train_days=_positive_int("window.train_days", raw_window.get("train_days")),
        test_days=_positive_int("window.test_days", raw_window.get("test_days")),
        step_days=_positive_int("window.step_days", raw_window.get("step_days")),
        mode=mode,
    )

    strategy_grid = payload.get("strategy_grid") or {}
    execution_grid = payload.get("execution_grid") or {}
    if not isinstance(strategy_grid, dict) or not isinstance(execution_grid, dict):
        raise run_errors.InvalidWalkForwardError("grids must be objects of value lists")

    objective_raw = payload.get("objective") or {}
    if not isinstance(objective_raw, dict):
        raise run_errors.InvalidWalkForwardError("objective must be an object")
    metric = objective_raw.get("metric")
    direction = objective_raw.get("direction")
    if metric not in optimization.OBJECTIVE_METRICS:
        raise run_errors.InvalidWalkForwardError(
            f"objective.metric must be one of {list(optimization.OBJECTIVE_METRICS)}"
        )
    if direction not in optimization.DIRECTIONS:
        raise run_errors.InvalidWalkForwardError(
            f"objective.direction must be one of {list(optimization.DIRECTIONS)}"
        )

    max_folds = payload.get("max_folds", DEFAULT_MAX_FOLDS)
    if isinstance(max_folds, bool) or not isinstance(max_folds, int) or max_folds < 1:
        raise run_errors.InvalidWalkForwardError("max_folds must be a positive integer")
    max_candidates = payload.get("max_candidates", optimization.DEFAULT_MAX_CANDIDATES)
    if isinstance(max_candidates, bool) or not isinstance(max_candidates, int) or max_candidates < 1:
        raise run_errors.InvalidWalkForwardError("max_candidates must be a positive integer")
    max_total = payload.get("max_total_candidates", DEFAULT_MAX_TOTAL_CANDIDATES)
    if isinstance(max_total, bool) or not isinstance(max_total, int) or max_total < 1:
        raise run_errors.InvalidWalkForwardError("max_total_candidates must be a positive integer")

    return WalkForwardRequest(
        strategy=strategy.strip(),
        market=base.market,
        execution=base.execution,
        window=window,
        strategy_grid=dict(strategy_grid),
        execution_grid=dict(execution_grid),
        objective=optimization.Objective(metric, direction),
        max_folds=min(max_folds, HARD_MAX_FOLDS),
        max_candidates=min(max_candidates, optimization.HARD_MAX_CANDIDATES),
        max_total_candidates=min(max_total, HARD_MAX_TOTAL_CANDIDATES),
        account=run_request.parse_account(payload.get("account")),
        instrument=run_request.parse_instrument(payload.get("instrument")),
    )


# --------------------------------------------------------------------------
# Deterministic window generation (no look-ahead)
# --------------------------------------------------------------------------


def _parse_date(value, label: str) -> date:
    text = str(value or "").strip()
    try:
        return date.fromisoformat(text[:10])
    except ValueError as exc:
        raise run_errors.InvalidWalkForwardError(f"{label} must be an ISO date (YYYY-MM-DD)") from exc


def generate_folds(overall_start, overall_end, window: WindowConfig) -> list[Fold]:
    """Chronological, non-look-ahead folds inside ``[overall_start, overall_end]``.

    Rolling: the training window moves forward by ``step_days``.
    Anchored: the training start is fixed; the training end expands.
    A fold is emitted only when its test window fits inside the overall range.
    """
    start = _parse_date(overall_start, "market.start")
    end = _parse_date(overall_end, "market.end")
    if end < start:
        raise run_errors.InvalidWalkForwardError("market.end must not precede market.start")
    if window.mode not in WINDOW_MODES:
        raise run_errors.InvalidWalkForwardError(f"window.mode must be one of {list(WINDOW_MODES)}")
    for name in ("train_days", "test_days", "step_days"):
        _positive_int(f"window.{name}", getattr(window, name))

    folds: list[Fold] = []
    cursor = start
    index = 0
    while True:
        train_start = start if window.mode == "anchored" else cursor
        train_end = cursor + timedelta(days=window.train_days - 1)
        test_start = train_end + timedelta(days=1)
        test_end = test_start + timedelta(days=window.test_days - 1)
        if test_end > end:
            break
        folds.append(Fold(
            index=index,
            train_start=train_start.isoformat(),
            train_end=train_end.isoformat(),
            test_start=test_start.isoformat(),
            test_end=test_end.isoformat(),
        ))
        index += 1
        cursor = cursor + timedelta(days=window.step_days)

    if not folds:
        raise run_errors.InvalidWalkForwardError(
            "window configuration produces no folds within the overall range"
        )
    return folds


def _with_range(base: run_request.MarketRequest, start: str, end: str) -> run_request.MarketRequest:
    return run_request.MarketRequest(
        symbol=base.symbol,
        timeframe=base.timeframe,
        start=start,
        end=end,
        data_source=base.data_source,
    )


def build_study_plan(request: WalkForwardRequest, declared: dict) -> tuple[list[Fold], list[dict]]:
    """Validate grids + windows + limits; return ``(folds, candidate_combinations)``."""
    opt_request = optimization.OptimizationRequest(
        strategy=request.strategy,
        market=request.market,
        execution=request.execution,
        strategy_grid=request.strategy_grid,
        execution_grid=request.execution_grid,
        objective=request.objective,
        max_candidates=request.max_candidates,
        account=request.account,
        instrument=request.instrument,
    )
    combos = optimization.build_combinations(opt_request, declared)
    folds = generate_folds(request.market.start, request.market.end, request.window)

    if len(folds) > request.max_folds:
        raise run_errors.WalkForwardLimitError(
            f"{len(folds)} folds exceed max_folds={request.max_folds}; widen the step or limits"
        )
    total = len(folds) * len(combos)
    if total > request.max_total_candidates:
        raise run_errors.WalkForwardLimitError(
            f"{total} total candidate executions exceed max_total_candidates="
            f"{request.max_total_candidates}; narrow the grid or limits"
        )
    return folds, combos


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------


def _fold_metrics(metrics) -> dict:
    return {
        "total_trades": metrics.total_trades,
        "wins": metrics.wins,
        "losses": metrics.losses,
        "win_rate": metrics.win_rate,
        "net_pnl": metrics.net_pnl,
        "gross_profit": metrics.gross_profit,
        "gross_loss": metrics.gross_loss,
        "profit_factor": metrics.profit_factor,
        "max_drawdown": metrics.max_drawdown,
        "expectancy": metrics.expectancy,
    }


def _downsample(values: list, limit: int = MAX_EQUITY_POINTS) -> list:
    if len(values) <= limit:
        return values
    step = len(values) / limit
    out = [values[int(i * step)] for i in range(limit)]
    out[-1] = values[-1]
    return out


def _fold_equity(equity: list) -> list[dict]:
    points = [
        {"timestamp": p.timestamp, "cumulative_pnl": p.cumulative_pnl}
        for p in equity
        if isinstance(p.cumulative_pnl, (int, float))
    ]
    return _downsample(points)


def run_walk_forward(
    request: WalkForwardRequest,
    *,
    runs: str | Path = "runs",
    studies: str | Path = optimization.DEFAULT_STUDIES,
    walkforwards: str | Path = DEFAULT_WALKFORWARDS,
    public: str | Path = run_backtest.publish.DEFAULT_PUBLIC,
) -> dict:
    """Execute a deterministic walk-forward study sequentially. Returns the study."""
    strategy, strategy_path = run_backtest.resolve_strategy(request.strategy)
    declared = strategy.parameters()
    folds, combos = build_study_plan(request, declared)
    candidate_count = len(combos)
    meta = strategy.metadata()

    wf_id = f"wf_{uuid.uuid4().hex[:8]}"
    created_at = utc_now_iso()

    fold_records: list[dict] = []
    for fold in folds:
        record = {
            **fold.to_dict(),
            "optimization_study_id": None,
            "selected_run_id": None,
            "selected_parameters": None,
            "training_objective_value": None,
            "test_run_id": None,
            "test_metrics": None,
            "equity": [],
            "status": "pending",
            "failure": None,
        }

        train_market = _with_range(request.market, fold.train_start, fold.train_end)
        opt_request = optimization.OptimizationRequest(
            strategy=request.strategy,
            market=train_market,
            execution=request.execution,
            strategy_grid=request.strategy_grid,
            execution_grid=request.execution_grid,
            objective=request.objective,
            max_candidates=request.max_candidates,
        )
        try:
            study = optimization.run_study(
                opt_request, runs=runs, studies=studies, public=public
            )
        except run_errors.RunError as exc:
            record.update(status="train_failed", failure={"code": exc.code, "message": exc.message})
            fold_records.append(record)
            continue
        except Exception as exc:  # noqa: BLE001
            record.update(status="train_failed",
                          failure={"code": "train_failed", "message": str(exc)})
            fold_records.append(record)
            continue

        record["optimization_study_id"] = study["study_id"]
        best_run_id = study.get("best_run_id")
        best = next(
            (c for c in study.get("candidates", []) if c["run_id"] == best_run_id), None
        )
        if best is None or best.get("objective_value") is None:
            record.update(status="train_failed",
                          failure={"code": "no_ranked_candidate",
                                   "message": "training optimization produced no rankable candidate"})
            fold_records.append(record)
            continue

        selected_parameters = dict(best.get("parameters") or {})
        record["selected_run_id"] = best_run_id
        record["selected_parameters"] = selected_parameters
        record["training_objective_value"] = best.get("objective_value")

        # Out-of-sample test: parameters come ONLY from this fold's training best.
        test_run_id = f"{wf_id}_f{fold.index:02d}"
        record["test_run_id"] = test_run_id
        test_market = _with_range(request.market, fold.test_start, fold.test_end)
        test_request = run_request.BacktestRunRequest(
            strategy=run_request.StrategyRequest(
                selector=request.strategy,
                parameters=selected_parameters,
                file=str(strategy_path) if strategy_path is not None else None,
            ),
            market=test_market,
            execution=run_request.ExecutionRequest(
                stop_distance=request.execution.stop_distance,
                take_profit_distance=request.execution.take_profit_distance,
            ),
            account=request.account,
            instrument=request.instrument,
            publish=False,  # OOS runs stay local (Task 17)
        )
        try:
            info = run_backtest.execute_run(
                test_request, out=str(runs), report=True, public=str(public), run_id=test_run_id
            )
            result = info["result"]
            record["test_metrics"] = _fold_metrics(result.metrics)
            record["equity"] = _fold_equity(result.equity)
            record["status"] = "ok"
        except run_errors.RunError as exc:
            record.update(status="test_failed", failure={"code": exc.code, "message": exc.message})
        except Exception as exc:  # noqa: BLE001
            record.update(status="test_failed",
                          failure={"code": "test_failed", "message": str(exc)})
        fold_records.append(record)

    aggregate = aggregate_oos(fold_records)
    status = _overall_status(fold_records)

    study = {
        "wf_id": wf_id,
        "created_at": created_at,
        "status": status,
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
        "window": request.window.to_dict(),
        "objective": request.objective.to_dict(),
        "strategy_grid": dict(request.strategy_grid),
        "execution_grid": dict(request.execution_grid),
        "candidates_per_fold": candidate_count,
        "fold_count": len(fold_records),
        "folds": fold_records,
        "aggregate": aggregate,
    }
    save_walk_forward(walkforwards, study)
    return study


def _overall_status(folds: list[dict]) -> str:
    if not folds:
        return "failed"
    ok = sum(1 for f in folds if f["status"] == "ok")
    if ok == len(folds):
        return "completed"
    if ok > 0:
        return "partial"
    return "failed"


def aggregate_oos(folds: list[dict]) -> dict:
    """Aggregate ONLY out-of-sample test runs (never training metrics).

    Metrics that cannot be combined honestly stay ``None``. Profit factor is
    recomputed from summed gross profit / gross loss, never averaged.
    """
    aggregate = {
        "fold_count": len(folds),
        "completed_folds": 0,
        "total_oos_trades": None,
        "oos_wins": None,
        "oos_losses": None,
        "oos_win_rate": None,
        "total_oos_net_pnl": None,
        "average_fold_pnl": None,
        "profitable_folds": None,
        "profitable_fold_pct": None,
        "worst_fold_pnl": None,
        "best_fold_pnl": None,
        "oos_profit_factor": None,
    }
    completed = [f for f in folds if f["status"] == "ok" and f.get("test_metrics")]
    if not completed:
        return aggregate

    metrics = [f["test_metrics"] for f in completed]
    total_trades = sum(m["total_trades"] for m in metrics)
    wins = sum(m["wins"] for m in metrics)
    losses = sum(m["losses"] for m in metrics)
    net = sum(m["net_pnl"] for m in metrics)
    gross_profit = sum(m["gross_profit"] for m in metrics)
    gross_loss = sum(m["gross_loss"] for m in metrics)
    fold_pnls = [m["net_pnl"] for m in metrics]
    profitable = sum(1 for p in fold_pnls if p > 0)

    aggregate.update({
        "completed_folds": len(completed),
        "total_oos_trades": total_trades,
        "oos_wins": wins,
        "oos_losses": losses,
        "oos_win_rate": (wins / total_trades * 100.0) if total_trades else None,
        "total_oos_net_pnl": net,
        "average_fold_pnl": net / len(completed),
        "profitable_folds": profitable,
        "profitable_fold_pct": profitable / len(completed) * 100.0,
        "worst_fold_pnl": min(fold_pnls),
        "best_fold_pnl": max(fold_pnls),
        "oos_profit_factor": (gross_profit / abs(gross_loss)) if gross_loss else None,
    })
    return aggregate


# --------------------------------------------------------------------------
# Walk-forward study archive
# --------------------------------------------------------------------------


def validate_wf_id(wf_id) -> str:
    if not isinstance(wf_id, str) or not wf_id.strip() or not _WF_ID_RE.match(wf_id.strip()):
        raise run_errors.InvalidWalkForwardIdError(f"invalid walk-forward id: {wf_id!r}")
    return wf_id.strip()


def _wf_dir(walkforwards_root, wf_id: str) -> Path:
    base = Path(walkforwards_root)
    target = base / validate_wf_id(wf_id)
    try:
        target.resolve().relative_to(base.resolve())
    except ValueError as exc:
        raise run_errors.InvalidWalkForwardIdError(f"invalid walk-forward id: {wf_id!r}") from exc
    return target


def save_walk_forward(walkforwards_root, study: dict) -> Path:
    directory = _wf_dir(walkforwards_root, study["wf_id"])
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "study.json"
    path.write_text(json.dumps(study, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def load_walk_forward(walkforwards_root, wf_id: str) -> dict:
    path = _wf_dir(walkforwards_root, wf_id) / "study.json"
    if not path.is_file():
        raise run_errors.WalkForwardNotFoundError(f"walk-forward study not found: {wf_id}")
    return json.loads(path.read_text(encoding="utf-8"))


def list_walk_forwards(walkforwards_root) -> list[dict]:
    base = Path(walkforwards_root)
    if not base.is_dir():
        return []
    summaries = []
    for path in base.glob("*/study.json"):
        try:
            study = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        agg = study.get("aggregate") or {}
        summaries.append({
            "wf_id": study.get("wf_id"),
            "created_at": study.get("created_at"),
            "status": study.get("status"),
            "strategy": study.get("strategy", {}).get("name", ""),
            "fold_count": study.get("fold_count", 0),
            "objective": study.get("objective", {}),
            "oos_net_pnl": agg.get("total_oos_net_pnl"),
            "profitable_folds": agg.get("profitable_folds"),
        })
    summaries.sort(
        key=lambda s: (str(s.get("created_at") or ""), str(s.get("wf_id") or "")), reverse=True
    )
    return summaries


# Re-exported helpers used by tests / callers that want finite-metric safety.
def is_finite(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
