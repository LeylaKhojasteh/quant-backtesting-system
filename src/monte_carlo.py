"""Monte Carlo robustness analysis over an EXISTING archived run.

Reference: ARCHITECTURE (Existing archived BacktestResult -> MonteCarloRequest
-> trade resampling -> Monte Carlo Study -> Research Workspace).

This is a robustness-analysis layer. It:

- operates only on the CLOSED TRADE RESULTS already produced by the verified
  engine (the source archive stays immutable),
- never calls ``src/backtest.py`` or ``execute_run()``,
- never creates an alternative BacktestResult,
- is fully deterministic given (run_id, method, simulations, seed).

Basis selection (never mixed):

- account basis when the source run has ``account.available`` and per-trade net
  account PnL — simulates a realized balance path from the account's starting
  balance;
- otherwise price basis — simulates cumulative price-term PnL from 0.

Methods:

- ``shuffle``   : permutation of the observed trades WITHOUT replacement
                  (terminal PnL is constant; useful for drawdown / streak / path).
- ``bootstrap`` : sample the same number of trades WITH replacement
                  (terminal outcome and drawdown distributions).

Not implemented here (future work): risk of ruin, block bootstrap, parametric
models, synthetic price generation, parallel simulation.
"""

from __future__ import annotations

import json
import math
import random
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

from src import run_errors, run_library
from src.result_contract import utc_now_iso

DEFAULT_MONTE_CARLO = "montecarlo"
DEFAULT_SIMULATIONS = 1000
HARD_MAX_SIMULATIONS = 5000
MIN_TRADES = 2
METHODS = ("shuffle", "bootstrap")
MAX_ENVELOPE_POINTS = 2000
SMALL_SAMPLE_THRESHOLD = 30

_MC_ID_RE = re.compile(r"^mc_[A-Za-z0-9_]+$")


@dataclass
class MonteCarloRequest:
    """Inputs for one Monte Carlo study (separate from any run/opt contract)."""

    run_id: str
    method: str = "shuffle"
    simulations: int = DEFAULT_SIMULATIONS
    seed: int = 0


def parse_monte_carlo_payload(payload) -> MonteCarloRequest:
    if not isinstance(payload, dict):
        raise run_errors.InvalidMonteCarloError("request body must be a JSON object")
    unknown = sorted(set(payload) - {"run_id", "method", "simulations", "seed"})
    if unknown:
        raise run_errors.InvalidMonteCarloError(f"unknown request field(s): {unknown}")

    run_id = payload.get("run_id")
    if not isinstance(run_id, str) or not run_id.strip():
        raise run_errors.InvalidMonteCarloError("run_id is required")

    method = payload.get("method", "shuffle")
    if method not in METHODS:
        raise run_errors.InvalidMonteCarloError(f"method must be one of {list(METHODS)}")

    simulations = payload.get("simulations", DEFAULT_SIMULATIONS)
    if isinstance(simulations, bool) or not isinstance(simulations, int) or simulations < 1:
        raise run_errors.InvalidMonteCarloError("simulations must be a positive integer")
    if simulations > HARD_MAX_SIMULATIONS:
        raise run_errors.MonteCarloLimitError(
            f"simulations={simulations} exceeds the hard maximum of {HARD_MAX_SIMULATIONS}"
        )

    seed = payload.get("seed", 0)
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise run_errors.InvalidMonteCarloError("seed must be an integer")

    return MonteCarloRequest(run_id=run_id.strip(), method=method, simulations=simulations, seed=seed)


# --------------------------------------------------------------------------
# Statistics helpers
# --------------------------------------------------------------------------


def _percentile(values: list[float], q: float) -> float | None:
    """Linear-interpolation percentile (matches common library defaults)."""
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * (q / 100.0)
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(ordered) - 1)
    frac = pos - lo
    return ordered[lo] + (ordered[hi] - ordered[lo]) * frac


def _round(value, digits: int = 6):
    if value is None:
        return None
    return round(float(value), digits)


