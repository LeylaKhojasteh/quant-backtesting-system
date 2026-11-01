import pandas as pd


REQUIRED_SIGNAL_COLUMNS = ["signal_candle_start", "signal_available_time", "direction"]
VALID_DIRECTIONS = ("LONG", "SHORT")


def run_backtest(ticks, signals, stop_distance=None, take_profit_distance=None):
    """Execute normalized signals tick-by-tick on real BID/ASK ticks.

    The engine only knows signals; it never sees candles and never generates
    signals itself. Per tick: first evaluate an open position for SL/TP, then
    process signals whose availability time has been reached. Signals arriving
    while a position is open are ignored and are not queued.

    ``stop_distance`` and ``take_profit_distance`` are optional price
    distances. ``None`` disables that exit entirely: with both disabled a
    position stays open until the end of the selected data (``END_OF_DATA``).
    """
    missing = [c for c in REQUIRED_SIGNAL_COLUMNS if c not in signals.columns]
    if missing:
        raise ValueError(f"signals is missing required columns: {missing}")
    invalid = sorted(set(signals["direction"].unique()) - set(VALID_DIRECTIONS))
    if invalid:
        raise ValueError(f"signals contains invalid directions: {invalid}")

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
    n_signals = len(s)

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
            stop_loss = position["stop_loss"]
            take_profit = position["take_profit"]
            if position["direction"] == "LONG":
                if stop_loss is not None and bid[i] <= stop_loss:
                    close_position(i, bid[i], "SL")
                elif take_profit is not None and bid[i] >= take_profit:
                    close_position(i, bid[i], "TP")
            else:
                if stop_loss is not None and ask[i] >= stop_loss:
                    close_position(i, ask[i], "SL")
                elif take_profit is not None and ask[i] <= take_profit:
                    close_position(i, ask[i], "TP")

        # 2) then consume signals that are due at this tick
        while next_signal < n_signals and s_time[next_signal] <= ti:
            if position is None:
                direction = s_dir[next_signal]
                if direction == "LONG":
                    entry_price = ask[i]
                    stop_loss = (
                        None if stop_distance is None else entry_price - stop_distance
                    )
                    take_profit = (
                        None if take_profit_distance is None
                        else entry_price + take_profit_distance
                    )
                    entry_move = bid[i] - entry_price
                else:
                    entry_price = bid[i]
                    stop_loss = (
                        None if stop_distance is None else entry_price + stop_distance
                    )
                    take_profit = (
                        None if take_profit_distance is None
                        else entry_price - take_profit_distance
                    )
                    entry_move = entry_price - ask[i]
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
