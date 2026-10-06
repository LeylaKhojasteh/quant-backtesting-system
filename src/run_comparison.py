"""Run Comparison Service.

Reference: ARCHITECTURE (Existing Run Archives -> Run Comparison Service ->
Bridge -> Research Workspace UI). Reads archived BacktestResult/config data and
derives compact comparison data. It never recalculates trading logic and never
coerces an unavailable metric to zero.

Honest incompatibility handling: differing symbols / timeframes / date ranges /
strategies are allowed but explicitly flagged, and no universal score or
arbitrary normalization is invented.
"""

from __future__ import annotations

import json
from pathlib import Path

from src import run_errors, run_library

MIN_RUNS = 2
MAX_RUNS = 10
MAX_EQUITY_POINTS = 200


def _load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _downsample(values: list, limit: int = MAX_EQUITY_POINTS) -> list:
    if len(values) <= limit:
        return values
    step = len(values) / limit
    out = [values[int(i * step)] for i in range(limit)]
    out[-1] = values[-1]
    return out


def _comparison_entry(runs_dir, run_id, public_dir) -> dict:
    detail = run_library.run_detail(runs_dir, run_id, public_dir)
    directory = Path(runs_dir) / run_id
    result = _load_json(directory / "result.json") or {}
    metrics = result.get("metrics") or {}
    trades = result.get("trades") or []

    long_trades = sum(1 for t in trades if t.get("direction") == "LONG")
    short_trades = sum(1 for t in trades if t.get("direction") == "SHORT")
    total = metrics.get("total_trades")
    net = metrics.get("net_pnl")
    avg_trade = (net / total) if (isinstance(total, int) and total > 0 and isinstance(net, (int, float))) else None

    equity = result.get("equity") or []
    cumulative = [p.get("cumulative_pnl") for p in equity if isinstance(p.get("cumulative_pnl"), (int, float))]
    quality = result.get("data_quality") or {}
    dq_status = "measured" if quality.get("tick_count") is not None else "unavailable"

    account = result.get("account") or {}
    account_ready = bool(account.get("available"))
    account_metrics = account.get("metrics") or {}
    account_model = account.get("model") or {}
    account_instrument = account.get("instrument") or {}

    return {
        "run_id": run_id,
        "created_at": detail.get("created_at"),
        "strategy": detail.get("strategy"),
        "strategy_selector": detail.get("strategy_selector"),
        "strategy_file": detail.get("strategy_file"),
        "symbol": detail.get("symbol"),
        "timeframe": detail.get("timeframe"),
        "start": detail.get("start"),
        "end": detail.get("end"),
        "parameters": detail.get("parameters") or {},
        "execution": detail.get("execution") or {},
        "total_trades": total,
        "long_trades": long_trades,
        "short_trades": short_trades,
        "wins": metrics.get("wins"),
        "losses": metrics.get("losses"),
        "win_rate": metrics.get("win_rate"),
        "net_pnl": net,
        "avg_trade": avg_trade,
        "profit_factor": metrics.get("profit_factor"),
        "max_drawdown": metrics.get("max_drawdown"),
        "expectancy": metrics.get("expectancy"),
        "data_quality_status": dq_status,
        "account_ready": account_ready,
        "account_currency": account.get("account_currency") if account_ready else None,
        "account_net_pnl": account_metrics.get("net_pnl") if account_ready else None,
        "account_return_pct": account_metrics.get("total_return_pct") if account_ready else None,
        "account_commission": account_metrics.get("total_commission") if account_ready else None,
        "account_max_drawdown": account_metrics.get("max_drawdown") if account_ready else None,
        "account_position_size_lots": account_model.get("position_size_lots") if account_ready else None,
        "account_contract_size": account_instrument.get("contract_size") if account_ready else None,
        "equity": _downsample(cumulative),
    }