def summary_stats(values: list[float], bins: int = 12) -> dict | None:
    """min / p05 / median / p95 / max over finite values plus a compact histogram."""
    finite = [v for v in values if isinstance(v, (int, float)) and math.isfinite(v)]
    if not finite:
        return None
    count = len(finite)
    if count == 1 or min(finite) == max(finite):
        histogram = {
            "from": _round(min(finite)),
            "to": _round(max(finite)),
            "bins": [{"from": _round(min(finite)), "to": _round(max(finite)), "count": count}],
        }
    else:
        lo = min(finite)
        hi = max(finite)
        width = (hi - lo) / bins
        counts = [0] * bins
        for v in finite:
            idx = min(bins - 1, int((v - lo) / width))
            counts[idx] += 1
        histogram = {
            "from": _round(lo),
            "to": _round(hi),
            "bins": [
                {"from": _round(lo + i * width), "to": _round(lo + (i + 1) * width), "count": counts[i]}
                for i in range(bins)
            ],
        }
    return {
        "min": _round(min(finite)),
        "p05": _round(_percentile(finite, 5)),
        "median": _round(_percentile(finite, 50)),
        "p95": _round(_percentile(finite, 95)),
        "max": _round(max(finite)),
        "histogram": histogram,
    }


def max_drawdown(path: list[float]) -> float:
    """Peak-to-trough decline of a cumulative path (peak includes the start)."""
    peak = path[0]
    worst = 0.0
    for value in path:
        if value > peak:
            peak = value
        decline = peak - value
        if decline > worst:
            worst = decline
    return worst


def max_drawdown_pct(path: list[float]) -> float | None:
    """Drawdown relative to the running peak balance, percent; None if peak <= 0."""
    peak = path[0]
    worst = 0.0
    for value in path:
        if value > peak:
            peak = value
        decline = peak - value
        if decline > worst:
            worst = decline
    return (worst / peak) * 100.0 if peak > 0 else None


def max_losing_streak(pnls: list[float]) -> int:
    """Longest run of strictly negative trade PnL.

    A zero-PnL trade is NOT a loss: it breaks the streak (pnl < 0 is required).
    """
    best = 0
    current = 0
    for pnl in pnls:
        if pnl < 0:
            current += 1
            if current > best:
                best = current
        else:
            current = 0
    return best


# --------------------------------------------------------------------------
# Basis + source loading
# --------------------------------------------------------------------------


def _load_source(runs: str | Path, run_id: str, public: str | Path) -> tuple[dict, dict]:
    detail = run_library.run_detail(runs, run_id, public)
    result_path = Path(runs) / run_library.validate_run_id(run_id) / "result.json"
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise run_errors.RunNotFoundError(f"archived run not found: {run_id}") from exc
    return detail, result


def _select_basis(result: dict) -> dict:
    """Return ``{basis, start, pnls, path, currency, starting_balance}``.

    Account basis is chosen only when the archive reports account availability
    and a usable realized balance curve exists. Otherwise the price basis is
    used. Missing account values are never reconstructed.
    """
    account = result.get("account") or {}
    curve = account.get("balance_curve") or []
    model = account.get("model") or {}
    if account.get("available") and len(curve) >= MIN_TRADES:
        start = model.get("starting_balance")
        if not isinstance(start, (int, float)):
            start = (account.get("metrics") or {}).get("starting_balance")
        balances = [p.get("realized_balance") for p in curve]
        if isinstance(start, (int, float)) and all(isinstance(b, (int, float)) and math.isfinite(b) for b in balances):
            path = [float(start)] + [float(b) for b in balances]
            pnls = [path[i] - path[i - 1] for i in range(1, len(path))]
            return {
                "basis": "account",
                "start": float(start),
                "pnls": pnls,
                "path": path,
                "currency": account.get("account_currency"),
                "starting_balance": float(start),
            }

    equity = result.get("equity") or []
    cumulative = [p.get("cumulative_pnl") for p in equity]
    if not cumulative or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in cumulative):
        raise run_errors.InvalidMonteCarloError(
            "source run has no usable equity points for Monte Carlo analysis"
        )
    path = [0.0] + [float(v) for v in cumulative]
    pnls = [path[i] - path[i - 1] for i in range(1, len(path))]
    return {
        "basis": "price",
        "start": 0.0,
        "pnls": pnls,
        "path": path,
        "currency": None,
        "starting_balance": None,
    }


