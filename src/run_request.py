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

from src.accounting import AccountModel, InstrumentSpec, resolve_instrument
from src.execution import resolve_distances
from src.run_errors import (
    InvalidDatasetError,
    InvalidExecutionError,
    InvalidParametersError,
    InvalidStrategyError,
    UnsupportedTimeframeError,
)

#: CLI/engine setting names that are execution configuration, not strategy
#: parameters. Passing them through ``--param`` is rejected.
ENGINE_SETTING_NAMES = (
    "stop_distance",
    "take_profit_distance",
    "stop_loss_pips",
    "take_profit_pips",
)

#: Resampling timeframes supported by the engine (pandas offset aliases).
#: These are the canonical request values; the UI presents friendly labels.
SUPPORTED_TIMEFRAMES: tuple[tuple[str, str], ...] = (
    ("1min", "1 minute (M1)"),
    ("5min", "5 minutes (M5)"),
    ("15min", "15 minutes (M15)"),
    ("30min", "30 minutes (M30)"),
    ("1h", "1 hour (H1)"),
    ("4h", "4 hours (H4)"),
)
SUPPORTED_TIMEFRAME_VALUES = tuple(value for value, _ in SUPPORTED_TIMEFRAMES)


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
    #: Optional first-class logical dataset id (Data Engine). When present the run
    #: resolves via the derived tick/candle cache rather than a raw filesystem path.
    dataset_id: str | None = None


@dataclass
class ExecutionRequest:
    """Strategy-independent execution settings consumed by the engine.

    ``stop_distance`` / ``take_profit_distance`` are the **price distances** the
    engine consumes. ``stop_loss_pips`` / ``take_profit_pips`` are the user's
    intent (v1.0.3); when present they are converted once via the instrument's
    ``pip_size`` and the effective price distances are stored above. Legacy
    callers that only set ``stop_distance`` behave exactly as before.
    """

    stop_distance: float = 5.0
    take_profit_distance: float = 10.0
    stop_loss_pips: float | None = None
    take_profit_pips: float | None = None
    #: Pip size used for the conversion (None for legacy price-unit runs).
    pip_size: float | None = None


@dataclass
class BacktestRunRequest:
    """Everything needed to reproduce a run (inputs only)."""

    strategy: StrategyRequest
    market: MarketRequest
    execution: ExecutionRequest
    #: Optional account/economics inputs. ``None`` keeps a run price-term only,
    #: exactly as before this layer existed.
    account: AccountModel | None = None
    instrument: InstrumentSpec | None = None
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


def _account_amount(name: str, value, *, allow_zero: bool) -> float:
    """Validate an account/instrument number (never bool) with explicit bounds."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidParametersError(f"{name} must be a number, got {value!r}")
    number = float(value)
    if number < 0 or (number == 0 and not allow_zero):
        raise InvalidParametersError(
            f"{name} must be {'>= 0' if allow_zero else '> 0'}, got {value!r}"
        )
    return number


def parse_account(raw) -> AccountModel | None:
    """Optional account model; ``None`` keeps the run price-term only."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise InvalidParametersError("account must be an object")
    unknown = sorted(set(raw) - {
        "account_currency", "starting_balance", "position_size_lots",
        "commission_per_lot_round_turn",
    })
    if unknown:
        raise InvalidParametersError(f"unknown account field(s): {unknown}")
    currency = raw.get("account_currency", "USD")
    if not isinstance(currency, str) or not currency.strip():
        raise InvalidParametersError("account.account_currency is required")
    return AccountModel(
        account_currency=currency.strip(),
        starting_balance=_account_amount("account.starting_balance", raw.get("starting_balance", 10000.0), allow_zero=False),
        position_size_lots=_account_amount("account.position_size_lots", raw.get("position_size_lots", 1.0), allow_zero=False),
        commission_per_lot_round_turn=_account_amount(
            "account.commission_per_lot_round_turn",
            raw.get("commission_per_lot_round_turn", 0.0),
            allow_zero=True,
        ),
    )


