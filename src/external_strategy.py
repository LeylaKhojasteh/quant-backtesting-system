"""Strategy-owned execution adapter (Phase 3).

Reference: architecture decision — one shared downstream pipeline, one
capability-chosen producer at the import boundary. This module never touches
the tick engine; it owns the ``run_strategy(data, ctx)`` contract only.

``data`` carries the Quant-side candle frame (already resampled, UTC, range
filtered). ``ctx`` carries run context: symbol/timeframe/range, effective
strategy parameters, instrument/account economics, determinism seed, and the
optional ``run_engine`` helper for strategies that want tick execution.

The adapter calls the strategy, normalizes its trade table to the engine
column names, validates ownership boundaries, and hands the frame to the same
``trades_from_engine`` path as engine runs. Commission stays out of
``pnl_price``: ``pnl_price`` comes from the strategy's ``gross`` price move
and commission flows into the account block downstream.
"""

from __future__ import annotations

from dataclasses import dataclass


DIRECTION_MAP = {"BUY": "LONG", "SELL": "SHORT", "LONG": "LONG", "SHORT": "SHORT"}


def _iso(value) -> str:
    text = str(value)
    return text.replace("+00:00", "Z") if text.endswith("+00:00") else text


@dataclass
class StrategyData:
    """Quant-owned market view handed to a strategy-owned run."""

    candles: object
    symbol: str
    timeframe: str
    start: str
    end: str


@dataclass
class StrategyContext:
    """Run context for ``run_strategy(data, ctx)``.

    ``parameters`` are the effective strategy inputs (declared + UI).
    ``instrument``/``account`` are the resolved specs or ``None`` when the
    run carries no account model. ``seed`` keeps runs reproducible.
    ``run_engine`` is the tick engine bound for this frame (optional use).
    """

    parameters: dict
    instrument: object | None
    account: object | None
    symbol: str
    timeframe: str
    seed: int
    run_engine: object = None


def _empty_signals():
    import pandas as pd

    return pd.DataFrame(columns=["signal_candle_start", "signal_available_time", "direction"])


def build_context(*, parameters, instrument, account, symbol, timeframe,
                  seed=0, ticks=None):
    """Build the ``ctx`` object; binds ``run_engine`` to this run's ticks."""
    import pandas as pd

    def run_engine(signals, stop_distance=None, take_profit_distance=None):
        from src import backtest as _engine

        if ticks is None:
            raise ValueError("ctx.run_engine needs the run tick frame")
        if signals is None:
            signals = _empty_signals()
        return _engine.run_backtest(ticks, signals, stop_distance, take_profit_distance)

    return StrategyContext(
        parameters=dict(parameters or {}),
        instrument=instrument,
        account=account,
        symbol=symbol,
        timeframe=timeframe,
        seed=int(seed),
        run_engine=run_engine,
    )


def _direction(raw) -> str:
    if raw is None or (isinstance(raw, float) and raw != raw):
        raise ValueError("strategy trade direction is required")
    text = str(raw).strip().upper()
    mapped = DIRECTION_MAP.get(text)
    if mapped is None:
        raise ValueError(f"strategy trade direction must be BUY/SELL/LONG/SHORT, got {raw!r}")
    return mapped


def normalize_external_trades(trades_df, *, contract_size, lots):
    """Normalize a strategy-owned trade table to engine column names.

    Required: ``direction`` (BUY/SELL/LONG/SHORT), ``entry_time``,
    ``exit_time``, ``entry_price``, ``exit_price``, ``exit_reason``.
    Optional: ``stop_loss``, ``take_profit`` (None when absent),
    ``gross_pnl`` (money) or ``pnl_price`` (price terms), ``commission``.
    ``pnl_price`` is derived from ``gross`` money only — commission never
    enters price-term fields (it flows to the account block downstream).
    """
    import pandas as pd

    required = ["direction", "entry_time", "exit_time", "entry_price", "exit_price", "exit_reason"]
    missing = [c for c in required if c not in trades_df.columns]
    if missing:
        raise ValueError(f"strategy trades are missing columns: {missing}")
    if contract_size is None or lots is None or lots <= 0 or contract_size <= 0:
        raise ValueError("normalization needs positive contract_size and lots")

    rows = []
    if trades_df.empty:
        return pd.DataFrame(columns=[
            "trade_id", "direction", "signal_candle_start", "signal_available_time",
            "entry_time", "entry_bid", "entry_ask", "entry_price", "stop_loss",
            "take_profit", "exit_time", "exit_bid", "exit_ask", "exit_price",
            "exit_reason", "_exit_reason_detail", "pnl_price", "duration_seconds",
            "spread_at_entry", "spread_at_exit", "mae", "mfe",
        ])
    for i, row in trades_df.iterrows():
        direction = _direction(row["direction"])
        entry_price = float(row["entry_price"])
        exit_price = float(row["exit_price"])
        if "pnl_price" in trades_df.columns and row.get("pnl_price") is not None:
            pnl_price = float(row["pnl_price"])
        elif "gross_pnl" in trades_df.columns and row.get("gross_pnl") is not None:
            pnl_price = float(row["gross_pnl"]) / (float(contract_size) * float(lots))
        else:
            raise ValueError("strategy trades need gross_pnl (money) or pnl_price (price terms)")
        from src import result_contract as _rc

        reason, detail = _rc.canonicalize_strategy_exit_reason(row["exit_reason"])
        rows.append({
            "trade_id": i + 1,
            "direction": direction,
            "signal_candle_start": _iso(row.get("signal_time", row["entry_time"])),
            "signal_available_time": _iso(row.get("signal_time", row["entry_time"])),
            "entry_time": _iso(row["entry_time"]),
            "entry_bid": float(row.get("entry_bid", entry_price)),
            "entry_ask": float(row.get("entry_ask", entry_price)),
            "entry_price": entry_price,
            "stop_loss": _none_or_float(row.get("stop_loss")),
            "take_profit": _none_or_float(row.get("take_profit")),
            "exit_time": _iso(row["exit_time"]),
            "exit_bid": float(row.get("exit_bid", exit_price)),
            "exit_ask": float(row.get("exit_ask", exit_price)),
            "exit_price": exit_price,
            "exit_reason": reason,
            "_exit_reason_detail": detail,
            "pnl_price": pnl_price,
            "duration_seconds": _duration_seconds(row["entry_time"], row["exit_time"]),
            "spread_at_entry": 0.0,
            "spread_at_exit": 0.0,
            "mae": None,
            "mfe": None,
        })
    return pd.DataFrame(rows)


