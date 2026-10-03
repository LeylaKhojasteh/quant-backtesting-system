import pandas as pd


REQUIRED_SIGNAL_COLUMNS = ["signal_candle_start", "signal_available_time", "direction"]
VALID_DIRECTIONS = ("LONG", "SHORT")


def run_backtest(ticks, signals, stop_distance, take_profit_distance):
    """Execute normalized signals tick-by-tick on real BID/ASK ticks.

    The engine only knows signals; it never sees candles and never generates
    signals itself. Per tick: first evaluate an open position for SL/TP, then
    process signals whose availability time has been reached. Signals arriving
    while a position is open are ignored and are not queued.
    """
    missing = [c for c in REQUIRED_SIGNAL_COLUMNS if c not in signals.columns]
    if missing:
        raise ValueError(f"signals is missing required columns: {missing}")
    invalid = sorted(set(signals["direction"].unique()) - set(VALID_DIRECTIONS))
    if invalid:
        raise ValueError(f"signals contains invalid directions: {invalid}")

    t = ticks.sort_values("timestamp", kind="stable").reset_index(drop=True)
    timestamps = t["timestamp"].tolist()
    bid = t["bid"].to_numpy(dtype="float64")
    ask = t["ask"].to_numpy(dtype="float64")

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
            }
        )
        position = None

    for i in range(len(t)):
        ti = timestamps[i]

        # 1) evaluate exit on this tick first
        if position is not None:
            if position["direction"] == "LONG":
                if bid[i] <= position["stop_loss"]:
                    close_position(i, bid[i], "SL")
                elif bid[i] >= position["take_profit"]:
                    close_position(i, bid[i], "TP")
            else:
                if ask[i] >= position["stop_loss"]:
                    close_position(i, ask[i], "SL")
                elif ask[i] <= position["take_profit"]:
                    close_position(i, ask[i], "TP")

        # 2) then consume signals that are due at this tick
        while next_signal < n_signals and s_time[next_signal] <= ti:
            if position is None:
                direction = s_dir[next_signal]
                if direction == "LONG":
                    entry_price = ask[i]
                    stop_loss = entry_price - stop_distance
                    take_profit = entry_price + take_profit_distance
                else:
                    entry_price = bid[i]
                    stop_loss = entry_price + stop_distance
                    take_profit = entry_price - take_profit_distance
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
                }
            next_signal += 1

    # close any open position at the end of the selected data
    if position is not None:
        i = len(t) - 1
        exit_price = bid[i] if position["direction"] == "LONG" else ask[i]
        close_position(i, exit_price, "END_OF_DATA")

    return pd.DataFrame(trades)