def compare_runs(runs_dir, public_dir, run_ids) -> dict:
    """Compare 2..N existing runs. Raises structured errors on invalid input."""
    if not isinstance(run_ids, list) or not all(isinstance(r, str) for r in run_ids):
        raise run_errors.InvalidParametersError("run_ids must be a list of run ids")
    if len(run_ids) < MIN_RUNS:
        raise run_errors.InvalidParametersError(f"comparison needs at least {MIN_RUNS} runs")
    if len(run_ids) > MAX_RUNS:
        raise run_errors.InvalidParametersError(f"comparison supports at most {MAX_RUNS} runs")
    if len(set(run_ids)) != len(run_ids):
        raise run_errors.InvalidParametersError("run_ids must be unique")

    entries = []
    for run_id in run_ids:
        run_library.validate_run_id(run_id)
        if not (Path(runs_dir) / run_id / "result.json").is_file():
            raise run_errors.RunNotFoundError(f"archived run not found: {run_id}")
        entries.append(_comparison_entry(runs_dir, run_id, public_dir))

    symbols = {e["symbol"] for e in entries}
    timeframes = {e["timeframe"] for e in entries}
    strategies = {(e["strategy"] or {}).get("name") for e in entries}
    ranges = {(e["start"], e["end"]) for e in entries}

    warnings = []
    if len(symbols) > 1:
        warnings.append(
            f"Symbols differ ({', '.join(sorted(str(s) for s in symbols))}); "
            "raw price-term PnL is not directly comparable across instruments."
        )
    if len(timeframes) > 1:
        warnings.append(
            f"Timeframes differ ({', '.join(sorted(str(t) for t in timeframes))}); "
            "trade counts and durations cover different bar sizes."
        )
    if len(ranges) > 1:
        warnings.append("Tested date ranges differ; runs cover different market periods.")
    if len(strategies) > 1:
        warnings.append(
            f"Strategies differ ({', '.join(sorted(str(s) for s in strategies))}); "
            "this compares configurations, not a single strategy."
        )
    warnings.append(
        "Metrics are in price terms; no account model is present, so no normalized "
        "return comparison is shown."
    )

    ready_flags = {e["account_ready"] for e in entries}
    account_currencies = {e["account_currency"] for e in entries if e["account_ready"]}
    account_positions = {e["account_position_size_lots"] for e in entries if e["account_ready"]}
    account_contracts = {e["account_contract_size"] for e in entries if e["account_ready"]}
    all_account_ready = ready_flags == {True}
    any_account_ready = True in ready_flags

    if any_account_ready and not all_account_ready:
        warnings.append(
            "Some runs use an account model and others do not; account-level PnL is not "
            "comparable across these runs."
        )
    elif all_account_ready:
        if len(account_currencies) > 1:
            warnings.append(
                f"Account currencies differ ({', '.join(sorted(str(c) for c in account_currencies))}); "
                "raw account PnL is not normalized across currencies."
            )
        if len(account_positions) > 1:
            warnings.append(
                f"Position sizes differ ({', '.join(sorted(str(p) for p in account_positions))} lots); "
                "raw account PnL is not directly comparable."
            )
        if len(account_contracts) > 1:
            warnings.append(
                f"Instrument contract sizes differ ({', '.join(sorted(str(c) for c in account_contracts))}); "
                "raw account PnL is not directly comparable."
            )

    difficulty = {
        "symbol": len(symbols) > 1,
        "timeframe": len(timeframes) > 1,
        "date_range": len(ranges) > 1,
        "strategy": len(strategies) > 1,
        "account_currency": len(account_currencies) > 1,
        "account_model": any_account_ready and not all_account_ready,
        "account_comparable": all_account_ready
        and len(account_currencies) <= 1
        and len(account_positions) <= 1
        and len(account_contracts) <= 1,
    }

    equity = {e["run_id"]: e.pop("equity") for e in entries}
    return {
        "runs": entries,
        "warnings": warnings,
        "differences": difficulty,
        "equity": equity,
        "units": "price-terms",
    }
