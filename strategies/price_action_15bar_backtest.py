#!/usr/bin/env python3
"""XAUUSD M1 15-candle price-action breakout backtest. Requires pandas.

Signal: a CLOSED candle closes above/below the high/low of the previous N
closed candles (signal candle excluded). Enter at next candle open.
One position at a time; opposite breakout closes and reverses.
SL: beyond breakout candle extreme + buffer. TP: RR * initial risk.
If SL and TP are both touched in one candle, assume SL first.

CSV columns: time,open,high,low,close. Separate Date and Time are supported.
OHLC is assumed BID (usual MT5 chart convention).
"""

import argparse
import math
from pathlib import Path
import pandas as pd


def load_ohlc(path):
    raw = pd.read_csv(path, sep=None, engine="python")
    cols = {str(c).strip().lower(): c for c in raw.columns}
    if "date" in cols and "time" in cols:
        times = pd.to_datetime(raw[cols["date"]].astype(str) + " " +
                               raw[cols["time"]].astype(str), errors="coerce")
    else:
        tcol = cols.get("datetime") or cols.get("timestamp") or cols.get("time")
        if tcol is None:
            raise ValueError("CSV needs time/datetime or separate Date and Time columns.")
        times = pd.to_datetime(raw[tcol], errors="coerce")
    missing = [x for x in ("open", "high", "low", "close") if x not in cols]
    if missing:
        raise ValueError("Missing OHLC columns: " + ", ".join(missing))
    df = pd.DataFrame({
        "time": times,
        "open": pd.to_numeric(raw[cols["open"]], errors="coerce"),
        "high": pd.to_numeric(raw[cols["high"]], errors="coerce"),
        "low": pd.to_numeric(raw[cols["low"]], errors="coerce"),
        "close": pd.to_numeric(raw[cols["close"]], errors="coerce"),
    }).dropna().sort_values("time").drop_duplicates("time").reset_index(drop=True)
    if len(df) < 50:
        raise ValueError("Provide at least 50 valid candles.")
    if (df["high"] < df["low"]).any():
        raise ValueError("Invalid OHLC: High is below Low.")
    return df


