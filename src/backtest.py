import math

import pandas as pd


REQUIRED_SIGNAL_COLUMNS = ["signal_candle_start", "signal_available_time", "direction"]
VALID_DIRECTIONS = ("LONG", "SHORT")


def _run_level(name, value):
    """Run-level price distance: ``None`` or a positive finite number."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a positive number or None, got {value!r}")
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"{name} must be a positive finite number or None, got {value!r}")
    return number


def run_backtest(ticks, signals, stop_distance=None, take_profit_distance=None):
    """Execute normalized signals tick-by-tick on real BID/ASK ticks.

    The engine only knows signals; it never sees candles and never generates
    signals itself. Per tick: first evaluate an open position for SL/TP, then
    process signals whose availability time has been reached. Signals arriving
    while a position is open are ignored and are not queued.

    ``stop_distance`` and ``take_profit_distance`` are optional run-level
    price distances. ``None`` disables that exit entirely: with both disabled
    a position stays open until the end of the selected data (``END_OF_DATA``).
    A signal may also carry its own ``stop_distance`` /
    ``take_profit_distance`` columns (positive numbers or null); per-signal
    levels win for that trade, null falls back to the run-level values.

    Optional per-signal policies (all null = the feature is simply off; the
    engine never applies a default). Single position only:

    - ``trail_distance`` (positive price distance): trailing stop keeps this
      distance behind the best exit-side price seen so far. It only moves in
      the favorable direction and never widens. A hit closes as ``"TSL"``.
    - ``trail_after_distance`` (positive price distance): trailing stays
      inactive until the trade is this far in profit. Omit for trail-from-entry.
    - ``trail_step`` (positive price distance): stepped trailing. The stop only
      advances in increments of at least ``trail_step``. Requires
      ``trail_distance``.
    - ``breakeven_after_distance`` (positive price distance): once the trade is
      this far in profit, the stop moves to entry + ``breakeven_offset``
      (default 0). A hit after that closes as ``"BE"``. Requires no trail.
    - ``max_holding_seconds`` (positive seconds): time exit at the first tick
      at/after ``entry_time + max_holding_seconds``, reason ``"TIME"``.
    - ``size_multiplier`` (positive number, default 1): scales account
      economics for that trade only. Price-term PnL is per unit and is never
      scaled, so metrics stay comparable across sizes.
    """
    missing = [c for c in REQUIRED_SIGNAL_COLUMNS if c not in signals.columns]
    if missing:
        raise ValueError(f"signals is missing required columns: {missing}")
    invalid = sorted(set(signals["direction"].unique()) - set(VALID_DIRECTIONS))
    if invalid:
        raise ValueError(f"signals contains invalid directions: {invalid}")
    # The engine is a library: direct callers must not get silent nonsense
    # (a negative stop would sit on the wrong side of the entry).
    stop_distance = _run_level("stop_distance", stop_distance)
    take_profit_distance = _run_level("take_profit_distance", take_profit_distance)

    # The runner marks Data-Engine frames with attrs["qbl_sorted"]; such
    # frames skip the defensive re-sort (identical output — the loader
    # guarantees chronological order). Raw-CSV frames keep the sort.
    if ticks.attrs.get("qbl_sorted") == "timestamp" and ticks["timestamp"].is_monotonic_increasing:
        t = ticks.reset_index(drop=True) if not ticks.index.equals(
            pd.RangeIndex(len(ticks))) else ticks
    else:
        t = ticks.sort_values("timestamp", kind="stable").reset_index(drop=True)
    timestamps = t["timestamp"].tolist()
    bid = t["bid"].to_numpy(dtype="float64")
    ask = t["ask"].to_numpy(dtype="float64")

    if (signals.attrs.get("qbl_sorted") == "signal_available_time"
            and signals["signal_available_time"].is_monotonic_increasing):
        s = signals.reset_index(drop=True) if not signals.index.equals(
            pd.RangeIndex(len(signals))) else signals
    else:
        s = signals.sort_values("signal_available_time", kind="stable").reset_index(drop=True)
    s_time = s["signal_available_time"].tolist()
    s_dir = s["direction"].tolist()
    s_start = s["signal_candle_start"].tolist()

    def _level_list(col, allow_zero=False):
        """Per-signal levels: missing column/NaN means "off"; bad values raise.

        Silently turning a bad value into None would disable the feature with
        no error, which is the exact failure mode the contract forbids.
        ``allow_zero`` exists for ``breakeven_offset``, where zero means
        "exactly at entry" and is valid.
        """
        if col not in s.columns:
            return None
        out = []
        for index, value in enumerate(s[col].tolist()):
            if value is None or (isinstance(value, float) and value != value):
                out.append(None)
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(
                    f"signal column {col!r} row {index} must be a "
                    f"{'non-negative' if allow_zero else 'positive'} number "
                    f"or null, got {value!r}"
                )
            number = float(value)
            bad = (
                not math.isfinite(number)
                or number < 0
                or (not allow_zero and number == 0)
            )
            if bad:
                raise ValueError(
                    f"signal column {col!r} row {index} must be a "
                    f"{'non-negative' if allow_zero else 'positive'} finite "
                    f"number or null, got {value!r}"
                )
            out.append(number)
        return out

    s_stop = _level_list("stop_distance")
    s_take = _level_list("take_profit_distance")
    s_trail = _level_list("trail_distance")
    s_trail_after = _level_list("trail_after_distance")
    s_trail_step = _level_list("trail_step")
    s_be_after = _level_list("breakeven_after_distance")
    s_be_offset = _level_list("breakeven_offset", allow_zero=True)
    s_maxhold = _level_list("max_holding_seconds")
    s_size = _level_list("size_multiplier")
    n_signals = len(s)

    def _timedelta_seconds(seconds):
        import datetime as _dt

        return _dt.timedelta(seconds=float(seconds))

    trades = []
    position = None
    next_signal = 0

    def close_position(i, exit_price, reason):
        nonlocal position
        d = position
        if d["direction"] == "LONG":
            pnl_price = exit_price - d["entry_price"]
        else:
            pnl_price = d["entry_price"] - exit_price
        trades.append(
            {
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
                "exit_time": timestamps[i],
                "exit_bid": bid[i],
                "exit_ask": ask[i],
                "exit_price": exit_price,
                "exit_reason": reason,
                "pnl_price": pnl_price,
                "duration_seconds": (timestamps[i] - d["entry_time"]).total_seconds(),
                "spread_at_entry": d["entry_ask"] - d["entry_bid"],
                "spread_at_exit": ask[i] - bid[i],
                "mae": d["mae"],
                "mfe": d["mfe"],
                "size_multiplier": d.get("size_multiplier", 1.0),
            }
        )
        position = None

    def track_excursion(i):
        """Update MAE (signed, <= 0) / MFE (>= 0) on the exit-side price.

        LONG exits on BID and SHORT exits on ASK, so excursions use the same
        side as the real PnL; the entry spread is an immediate adverse move.
        """
        d = position
        if d["direction"] == "LONG":
            move = bid[i] - d["entry_price"]
        else:
            move = d["entry_price"] - ask[i]
        d["mae"] = min(d["mae"], move)
        d["mfe"] = max(d["mfe"], move)

    for i in range(len(t)):
        ti = timestamps[i]

        # 1) update the open position's excursions, then evaluate its exit.
        # The exit tick is included so gap fills are reflected in MAE/MFE.
        if position is not None:
            track_excursion(i)
            d = position
            is_long = d["direction"] == "LONG"
            entry = d["entry_price"]
            favorable = (bid[i] - entry) if is_long else (entry - ask[i])
            prev = d["stop_loss"]

            # 1) break-even: one-shot move to entry (+ offset) once armed.
            be_after = d.get("be_after")
            if be_after is not None and not d.get("be_done") and favorable >= be_after:
                offset = d.get("be_offset") or 0.0
                level = entry + offset if is_long else entry - offset
                if prev is None or (level > prev if is_long else level < prev):
                    d["stop_loss"] = level
                    d["stop_source"] = "BE"
                d["be_done"] = True

            # 2) trailing, optionally gated by profit and optionally stepped.
            trail = d.get("trail_distance")
            if trail is not None:
                after = d.get("trail_after")
                if after is None or favorable >= after:
                    step = d.get("trail_step")
                    if is_long:
                        tracked = bid[i] - trail
                        if tracked > d.get("trail_best", float("-inf")):
                            d["trail_best"] = tracked
                        cand = d["trail_best"]
                        floor = d.get("stop_floor")
                        if floor is not None and cand < floor:
                            cand = floor
                        allowed = (
                            prev is None
                            or (cand >= prev + step if step is not None else cand > prev)
                        )
                        if allowed and (prev is None or cand > prev):
                            d["stop_loss"] = cand
                            d["stop_source"] = "TSL"
                    else:
                        tracked = ask[i] + trail
                        if tracked < d.get("trail_best", float("inf")):
                            d["trail_best"] = tracked
                        cand = d["trail_best"]
                        ceiling = d.get("stop_ceiling")
                        if ceiling is not None and cand > ceiling:
                            cand = ceiling
                        allowed = (
                            prev is None
                            or (cand <= prev - step if step is not None else cand < prev)
                        )
                        if allowed and (prev is None or cand < prev):
                            d["stop_loss"] = cand
                            d["stop_source"] = "TSL"

            stop_loss = d["stop_loss"]
            take_profit = d["take_profit"]
            deadline = d.get("exit_deadline")
            reason_override = d.get("stop_source")
            if is_long:
                if stop_loss is not None and bid[i] <= stop_loss:
                    close_position(i, bid[i], reason_override or "SL")
                elif take_profit is not None and bid[i] >= take_profit:
                    close_position(i, bid[i], "TP")
                elif deadline is not None and ti >= deadline:
                    close_position(i, bid[i], "TIME")
            else:
                if stop_loss is not None and ask[i] >= stop_loss:
                    close_position(i, ask[i], reason_override or "SL")
                elif take_profit is not None and ask[i] <= take_profit:
                    close_position(i, ask[i], "TP")
                elif deadline is not None and ti >= deadline:
                    close_position(i, ask[i], "TIME")

        # 2) then consume signals that are due at this tick
        while next_signal < n_signals and s_time[next_signal] <= ti:
            if position is None:
                direction = s_dir[next_signal]
                sig_stop = s_stop[next_signal] if s_stop is not None else None
                sig_take = s_take[next_signal] if s_take is not None else None
                eff_stop = stop_distance if sig_stop is None else sig_stop
                eff_take = take_profit_distance if sig_take is None else sig_take
                if direction == "LONG":
                    entry_price = ask[i]
                    stop_loss = (
                        None if eff_stop is None else entry_price - eff_stop
                    )
                    take_profit = (
                        None if eff_take is None
                        else entry_price + eff_take
                    )
                    entry_move = bid[i] - entry_price
                else:
                    entry_price = bid[i]
                    stop_loss = (
                        None if eff_stop is None else entry_price + eff_stop
                    )
                    take_profit = (
                        None if eff_take is None
                        else entry_price - eff_take
                    )
                    entry_move = entry_price - ask[i]
                def _row(col):
                    return col[next_signal] if col is not None else None

                sig_trail = _row(s_trail)
                sig_hold = _row(s_maxhold)
                sig_size = _row(s_size)
                position = {
                    "direction": direction,
                    "signal_candle_start": s_start[next_signal],
                    "signal_available_time": s_time[next_signal],
                    "entry_time": ti,
                    "entry_bid": bid[i],
                    "entry_ask": ask[i],
                    "entry_price": entry_price,
                    "stop_loss": stop_loss,
                    "take_profit": take_profit,
                    # Trailing / break-even spec: every field is opt-in per
                    # signal. None means the feature is off for this trade.
                    "trail_distance": sig_trail,
                    "trail_after": _row(s_trail_after),
                    "trail_step": _row(s_trail_step),
                    "be_after": _row(s_be_after),
                    "be_offset": _row(s_be_offset),
                    "be_done": False,
                    # Which policy last moved the stop: None (initial) | TSL | BE.
                    "stop_source": None,
                    # Account-only size for this trade; price terms ignore it.
                    "size_multiplier": 1.0 if sig_size is None else sig_size,
                    # An explicit stop is a hard floor/ceiling the trail can
                    # never pass, so trailing can never widen risk.
                    "stop_floor": stop_loss if direction == "LONG" else None,
                    "stop_ceiling": stop_loss if direction == "SHORT" else None,
                    "exit_deadline": (
                        None if sig_hold is None else ti + _timedelta_seconds(sig_hold)
                    ),
                    # Entry-tick excursion: the spread is an immediate adverse
                    # move on the exit side, so MAE starts negative.
                    "mae": min(0.0, entry_move),
                    "mfe": max(0.0, entry_move),
                }
            next_signal += 1

    # close any open position at the end of the selected data
    if position is not None:
        i = len(t) - 1
        exit_price = bid[i] if position["direction"] == "LONG" else ask[i]
        close_position(i, exit_price, "END_OF_DATA")

    return pd.DataFrame(trades)
