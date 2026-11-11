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

    ``ticks`` is the run's real tick frame (``timestamp``, ``bid``, ``ask``),
    read-only by convention, or ``None`` when the run has no tick data.
    ``fill_price(when, direction)`` returns the price the strategy would
    actually get at the first tick at/after ``when``: ask for a long entry,
    bid for a short entry, and the exit side for closing. Use it instead of
    candle prices when you want realistic fills.
    """

    parameters: dict
    instrument: object | None
    account: object | None
    symbol: str
    timeframe: str
    seed: int
    run_engine: object = None
    ticks: object | None = None
    fill_price: object = None


def _empty_signals():
    import pandas as pd

    return pd.DataFrame(columns=["signal_candle_start", "signal_available_time", "direction"])


def _tick_index(ticks):
    """Cheap positional view of a tick frame, or ``None`` when unusable."""
    import pandas as pd

    if ticks is None or not hasattr(ticks, "columns"):
        return None
    needed = {"timestamp", "bid", "ask"}
    if not needed.issubset(set(ticks.columns)) or ticks.empty:
        return None
    frame = ticks
    if not frame["timestamp"].is_monotonic_increasing:
        frame = frame.sort_values("timestamp", kind="stable").reset_index(drop=True)
    return {
        "ts": frame["timestamp"],
        "bid": frame["bid"].to_numpy(dtype="float64"),
        "ask": frame["ask"].to_numpy(dtype="float64"),
    }


def _fill_price(index, when, direction):
    """First tick at/after ``when``: ask for a long entry, bid for a short."""
    import pandas as pd

    if index is None:
        return None
    stamp = pd.Timestamp(when)
    pos = int(index["ts"].searchsorted(stamp, side="left"))
    if pos >= len(index["bid"]) or pos < 0:
        return None
    side = "ask" if str(direction).upper() in ("LONG", "BUY") else "bid"
    return float(index[side][pos])


def build_context(*, parameters, instrument, account, symbol, timeframe,
                  seed=0, ticks=None):
    """Build the ``ctx`` object; binds ticks, ``fill_price`` and ``run_engine``."""
    import pandas as pd

    index = _tick_index(ticks)

    def fill_price(when, direction):
        return _fill_price(index, when, direction)

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
        ticks=ticks,
        fill_price=fill_price,
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
            "spread_at_entry", "spread_at_exit", "mae", "mfe", "size_multiplier",
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
            "size_multiplier": _multiplier(row.get("size_multiplier")),
        })
    return pd.DataFrame(rows)


def _multiplier(value):
    """Per-trade size multiplier: missing/invalid stays 1.0, never zero."""
    if value is None:
        return 1.0
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 1.0
    return number if number == number and number > 0 else 1.0


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


def enrich_with_ticks(trades_df, ticks):
    """Fill real spread and excursions from the run's ticks.

    Strategy-owned trades arrive with ``spread_at_* = 0`` and no excursions.
    This reads the actual tick frame so a strategy-owned run reports the same
    execution facts as an engine run: real spread at entry/exit and MAE/MFE on
    the exit side (bid for long, ask for short), signed like the engine
    (``mae <= 0``, ``mfe >= 0``). Prices the strategy reported are not
    modified; only the measured facts are added.
    """
    import numpy as np
    import pandas as pd

    index = _tick_index(ticks)
    if index is None or trades_df.empty:
        return trades_df

    ts = index["ts"]
    bid = index["bid"]
    ask = index["ask"]
    last = len(bid) - 1

    spread_in = []
    spread_out = []
    maes = []
    mfes = []
    for _, row in trades_df.iterrows():
        entry_pos = int(ts.searchsorted(pd.Timestamp(row["entry_time"]), side="left"))
        exit_pos = int(ts.searchsorted(pd.Timestamp(row["exit_time"]), side="left"))
        if entry_pos > last:
            spread_in.append(None); spread_out.append(None)
            maes.append(None); mfes.append(None)
            continue
        exit_pos = min(max(exit_pos, entry_pos), last)
        spread_in.append(float(ask[entry_pos] - bid[entry_pos]))
        spread_out.append(float(ask[exit_pos] - bid[exit_pos]))
        entry_price = float(row["entry_price"])
        if str(row["direction"]) == "LONG":
            move = bid[entry_pos:exit_pos + 1] - entry_price
        else:
            move = entry_price - ask[entry_pos:exit_pos + 1]
        if move.size == 0:
            maes.append(None); mfes.append(None)
            continue
        maes.append(float(min(0.0, float(np.min(move)))))
        mfes.append(float(max(0.0, float(np.max(move)))))

    out = trades_df.copy()
    out["spread_at_entry"] = spread_in
    out["spread_at_exit"] = spread_out
    out["mae"] = maes
    out["mfe"] = mfes
    return out


def max_concurrent_positions(trades_df) -> int:
    """Highest number of positions open at the same instant (sweep line).

    A measurement of the trade list, not a constraint: overlapping trades are
    allowed and this only reports how many were open together.
    """
    import pandas as pd

    if trades_df.empty:
        return 0
    events = []
    for _, row in trades_df.iterrows():
        events.append((pd.Timestamp(row["entry_time"]), 1))
        events.append((pd.Timestamp(row["exit_time"]), -1))
    # Closing sorts before opening at the same instant: a position that exits
    # and another that enters on the same tick are not concurrent.
    events.sort(key=lambda e: (e[0], e[1]))
    current = 0
    peak = 0
    for _, delta in events:
        current += delta
        peak = max(peak, current)
    return peak


def validate_external_trades(trades_df, *, range_start, range_end,
                             max_concurrent=None):
    """Ownership-boundary checks: range, ordering, finiteness, optional cap.

    Overlapping (pyramided/hedged) trades are valid. ``max_concurrent`` is the
    strategy's own declared ceiling: ``None`` means "no ceiling declared" and
    nothing is enforced — there is no system default. Raises on violation.
    """
    import pandas as pd
    import math

    def _aware(value):
        if value is None or value == "":
            return None
        stamp = pd.Timestamp(value)
        return stamp if stamp.tzinfo is not None else stamp.tz_localize("UTC")

    start = _aware(range_start)
    end = _aware(range_end)
    previous_entry = None
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
        # Ordering is by entry: the trade list must be chronological by entry
        # time. Overlap is fine; going backwards in entry time is not.
        if previous_entry is not None and entry < previous_entry:
            raise ValueError("strategy trades must be ordered by entry time")
        for key in ("entry_price", "exit_price", "pnl_price"):
            value = float(row[key])
            if not math.isfinite(value):
                raise ValueError(f"strategy trade {key} must be finite")
        previous_entry = entry

    if max_concurrent is not None:
        cap = int(max_concurrent)
        if cap <= 0:
            raise ValueError("max_concurrent must be a positive integer")
        observed = max_concurrent_positions(trades_df)
        if observed > cap:
            raise ValueError(
                f"strategy opened {observed} concurrent positions, "
                f"above its declared max_concurrent={cap}"
            )


def run_external(strategy, candles, *, parameters, instrument, account,
                 symbol, timeframe, range_start, range_end, seed=0, ticks=None):
    """Execute a strategy-owned run and return an engine-shaped trade frame.

    Returns ``(trades_df, raw_output)`` where ``trades_df`` uses engine
    column names plus ``_exit_reason_detail`` (consumed by the runner when
    building contract records).

    Overlapping positions are allowed. The only concurrency ceiling is the one
    the strategy declares itself via the ``max_concurrent_positions``
    parameter; when it is absent nothing is enforced (no system default).
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
    # No silent guesses: normalization is money math and must say whose
    # economics it used. Without an instrument/account model the run cannot
    # convert money PnL, so it must fail loudly instead of assuming 100/1.
    if contract_size is None or lots is None:
        raise ValueError(
            "strategy-owned run needs instrument contract_size and account "
            "position_size_lots for normalization (no defaults are assumed)"
        )
    if lots <= 0 or contract_size <= 0:
        raise ValueError("contract_size and lots must be positive")
    normalized = normalize_external_trades(
        trades_df, contract_size=contract_size, lots=lots,
    )
    declared_cap = None
    if isinstance(parameters, dict):
        declared_cap = parameters.get("max_concurrent_positions")
    validate_external_trades(
        normalized, range_start=range_start, range_end=range_end,
        max_concurrent=declared_cap,
    )
    # Same execution facts as an engine run: real spread and excursions from
    # the run's ticks (skipped when the run has no tick frame).
    normalized = enrich_with_ticks(normalized, ticks)
    return normalized, output