def run_backtest(df, lookback=15, lots=0.01, contract_size=100.0,
                 spread=0.0, slippage=0.0, commission_per_lot_side=0.0,
                 buffer=0.05, rr=1.5, initial_balance=10000.0,
                 warmup_bars=200):
    if lookback < 2 or lots <= 0 or contract_size <= 0 or rr <= 0:
        raise ValueError("lookback >= 2 and lots/contract_size/rr > 0 are required.")
    if min(spread, slippage, commission_per_lot_side, buffer) < 0:
        raise ValueError("Cost and buffer inputs cannot be negative.")

    d = df.reset_index(drop=True)
    pos, trades = None, []

    # raw_exit is always BID. A short exits at ASK = BID + spread.
    def close_pos(raw_exit_bid, timestamp, index, reason):
        nonlocal pos
        if pos is None:
            return
        if pos["dir"] == 1:
            exit_price = raw_exit_bid - slippage
            gross = (exit_price - pos["entry"]) * contract_size * lots
            name = "BUY"
        else:
            exit_price = raw_exit_bid + spread + slippage
            gross = (pos["entry"] - exit_price) * contract_size * lots
            name = "SELL"
        commission = 2 * commission_per_lot_side * lots
        trades.append({
            "direction": name, "entry_time": pos["time"], "exit_time": timestamp,
            "entry_price": pos["entry"], "exit_price": exit_price,
            "stop_loss": pos["sl"], "take_profit": pos["tp"],
            "bars_held": max(0, index - pos["index"]),
            "gross_pnl": gross, "commission": commission,
            "net_pnl": gross - commission, "exit_reason": reason
        })
        pos = None

    def open_pos(direction, index, signal_index):
        nonlocal pos
        row, sig = d.iloc[index], d.iloc[signal_index]
        bid_open = float(row.open)
        if direction == 1:
            entry = bid_open + spread + slippage  # buy at ASK
            sl = float(sig.low) - buffer           # long stop is BID-side
            risk = entry - sl
            if risk <= 0:
                return
            tp = entry + rr * risk
        else:
            entry = bid_open - slippage             # sell at BID
            sl = float(sig.high) + spread + buffer # short stop is ASK-side
            risk = sl - entry
            if risk <= 0:
                return
            tp = entry - rr * risk                 # short target is ASK-side
        pos = {"dir": direction, "time": row.time, "index": index,
               "entry": entry, "sl": sl, "tp": tp}

    def intrabar_exit(index):
        if pos is None:
            return
        row = d.iloc[index]
        o, h, l, t = float(row.open), float(row.high), float(row.low), row.time
        if pos["dir"] == 1:
            sl, tp = pos["sl"], pos["tp"]
            if o <= sl:
                close_pos(o, t, index, "stop_loss_gap")
            elif o >= tp:
                close_pos(o, t, index, "take_profit_gap")
            elif l <= sl:  # if both levels hit, stop wins (conservative)
                close_pos(sl, t, index, "stop_loss")
            elif h >= tp:
                close_pos(tp, t, index, "take_profit")
        else:
            ask_o, ask_h, ask_l = o + spread, h + spread, l + spread
            sl, tp = pos["sl"], pos["tp"]
            if ask_o >= sl:
                close_pos(o, t, index, "stop_loss_gap")
            elif ask_o <= tp:
                close_pos(o, t, index, "take_profit_gap")
            elif ask_h >= sl:
                close_pos(sl - spread, t, index, "stop_loss")
            elif ask_l <= tp:
                close_pos(tp - spread, t, index, "take_profit")

    first_i = max(lookback + 1, warmup_bars)
    for i in range(first_i, len(d)):
        s = i - 1  # latest closed candle
        prior = d.iloc[s - lookback:s]  # exclude signal candle
        if len(prior) != lookback:
            continue
        range_high = float(prior.high.max())
        range_low = float(prior.low.min())
        signal_close = float(d.at[s, "close"])
        direction = 1 if signal_close > range_high else (-1 if signal_close < range_low else 0)

        if direction:
            if pos is None:
                open_pos(direction, i, s)
            elif pos["dir"] != direction:
                close_pos(float(d.at[i, "open"]), d.at[i, "time"], i, "opposite_breakout")
                open_pos(direction, i, s)
            # Same-direction breakout: do nothing.

        intrabar_exit(i)

    if pos is not None:
        last = d.iloc[-1]
        close_pos(float(last.close), last.time, len(d) - 1, "end_of_data")

    cols = ["direction", "entry_time", "exit_time", "entry_price", "exit_price",
            "stop_loss", "take_profit", "bars_held", "gross_pnl",
            "commission", "net_pnl", "exit_reason"]
    td = pd.DataFrame(trades, columns=cols)
    balance, peak, max_dd = initial_balance, initial_balance, 0.0
    eq = [{"time": None, "balance": balance, "drawdown": 0.0}]
    for _, tr in td.iterrows():
        balance += float(tr.net_pnl)
        peak = max(peak, balance)
        dd = peak - balance
        max_dd = max(max_dd, dd)
        eq.append({"time": tr.exit_time, "balance": balance, "drawdown": dd})
    ed = pd.DataFrame(eq)

    n = len(td)
    wins = int((td.net_pnl > 0).sum()) if n else 0
    losses = int((td.net_pnl < 0).sum()) if n else 0
    gp = float(td.loc[td.net_pnl > 0, "net_pnl"].sum()) if n else 0.0
    gl = abs(float(td.loc[td.net_pnl < 0, "net_pnl"].sum())) if n else 0.0
    summary = {
        "candles": len(d), "lookback_bars": lookback, "trades": n,
        "wins": wins, "losses": losses, "win_rate_pct": 100*wins/n if n else 0.0,
        "net_profit": float(td.net_pnl.sum()) if n else 0.0,
        "profit_factor": gp/gl if gl else (math.inf if gp else 0.0),
        "max_drawdown_money": max_dd, "final_balance": balance
    }
    return td, ed, summary


def main():
    ap = argparse.ArgumentParser(description="XAUUSD 15-candle price-action breakout backtest")
    ap.add_argument("csv", type=Path)
    ap.add_argument("--lookback", type=int, default=15)
    ap.add_argument("--lots", type=float, default=0.01)
    ap.add_argument("--contract-size", type=float, default=100.0)
    ap.add_argument("--spread", type=float, default=0.0, help="Full fixed spread in XAUUSD price units")
    ap.add_argument("--slippage", type=float, default=0.0, help="Adverse slippage per order in price units")
    ap.add_argument("--commission-per-lot-side", type=float, default=0.0)
    ap.add_argument("--buffer", type=float, default=0.05)
    ap.add_argument("--rr", type=float, default=1.5)
    ap.add_argument("--initial-balance", type=float, default=10000.0)
    ap.add_argument("--warmup-bars", type=int, default=200)
    ap.add_argument("--output-prefix", type=Path, default=None)
    a = ap.parse_args()
    df = load_ohlc(a.csv)
    td, ed, stats = run_backtest(
        df, a.lookback, a.lots, a.contract_size, a.spread, a.slippage,
        a.commission_per_lot_side, a.buffer, a.rr, a.initial_balance, a.warmup_bars
    )
    prefix = a.output_prefix or a.csv.with_suffix("")
    tp = Path(str(prefix) + "_price_action_trades.csv")
    ep = Path(str(prefix) + "_price_action_equity.csv")
    td.to_csv(tp, index=False)
    ed.to_csv(ep, index=False)
    print("\\n=== 15-CANDLE PRICE ACTION BREAKOUT ===")
    for k, v in stats.items():
        print(f"{k}: {v:.4f}" if isinstance(v, float) else f"{k}: {v}")
    print(f"Trades saved: {tp}\\nEquity saved: {ep}")
    print("Any remaining open position is closed at final candle close for reporting.")


if __name__ == "__main__":
    main()
