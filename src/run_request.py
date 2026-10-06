"""BacktestRunRequest — the inputs to one backtest run.

Reference: ``ARCHITECTURE.md`` Section 4 (Result Contract) and the layer split:

- ``BacktestRunRequest`` = inputs (strategy + market + execution settings)
- ``BacktestResult``      = outputs (trades, equity, metrics, ...)

Engine settings (``stop_distance`` / ``take_profit_distance``) are execution
configuration. They are never part of ``Strategy.parameters()``; the CLI rejects
attempts to set them through ``--param``.

This module is plain data plus small pure helpers (no pandas, no engine).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from src.run_errors import (
    InvalidDatasetError,
    InvalidExecutionError,
    InvalidParametersError,
    InvalidStrategyError,
)

#: CLI/engine setting names that are execution configuration, not strategy
#: parameters. Passing them through ``--param`` is rejected.
ENGINE_SETTING_NAMES = ("stop_distance", "take_profit_distance")


@dataclass
class StrategyRequest:
    """Which strategy to run and the validated parameter overrides requested."""

    selector: str
    parameters: dict = field(default_factory=dict)
    file: str | None = None


@dataclass
class MarketRequest:
    """The dataset and market window a run is evaluated on."""

    symbol: str
    timeframe: str
    start: str | None = None
    end: str | None = None
    data_source: str = ""


@dataclass
class ExecutionRequest:
    """Strategy-independent execution settings consumed by the engine."""

    stop_distance: float = 5.0
    take_profit_distance: float = 10.0


@dataclass
class BacktestRunRequest:
    """Everything needed to reproduce a run (inputs only)."""

    strategy: StrategyRequest
    market: MarketRequest
    execution: ExecutionRequest
    #: Orchestration-only flag (archive -> report -> publish). It is not a run
    #: input, so it is deliberately excluded from the archived run config.
    publish: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def coerce_parameter(key: str, raw: str, default):
    """Parse a CLI string using the declared default's type.

    Supports int, float, bool and string. Malformed values raise clearly.
    """
    if isinstance(default, bool):
        low = raw.strip().lower()
        if low in ("true", "1"):
            return True
        if low in ("false", "0"):
            return False
        raise InvalidParametersError(f"parameter {key!r} expects bool (true/false), got {raw!r}")
    if isinstance(default, int) and not isinstance(default, bool):
        try:
            return int(raw)
        except ValueError as exc:
            raise InvalidParametersError(f"parameter {key!r} expects int, got {raw!r}") from exc
    if isinstance(default, float):
        try:
            return float(raw)
        except ValueError as exc:
            raise InvalidParametersError(f"parameter {key!r} expects float, got {raw!r}") from exc
    return raw


def parse_parameter_overrides(items, declared: dict) -> dict:
    """Parse repeatable ``key=value`` strings against declared defaults.

    Rejects malformed entries, unknown parameter names, and execution settings
    (which must use the dedicated runner flags). Never silently ignores input.
    """
    declared = dict(declared or {})
    overrides: dict = {}
    for item in items:
        key, sep, raw = str(item).partition("=")
        if not key or not sep:
            raise InvalidParametersError(f"--param must be key=value, got {item!r}")
        if key in ENGINE_SETTING_NAMES:
            flag = "--stop-distance" if key == "stop_distance" else "--take-profit-distance"
            raise InvalidExecutionError(
                f"{key} is an execution setting, not a strategy parameter; use {flag}"
            )
        if key not in declared:
            raise InvalidParametersError(
                f"unknown strategy parameter {key!r}; declared: {sorted(declared)}"
            )
        overrides[key] = coerce_parameter(key, raw, declared[key])
    return overrides


def _coerce_typed(key: str, value, default):
    """Validate a JSON-typed value against a declared default's type."""
    if isinstance(value, str) and not isinstance(default, str):
        # A string from JSON/CLI is parsed using the declared default type.
        return coerce_parameter(key, value, default)
    if isinstance(default, bool):
        if isinstance(value, bool):
            return value
        raise InvalidParametersError(f"parameter {key!r} expects bool, got {value!r}")
    if isinstance(default, int) and not isinstance(default, bool):
        if isinstance(value, bool):
            raise InvalidParametersError(f"parameter {key!r} expects int, got {value!r}")
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
        raise InvalidParametersError(f"parameter {key!r} expects int, got {value!r}")
    if isinstance(default, float):
        if isinstance(value, bool):
            raise InvalidParametersError(f"parameter {key!r} expects float, got {value!r}")
        if isinstance(value, (int, float)):
            return float(value)
        raise InvalidParametersError(f"parameter {key!r} expects float, got {value!r}")
    # String-typed default.
    if isinstance(value, str):
        return value
    raise InvalidParametersError(f"parameter {key!r} expects string, got {value!r}")


def coerce_parameter_value(key: str, value, default):
    """Public typed coercion used by optimization grids (JSON/CLI values)."""
    return _coerce_typed(key, value, default)