# --------------------------------------------------------------------------
# Engine
# --------------------------------------------------------------------------


def run_monte_carlo(
    request: MonteCarloRequest,
    *,
    runs: str | Path = run_library.DEFAULT_RUNS,
    public: str | Path = "Quant-Backtest-Lab/public",
    montecarlo: str | Path = DEFAULT_MONTE_CARLO,
) -> dict:
    detail, result = _load_source(runs, request.run_id, public)
    selected = _select_basis(result)
    pnls: list[float] = selected["pnls"]
    n = len(pnls)
    if n < MIN_TRADES:
        raise run_errors.InvalidMonteCarloError(
            f"source run has {n} closed trades; at least {MIN_TRADES} are required"
        )
    if not all(math.isfinite(p) for p in pnls):
        raise run_errors.InvalidMonteCarloError("source run contains non-finite trade PnL")

    basis = selected["basis"]
    account_basis = basis == "account"
    start = selected["start"]

    rng = random.Random(request.seed)
    simulations = request.simulations
    paths: list[list[float]] = []
    terminal_pnl: list[float] = []
    dd_amount: list[float] = []
    dd_pct: list[float] = []
    streak: list[int] = []

    for _ in range(simulations):
        if request.method == "shuffle":
            perm = list(pnls)
            rng.shuffle(perm)
        else:  # bootstrap: same length, WITH replacement
            perm = [pnls[rng.randrange(n)] for _ in range(n)]
        path = [start]
        acc = start
        for p in perm:
            acc += p
            path.append(acc)
        paths.append(path)
        terminal_pnl.append(acc - start if account_basis else acc)
        dd_amount.append(max_drawdown(path))
        if account_basis:
            pct = max_drawdown_pct(path)
            if pct is not None:
                dd_pct.append(pct)
        streak.append(max_losing_streak(perm))

    summary = {
        "simulations": simulations,
        "seed": request.seed,
        "method": request.method,
        "basis": basis,
        "trade_count": n,
        "max_drawdown": summary_stats(dd_amount),
        "max_losing_streak": summary_stats([float(s) for s in streak]),
        "terminal_pnl": summary_stats(terminal_pnl),
        # Shuffle terminal PnL is invariant by construction; never present variance.
        "terminal_pnl_constant": request.method == "shuffle",
    }
    if account_basis:
        summary["max_drawdown_pct"] = summary_stats(dd_pct)
        summary["ending_balance"] = summary_stats([start + t for t in terminal_pnl])
        return_pcts = [((start + t) - start) / start * 100.0 for t in terminal_pnl] if start else []
        summary["return_pct"] = summary_stats(return_pcts)
    else:
        summary["max_drawdown_pct"] = None
        summary["ending_balance"] = None
        summary["return_pct"] = None

    if request.method == "bootstrap":
        negative = sum(1 for t in terminal_pnl if t < 0)
        summary["probability_terminal_negative"] = _round(negative / simulations)
        summary["probability_terminal_negative_label"] = (
            "Resampling probability that terminal PnL is negative — not a guaranteed "
            "future loss probability."
        )
    else:
        summary["probability_terminal_negative"] = None
        summary["probability_terminal_negative_label"] = (
            "Not applicable to shuffle: terminal PnL is identical in every path."
        )

    # Compact percentile envelope by trade index (index 0 = start, before any trade).
    envelope = []
    for index in range(n + 1):
        column = [paths[s][index] for s in range(simulations)]
        envelope.append({
            "index": index,
            "p05": _round(_percentile(column, 5)),
            "median": _round(_percentile(column, 50)),
            "p95": _round(_percentile(column, 95)),
        })
    if len(envelope) > MAX_ENVELOPE_POINTS:
        step = len(envelope) / MAX_ENVELOPE_POINTS
        reduced = [envelope[int(i * step)] for i in range(MAX_ENVELOPE_POINTS)]
        reduced[-1] = envelope[-1]
        envelope = reduced

    original_path = [_round(v) for v in selected["path"]]
    source_actual = {
        "net_pnl": _round(result.get("metrics", {}).get("net_pnl")),
        "trade_count": result.get("metrics", {}).get("total_trades"),
        "max_drawdown": _round(result.get("metrics", {}).get("max_drawdown")),
    }
    if account_basis:
        source_actual["net_pnl"] = _round((result.get("account", {}).get("metrics") or {}).get("net_pnl"))
        source_actual["ending_balance"] = _round(
            (result.get("account", {}).get("metrics") or {}).get("ending_balance")
        )
        source_actual["return_pct"] = _round(
            (result.get("account", {}).get("metrics") or {}).get("total_return_pct")
        )

    mc_id = f"mc_{uuid.uuid4().hex[:8]}"
    study = {
        "study_id": mc_id,
        "created_at": utc_now_iso(),
        "status": "completed",
        "source": {
            "run_id": request.run_id,
            "strategy": detail.get("strategy"),
            "strategy_selector": detail.get("strategy_selector"),
            "symbol": detail.get("symbol"),
            "timeframe": detail.get("timeframe"),
            "start": detail.get("start"),
            "end": detail.get("end"),
        },
        "method": request.method,
        "basis": basis,
        "simulations": simulations,
        "seed": request.seed,
        "trade_count": n,
        "small_sample": n < SMALL_SAMPLE_THRESHOLD,
        "account": (
            {"currency": selected["currency"], "starting_balance": _round(selected["starting_balance"])}
            if account_basis
            else None
        ),
        "labels": {
            "path": (
                f"Simulated Realized Balance — {selected['currency'] or 'account currency'}"
                if account_basis
                else "Simulated Cumulative PnL — price terms"
            ),
            "original": (
                f"Original realized balance — {selected['currency'] or 'account currency'}"
                if account_basis
                else "Original cumulative PnL — price terms"
            ),
        },
        "summary": summary,
        "source_actual": source_actual,
        "envelope": envelope,
        "original_path": original_path,
    }
    save_monte_carlo(montecarlo, study)
    return study


