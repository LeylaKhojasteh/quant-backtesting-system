"""Candle-bar execution model.

Used for markets where only OHLC data exists (e.g. crypto 1m klines). This is
an explicit approximation, not a replication of exchange matching.

Rules (deliberately conservative and documented in docs/CANDLE_EXECUTION.md):

- Signals are produced on a closed bar and executed at the next bar's open.
- ``spread_bps`` is the FULL spread in basis points of the bar price (mid):
  ``ask = mid * (1 + h)`` and ``bid = mid * (1 - h)`` with ``h = bps / 20000``.
  A round trip therefore costs exactly one full spread, never two.
- Stop/target levels are fixed for the whole bar. A level created by
  break-even or trailing only becomes active on the NEXT bar, because the
  order of events inside one OHLC bar is unknown.
- If a bar opens beyond a level, the fill is at the open (gap), not the level.
- If both stop and target are touched inside one bar, the stop is assumed and
  the trade is flagged ``ambiguous_exit``.
- ``mae`` / ``mfe`` are approximated from bar extremes on the exit side.
- A position still open at the end of the data closes at the last close with
  reason ``END_OF_DATA``.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

REQUIRED_SIGNAL_COLUMNS = ["signal_candle_start", "signal_available_time", "direction"]
OPTIONAL_LEVELS = (
    "stop_distance", "take_profit_distance", "trail_distance",
    "trail_after_distance", "trail_step", "breakeven_after_distance",
    "breakeven_offset", "max_holding_seconds", "size_multiplier",
)
VALID_DIRECTIONS = ("LONG", "SHORT")

TRADE_COLUMNS = [
    "trade_id", "direction", "signal_candle_start", "signal_available_time",
    "entry_time", "entry_bid", "entry_ask", "entry_price", "stop_loss",
    "take_profit", "exit_time", "exit_bid", "exit_ask", "exit_price",
    "exit_reason", "pnl_price", "duration_seconds", "spread_at_entry",
    "spread_at_exit", "mae", "mfe", "size_multiplier", "ambiguous_exit",
]


def _level(value, name, index):
    if value is None or (isinstance(value, float) and value != value):
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"signal {name!r} row {index} must be a number or null")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"signal {name!r} row {index} must be finite and >= 0")
    return number


def _validate_candles(candles):
    required = {"open", "high", "low", "close"}
    missing = sorted(required - set(candles.columns))
    if missing:
        raise ValueError(f"candles is missing columns: {missing}")
    if candles.empty:
        raise ValueError("candles is empty")
    index = candles.index
    if not isinstance(index, pd.DatetimeIndex) or index.tz is None:
        raise ValueError("candle index must be a timezone-aware DatetimeIndex")
    if not index.is_monotonic_increasing:
        raise ValueError("candle index must be sorted ascending")
    if index.has_duplicates:
        raise ValueError("candle index must not contain duplicates")
    o = candles["open"].to_numpy(dtype="float64")
    h = candles["high"].to_numpy(dtype="float64")
    low = candles["low"].to_numpy(dtype="float64")
    c = candles["close"].to_numpy(dtype="float64")
    if not (np.isfinite(o).all() and np.isfinite(h).all()
            and np.isfinite(low).all() and np.isfinite(c).all()):
        raise ValueError("candle prices must be finite")
    if ((h < low) | (h < o) | (h < c) | (low > o) | (low > c)).any():
        raise ValueError("candle OHLC is inconsistent (high/low outside range)")
    return o, h, low, c


def _signals_view(signals):
    missing = [c for c in REQUIRED_SIGNAL_COLUMNS if c not in signals.columns]
    if missing:
        raise ValueError(f"signals is missing required columns: {missing}")
    bad = sorted(set(signals["direction"].unique()) - set(VALID_DIRECTIONS))
    if bad:
        raise ValueError(f"signals contains invalid directions: {bad}")
    if not signals.empty:
        times = pd.to_datetime(signals["signal_available_time"], utc=True)
        if not times.is_monotonic_increasing:
            signals = signals.assign(signal_available_time=times).sort_values(
                "signal_available_time", kind="stable")
    return signals


def _col(signals, name):
    if name not in signals.columns:
        return [None] * len(signals)
    return [_level(v, name, i) for i, v in enumerate(signals[name].tolist())]


def run_candle_backtest(candles, signals, *, stop_distance=None,
                        take_profit_distance=None, spread_bps=0.0):
    """Execute normalized signals on OHLC bars. Returns the trade frame."""
    o, h, low, c = _validate_candles(candles)
    if not isinstance(spread_bps, (int, float)) or isinstance(spread_bps, bool):
        raise ValueError("spread_bps must be a number")
    spread_bps = float(spread_bps)
    if not math.isfinite(spread_bps) or spread_bps < 0:
        raise ValueError("spread_bps must be finite and >= 0")
    for name, value in (("stop_distance", stop_distance),
                        ("take_profit_distance", take_profit_distance)):
        if value is not None and (not isinstance(value, (int, float))
                                  or isinstance(value, bool)
                                  or not math.isfinite(float(value))
                                  or float(value) <= 0):
            raise ValueError(f"{name} must be a positive finite number or None")

    signals = _signals_view(signals)
    index = candles.index
    n_bars = len(candles)
    n_signals = len(signals)

    s_time = pd.to_datetime(signals["signal_available_time"], utc=True).tolist() if n_signals else []
    s_start = signals["signal_candle_start"].tolist() if n_signals else []
    s_dir = signals["direction"].tolist() if n_signals else []
    s_stop = _col(signals, "stop_distance")
    s_take = _col(signals, "take_profit_distance")
    s_trail = _col(signals, "trail_distance")
    s_trail_after = _col(signals, "trail_after_distance")
    s_trail_step = _col(signals, "trail_step")
    s_be_after = _col(signals, "breakeven_after_distance")
    s_be_offset = _col(signals, "breakeven_offset")
    s_hold = _col(signals, "max_holding_seconds")
    s_size = _col(signals, "size_multiplier")

    h_frac = spread_bps / 20000.0
    trades = []
    position = None
    next_signal = 0

    def bid(price):
        return price * (1.0 - h_frac)

    def ask(price):
        return price * (1.0 + h_frac)

    def close_at(bar_index, price, reason, ambiguous=False):
        nonlocal position
        d = position
        long_side = d["direction"] == "LONG"
        exit_price = bid(price) if long_side else ask(price)
        pnl = exit_price - d["entry_price"] if long_side else d["entry_price"] - exit_price
        trades.append({
            "trade_id": len(trades) + 1,
            "direction": d["direction"],
            "signal_candle_start": d["signal_candle_start"],
            "signal_available_time": d["signal_available_time"],
            "entry_time": d["entry_time"],
            "entry_bid": d["entry_bid"],
            "entry_ask": d["entry_ask"],
            "entry_price": d["entry_price"],
            "stop_loss": d["stop_loss"],
            "take_profit": d["take_profit"],
            "exit_time": index[bar_index],
            "exit_bid": bid(price),
            "exit_ask": ask(price),
            "exit_price": exit_price,
            "exit_reason": reason,
            "pnl_price": pnl,
            "duration_seconds": (index[bar_index] - d["entry_time"]).total_seconds(),
            "spread_at_entry": d["entry_ask"] - d["entry_bid"],
            "spread_at_exit": ask(price) - bid(price),
            "mae": d["mae"],
            "mfe": d["mfe"],
            "size_multiplier": d["size_multiplier"],
            "ambiguous_exit": bool(ambiguous),
        })
        # A closed trade must free the slot, otherwise the same position would
        # be closed again on every following bar.
        position = None

    def track(bar_index):
        d = position
        if d["direction"] == "LONG":
            worst = bid(low[bar_index]) - d["entry_price"]
            best = bid(h[bar_index]) - d["entry_price"]
        else:
            worst = d["entry_price"] - ask(h[bar_index])
            best = d["entry_price"] - ask(low[bar_index])
        d["mae"] = min(d["mae"], worst)
        d["mfe"] = max(d["mfe"], best)

    def resolve_levels(bar_index, entry_bar=False):
        """Check fixed levels against this bar's extremes; returns bool closed."""
        d = position
        long_side = d["direction"] == "LONG"
        sl, tp = d["stop_loss"], d["take_profit"]
        if long_side:
            sl_hit = sl is not None and bid(low[bar_index]) <= sl
            tp_hit = tp is not None and bid(h[bar_index]) >= tp
        else:
            sl_hit = sl is not None and ask(h[bar_index]) >= sl
            tp_hit = tp is not None and ask(low[bar_index]) <= tp
        if sl_hit and tp_hit:
            close_at(bar_index, sl, d.get("stop_source") or "SL", ambiguous=True)
            return True
        if sl_hit:
            close_at(bar_index, sl, d.get("stop_source") or "SL")
            return True
        if tp_hit:
            close_at(bar_index, tp, "TP")
            return True
        return False

    def update_exit_levels(bar_index):
        """Bar-close update; effective next bar, so no same-bar ambiguity."""
        d = position
        long_side = d["direction"] == "LONG"
        close_px = bid(c[bar_index]) if long_side else ask(c[bar_index])
        favorable = (close_px - d["entry_price"]) if long_side else (d["entry_price"] - close_px)

        be_after = d.get("be_after")
        if be_after is not None and not d["be_done"] and favorable >= be_after:
            offset = d.get("be_offset") or 0.0
            level = d["entry_price"] + offset if long_side else d["entry_price"] - offset
            prev = d["stop_loss"]
            if prev is None or (level > prev if long_side else level < prev):
                d["stop_loss"] = level
                d["stop_source"] = "BE"
            d["be_done"] = True

        trail = d.get("trail_distance")
        if trail is None:
            return
        after = d.get("trail_after")
        if after is not None and favorable < after:
            return
        step = d.get("trail_step")
        prev = d["stop_loss"]
        if long_side:
            tracked = close_px - trail
            best_so_far = d["trail_best"]
            if best_so_far is None or tracked > best_so_far:
                d["trail_best"] = tracked
            cand = d["trail_best"]
            floor = d.get("stop_floor")
            if floor is not None and cand < floor:
                cand = floor
            allowed = prev is None or (cand >= prev + step if step is not None else cand > prev)
            if allowed and (prev is None or cand > prev):
                d["stop_loss"] = cand
                d["stop_source"] = "TSL"
        else:
            tracked = close_px + trail
            best_so_far = d["trail_best"]
            if best_so_far is None or tracked < best_so_far:
                d["trail_best"] = tracked
            cand = d["trail_best"]
            ceiling = d.get("stop_ceiling")
            if ceiling is not None and cand > ceiling:
                cand = ceiling
            allowed = prev is None or (cand <= prev - step if step is not None else cand < prev)
            if allowed and (prev is None or cand < prev):
                d["stop_loss"] = cand
                d["stop_source"] = "TSL"

    for i in range(n_bars):
        ts = index[i]

        # Entry: only when flat and a signal became available by this bar's
        # open; the open is the fill. The entry bar is then fully evaluated
        # (levels are already fixed at the open) and nothing else happens.
        if position is None and next_signal < n_signals and s_time[next_signal] <= ts:
            direction = s_dir[next_signal]
            long_side = direction == "LONG"
            eff_stop = s_stop[next_signal] if s_stop[next_signal] is not None else stop_distance
            eff_take = s_take[next_signal] if s_take[next_signal] is not None else take_profit_distance
            entry_bid = bid(o[i])
            entry_ask = ask(o[i])
            entry_price = entry_ask if long_side else entry_bid
            hold = s_hold[next_signal]
            size = s_size[next_signal]
            position = {
                "direction": direction,
                "signal_candle_start": s_start[next_signal],
                "signal_available_time": s_time[next_signal],
                "entry_time": ts,
                "entry_bid": entry_bid,
                "entry_ask": entry_ask,
                "entry_price": entry_price,
                "stop_loss": (None if eff_stop is None else
                              entry_price - eff_stop if long_side else entry_price + eff_stop),
                "take_profit": (None if eff_take is None else
                                entry_price + eff_take if long_side else entry_price - eff_take),
                "trail_distance": s_trail[next_signal],
                "trail_after": s_trail_after[next_signal],
                "trail_step": s_trail_step[next_signal],
                "be_after": s_be_after[next_signal],
                "be_offset": s_be_offset[next_signal],
                "be_done": False,
                "stop_source": None,
                "trail_best": None,
                "stop_floor": None,
                "stop_ceiling": None,
                "exit_deadline": (None if hold is None else
                                  ts + pd.Timedelta(seconds=hold)),
                "size_multiplier": 1.0 if size is None else size,
                "mae": 0.0,
                "mfe": 0.0,
            }
            if long_side:
                position["stop_floor"] = position["stop_loss"]
            else:
                position["stop_ceiling"] = position["stop_loss"]
            next_signal += 1
            # Any other signal due on this same bar is ignored (single
            # position), exactly like the tick engine.
            while next_signal < n_signals and s_time[next_signal] <= ts:
                next_signal += 1
            track(i)
            resolve_levels(i)
            if position is not None:
                update_exit_levels(i)
            continue

        # Signals that became available while a trade is open are ignored, not
        # queued (the same single-position rule as the tick engine).
        while next_signal < n_signals and s_time[next_signal] <= ts:
            next_signal += 1

        if position is None:
            continue

        # Bar with an existing position: scheduled exit, then gap, then levels.
        d = position
        long_side = d["direction"] == "LONG"
        deadline = d.get("exit_deadline")
        if deadline is not None and ts >= deadline:
            close_at(i, o[i], "TIME")
            continue

        track(i)
        exit_open = bid(o[i]) if long_side else ask(o[i])
        sl, tp = d["stop_loss"], d["take_profit"]
        if sl is not None and (exit_open <= sl if long_side else exit_open >= sl):
            close_at(i, o[i], d.get("stop_source") or "SL")
            continue
        if tp is not None and (exit_open >= tp if long_side else exit_open <= tp):
            close_at(i, o[i], "TP")
            continue

        if resolve_levels(i):
            continue
        update_exit_levels(i)

    if position is not None:
        close_at(n_bars - 1, c[n_bars - 1], "END_OF_DATA")

    return pd.DataFrame(trades, columns=TRADE_COLUMNS)