def normalize_parameter_overrides(overrides, declared: dict) -> dict:
    """Validate already-typed (JSON) overrides against declared strategy defaults.

    Used by the bridge; the CLI path validates its raw strings earlier via
    ``parse_parameter_overrides``. Unknown names, execution settings and
    wrong-typed values are rejected, never silently ignored.
    """
    declared = dict(declared or {})
    overrides = dict(overrides or {})
    normalized: dict = {}
    for key, value in overrides.items():
        if key in ENGINE_SETTING_NAMES:
            raise InvalidExecutionError(
                f"{key} is an execution setting, not a strategy parameter"
            )
        if key not in declared:
            raise InvalidParametersError(
                f"unknown strategy parameter {key!r}; declared: {sorted(declared)}"
            )
        normalized[str(key)] = _coerce_typed(str(key), value, declared[key])
    return normalized


def _positive_number(name: str, value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidExecutionError(f"{name} must be a number, got {value!r}")
    number = float(value)
    if number <= 0:
        raise InvalidExecutionError(f"{name} must be greater than 0, got {value!r}")
    return number


def parse_request_payload(payload) -> BacktestRunRequest:
    """Validate an HTTP/JSON payload into the existing ``BacktestRunRequest``.

    This is the single HTTP->contract conversion; no second run contract exists.
    Unknown fields and wrong types are rejected explicitly.
    """
    if not isinstance(payload, dict):
        raise InvalidParametersError("request body must be a JSON object")
    unknown = sorted(set(payload) - {"strategy", "market", "execution", "publish"})
    if unknown:
        raise InvalidParametersError(f"unknown request field(s): {unknown}")

    raw_strategy = payload.get("strategy")
    if not isinstance(raw_strategy, dict):
        raise InvalidStrategyError("strategy must be an object with a selector")
    strategy_unknown = sorted(set(raw_strategy) - {"selector", "parameters"})
    if strategy_unknown:
        raise InvalidStrategyError(f"unknown strategy field(s): {strategy_unknown}")
    selector = raw_strategy.get("selector")
    if not isinstance(selector, str) or not selector.strip():
        raise InvalidStrategyError("strategy.selector is required")
    parameters = raw_strategy.get("parameters") or {}
    if not isinstance(parameters, dict):
        raise InvalidParametersError("strategy.parameters must be an object")

    raw_market = payload.get("market")
    if not isinstance(raw_market, dict):
        raise InvalidDatasetError("market is required")
    market_unknown = sorted(set(raw_market) - {"symbol", "timeframe", "start", "end", "data_source"})
    if market_unknown:
        raise InvalidDatasetError(f"unknown market field(s): {market_unknown}")
    symbol = raw_market.get("symbol")
    data_source = raw_market.get("data_source")
    timeframe = raw_market.get("timeframe", "15min")
    if not isinstance(symbol, str) or not symbol.strip():
        raise InvalidDatasetError("market.symbol is required")
    if not isinstance(data_source, str) or not data_source.strip():
        raise InvalidDatasetError("market.data_source is required")
    if not isinstance(timeframe, str) or not timeframe.strip():
        raise InvalidDatasetError("market.timeframe must be a non-empty string")
    for key in ("start", "end"):
        value = raw_market.get(key)
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise InvalidDatasetError(f"market.{key} must be a non-empty string")

    raw_execution = payload.get("execution") or {}
    if not isinstance(raw_execution, dict):
        raise InvalidExecutionError("execution must be an object")
    execution_unknown = sorted(set(raw_execution) - {"stop_distance", "take_profit_distance"})
    if execution_unknown:
        raise InvalidExecutionError(f"unknown execution field(s): {execution_unknown}")
    stop_distance = _positive_number("execution.stop_distance", raw_execution.get("stop_distance", 5.0))
    take_profit = _positive_number(
        "execution.take_profit_distance", raw_execution.get("take_profit_distance", 10.0)
    )

    publish = payload.get("publish", False)
    if not isinstance(publish, bool):
        raise InvalidParametersError("publish must be a boolean")

    return BacktestRunRequest(
        strategy=StrategyRequest(selector=selector.strip(), parameters=dict(parameters)),
        market=MarketRequest(
            symbol=symbol.strip(),
            timeframe=timeframe.strip(),
            start=raw_market.get("start"),
            end=raw_market.get("end"),
            data_source=data_source.strip(),
        ),
        execution=ExecutionRequest(stop_distance=stop_distance, take_profit_distance=take_profit),
        publish=publish,
    )


def build_run_config(
    request: BacktestRunRequest,
    *,
    run_id: str,
    metadata: dict,
    effective_parameters: dict,
    data_fingerprint: str,
    start: str,
    end: str,
) -> dict:
    """Build the archived ``config.json`` for one run.

    Separates strategy / market / execution so opening the file later tells the
    reader exactly how the run was produced. Values are the effective ones
    actually used (no fake or unavailable values are stored).
    """
    return {
        "run_id": run_id,
        "strategy": {
            "selector": request.strategy.selector,
            "file": request.strategy.file,
            "name": metadata.get("name", ""),
            "version": metadata.get("version", ""),
            "description": metadata.get("description", ""),
            "parameters": dict(effective_parameters),
            "overrides": dict(request.strategy.parameters),
        },
        "market": {
            "symbol": request.market.symbol,
            "timeframe": request.market.timeframe,
            "start": start,
            "end": end,
            "data_source": request.market.data_source,
            "data_fingerprint": data_fingerprint,
        },
        "execution": {
            "stop_distance": request.execution.stop_distance,
            "take_profit_distance": request.execution.take_profit_distance,
        },
    }