# --------------------------------------------------------------------------
# Archive
# --------------------------------------------------------------------------


def validate_mc_id(mc_id) -> str:
    if not isinstance(mc_id, str) or not mc_id.strip() or not _MC_ID_RE.match(mc_id.strip()):
        raise run_errors.InvalidMonteCarloIdError(f"invalid Monte Carlo id: {mc_id!r}")
    return mc_id.strip()


def _mc_dir(root, mc_id: str) -> Path:
    base = Path(root)
    target = base / validate_mc_id(mc_id)
    try:
        target.resolve().relative_to(base.resolve())
    except ValueError as exc:
        raise run_errors.InvalidMonteCarloIdError(f"invalid Monte Carlo id: {mc_id!r}") from exc
    return target


def save_monte_carlo(root, study: dict) -> Path:
    directory = _mc_dir(root, study["study_id"])
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "study.json"
    path.write_text(json.dumps(study, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def load_monte_carlo(root, mc_id: str) -> dict:
    path = _mc_dir(root, mc_id) / "study.json"
    if not path.is_file():
        raise run_errors.MonteCarloNotFoundError(f"Monte Carlo study not found: {mc_id}")
    return json.loads(path.read_text(encoding="utf-8"))


def list_monte_carlo(root) -> list[dict]:
    base = Path(root)
    if not base.is_dir():
        return []
    summaries = []
    for path in base.glob("*/study.json"):
        try:
            study = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        source = study.get("source") or {}
        summaries.append({
            "study_id": study.get("study_id"),
            "created_at": study.get("created_at"),
            "status": study.get("status"),
            "source_run_id": source.get("run_id"),
            "strategy": (source.get("strategy") or {}).get("name", ""),
            "method": study.get("method"),
            "basis": study.get("basis"),
            "simulations": study.get("simulations"),
            "seed": study.get("seed"),
        })
    summaries.sort(
        key=lambda s: (str(s.get("created_at") or ""), str(s.get("study_id") or "")), reverse=True
    )
    return summaries