def parse_instrument(raw) -> InstrumentSpec | None:
    """Optional explicit instrument economics; falls back to the catalog later."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise InvalidParametersError("instrument must be an object")
    unknown = sorted(set(raw) - {"symbol", "quote_currency", "contract_size", "tick_size", "pip_size"})
    if unknown:
        raise InvalidParametersError(f"unknown instrument field(s): {unknown}")
    symbol = raw.get("symbol")
    quote = raw.get("quote_currency")
    if not isinstance(symbol, str) or not symbol.strip():
        raise InvalidParametersError("instrument.symbol is required")
    if not isinstance(quote, str) or not quote.strip():
        raise InvalidParametersError("instrument.quote_currency is required")
    tick = raw.get("tick_size")
    pip = raw.get("pip_size")
    return InstrumentSpec(
        symbol=symbol.strip(),
        quote_currency=quote.strip(),
        contract_size=_account_amount("instrument.contract_size", raw.get("contract_size"), allow_zero=False),
        tick_size=None if tick is None else _account_amount("instrument.tick_size", tick, allow_zero=False),
        pip_size=None if pip is None else _account_amount("instrument.pip_size", pip, allow_zero=False),
    )


def parse_request_payload(payload) -> BacktestRunRequest:
    """Validate an HTTP/JSON payload into the existing ``BacktestRunRequest``.

    This is the single HTTP->contract conversion; no second run contract exists.
    Unknown fields and wrong types are rejected explicitly.
    """
    if not isinstance(payload, dict):
        raise InvalidParametersError("request body must be a JSON object")
    unknown = sorted(set(payload) - {"strategy", "market", "execution", "account", "instrument", "publish"})
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
    market_unknown = sorted(set(raw_market) - {"symbol", "timeframe", "start", "end", "data_source", "dataset_id"})
    if market_unknown:
        raise InvalidDatasetError(f"unknown market field(s): {market_unknown}")
    symbol = raw_market.get("symbol")
    data_source = raw_market.get("data_source")
    dataset_id = raw_market.get("dataset_id")
    timeframe = raw_market.get("timeframe", "15min")
    if not isinstance(symbol, str) or not symbol.strip():
        raise InvalidDatasetError("market.symbol is required")
    if dataset_id is not None:
        if not isinstance(dataset_id, str) or not dataset_id.strip():
            raise InvalidDatasetError("market.dataset_id must be a non-empty string")
        # Logical dataset reference resolves through the derived data engine.
        if data_source is None:
            data_source = f"cache:{dataset_id.strip()}"
    if not isinstance(data_source, str) or not data_source.strip():
        raise InvalidDatasetError("market.data_source or market.dataset_id is required")
    if not isinstance(timeframe, str) or not timeframe.strip():
        raise InvalidDatasetError("market.timeframe must be a non-empty string")
    if timeframe.strip() not in SUPPORTED_TIMEFRAME_VALUES:
        raise UnsupportedTimeframeError(
            "unsupported timeframe; supported: " + ", ".join(SUPPORTED_TIMEFRAME_VALUES)
        )
    for key in ("start", "end"):
        value = raw_market.get(key)
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise InvalidDatasetError(f"market.{key} must be a non-empty string")

    raw_execution = payload.get("execution") or {}
    if not isinstance(raw_execution, dict):
        raise InvalidExecutionError("execution must be an object")
    execution_unknown = sorted(set(raw_execution) - {
        "stop_distance", "take_profit_distance", "stop_loss_pips", "take_profit_pips",
    })
    if execution_unknown:
        raise InvalidExecutionError(f"unknown execution field(s): {execution_unknown}")
    has_pips = ("stop_loss_pips" in raw_execution) or ("take_profit_pips" in raw_execution)
    has_price = ("stop_distance" in raw_execution) or ("take_profit_distance" in raw_execution)
    if has_pips and has_price:
        raise InvalidExecutionError(
            "execution must use either pip fields (stop_loss_pips/take_profit_pips) or "
            "price fields (stop_distance/take_profit_distance), not both"
        )
    stop_loss_pips = take_profit_pips = None
    if has_pips:
        if "stop_loss_pips" not in raw_execution or "take_profit_pips" not in raw_execution:
            raise InvalidExecutionError(
                "pip execution requires both stop_loss_pips and take_profit_pips"
            )
        stop_loss_pips = _positive_number("execution.stop_loss_pips", raw_execution.get("stop_loss_pips"))
        take_profit_pips = _positive_number("execution.take_profit_pips", raw_execution.get("take_profit_pips"))
    stop_distance = _positive_number("execution.stop_distance", raw_execution.get("stop_distance", 5.0))
    take_profit = _positive_number(
        "execution.take_profit_distance", raw_execution.get("take_profit_distance", 10.0)
    )

    publish = payload.get("publish", False)
    if not isinstance(publish, bool):
        raise InvalidParametersError("publish must be a boolean")

    account = parse_account(payload.get("account"))
    instrument = parse_instrument(payload.get("instrument"))
    # Resolve the effective instrument once (explicit spec, else catalog) and
    # convert pips -> price distances exactly once at this single boundary.
    resolved_instrument = resolve_instrument(symbol.strip(), instrument)
    eff_stop, eff_take, pip_used = resolve_distances(
        stop_loss_pips=stop_loss_pips,
        take_profit_pips=take_profit_pips,
        stop_distance=stop_distance,
        take_profit_distance=take_profit,
        instrument=resolved_instrument,
        symbol=symbol.strip(),
    )

    return BacktestRunRequest(
        strategy=StrategyRequest(selector=selector.strip(), parameters=dict(parameters)),
        market=MarketRequest(
            symbol=symbol.strip(),
            timeframe=timeframe.strip(),
            start=raw_market.get("start"),
            end=raw_market.get("end"),
            data_source=data_source.strip(),
            dataset_id=dataset_id.strip() if isinstance(dataset_id, str) and dataset_id.strip() else None,
        ),
        execution=ExecutionRequest(
            stop_distance=eff_stop,
            take_profit_distance=eff_take,
            stop_loss_pips=stop_loss_pips,
            take_profit_pips=take_profit_pips,
            pip_size=pip_used,
        ),
        account=account,
        instrument=instrument,
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
    instrument: InstrumentSpec | None = None,
) -> dict:
    """Build the archived ``config.json`` for one run.

    Separates strategy / market / execution so opening the file later tells the
    reader exactly how the run was produced. Values are the effective ones
    actually used (no fake or unavailable values are stored).

    ``account`` and ``instrument`` are included only when an account model was
    requested; ``instrument`` is the effective spec actually used (explicit or
    resolved from the catalog). Runs without an account model archive exactly
    the same config as before.
    """
    config = {
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
            "dataset_id": request.market.dataset_id,
            "data_fingerprint": data_fingerprint,
        },
        "execution": _execution_config(request.execution),
    }
    if request.account is not None:
        config["account"] = request.account.to_dict()
    if request.account is not None or instrument is not None:
        config["instrument"] = instrument.to_dict() if instrument is not None else None
    return config


def _execution_config(execution: ExecutionRequest) -> dict:
    """Archive SL/TP: legacy price distances, plus pip intent when pip-based.

    Legacy runs archive exactly ``stop_distance`` / ``take_profit_distance`` as
    before. Pip runs additionally archive the user intent and the effective
    engine values so the run is reproducible without re-deriving anything.
    """
    config = {
        "stop_distance": execution.stop_distance,
        "take_profit_distance": execution.take_profit_distance,
    }
    if execution.stop_loss_pips is not None or execution.take_profit_pips is not None:
        config.update(
            {
                "stop_loss_pips": execution.stop_loss_pips,
                "take_profit_pips": execution.take_profit_pips,
                "pip_size": execution.pip_size,
                "effective_stop_distance": execution.stop_distance,
                "effective_take_profit_distance": execution.take_profit_distance,
            }
        )
    return config