def _none_or_float(value):
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if number != number else number


def _duration_seconds(entry, exit_):
    import pandas as pd

    return (pd.Timestamp(exit_) - pd.Timestamp(entry)).total_seconds()


def validate_external_trades(trades_df, *, range_start, range_end):
    """Ownership-boundary checks: range, order, finiteness. Raises on violation."""
    import pandas as pd
    import math

    def _aware(value):
        if value is None or value == "":
            return None
        stamp = pd.Timestamp(value)
        return stamp if stamp.tzinfo is not None else stamp.tz_localize("UTC")

    start = _aware(range_start)
    end = _aware(range_end)
    previous_exit = None
    for _, row in trades_df.iterrows():
        entry = pd.Timestamp(row["entry_time"])
        exit_ = pd.Timestamp(row["exit_time"])
        if entry.tzinfo is None or exit_.tzinfo is None:
            raise ValueError("strategy trade times must be timezone-aware (UTC)")
        if exit_ < entry:
            raise ValueError("strategy trade exits before it enters")
        if start is not None and entry < start:
            raise ValueError("strategy trade enters before the run range")
        if end is not None and exit_ > end + pd.Timedelta(days=1):
            raise ValueError("strategy trade exits after the run range")
        if previous_exit is not None and entry < previous_exit:
            raise ValueError("strategy trades overlap or are out of order")
        for key in ("entry_price", "exit_price", "pnl_price"):
            value = float(row[key])
            if not math.isfinite(value):
                raise ValueError(f"strategy trade {key} must be finite")
        previous_exit = exit_


def run_external(strategy, candles, *, parameters, instrument, account,
                 symbol, timeframe, range_start, range_end, seed=0, ticks=None):
    """Execute a strategy-owned run and return an engine-shaped trade frame.

    Returns ``(trades_df, raw_output)`` where ``trades_df`` uses engine
    column names plus ``_exit_reason_detail`` (consumed by the runner when
    building contract records).
    """
    data = StrategyData(
        candles=candles, symbol=symbol, timeframe=timeframe,
        start=str(range_start), end=str(range_end),
    )
    ctx = build_context(
        parameters=parameters, instrument=instrument, account=account,
        symbol=symbol, timeframe=timeframe, seed=seed, ticks=ticks,
    )
    try:
        output = strategy.run_strategy(data, ctx)
    except Exception as exc:  # noqa: BLE001 - surface the real strategy failure
        raise ValueError(f"external strategy failed: {type(exc).__name__}: {exc}") from exc
    if isinstance(output, tuple):
        trades_df = output[0]
    elif isinstance(output, dict) and "trades" in output:
        trades_df = output["trades"]
    else:
        trades_df = output
    import pandas as pd

    if not isinstance(trades_df, pd.DataFrame):
        raise ValueError("run_strategy must return a trade DataFrame (or (trades, ...) tuple)")
    contract_size = getattr(instrument, "contract_size", None)
    lots = getattr(account, "position_size_lots", None)
    if contract_size is None:
        contract_size = 100.0
    if lots is None:
        lots = 1.0
    normalized = normalize_external_trades(
        trades_df, contract_size=contract_size, lots=lots,
    )
    validate_external_trades(normalized, range_start=range_start, range_end=range_end)
    return normalized, output
