#!/usr/bin/env python3
"""
EMA 9/20 crossover backtest for XAUUSD M1.

Rules:
- Bullish cross on a CLOSED candle: EMA9 was <= EMA20 on the prior closed
  candle and is > EMA20 on the latest closed candle.
- Bearish cross: inverse condition.
- Signal is executed at the NEXT candle open.
- One position at a time. An opposite cross closes the current position
  and opens the opposite position at the same next-candle open.
- No SL/TP. No time-based exit.
- Any open position is marked closed at the final candle close for reporting.

CSV required columns:
    time,open,high,low,close
or separate date,time columns plus OHLC.

Examples:
    pip install pandas
    python ema_9_20_backtest.py XAUUSD_M1.csv
    python ema_9_20_backtest.py XAUUSD_M1.csv --lots 0.01 --contract-size 100 \
        --spread 0.20 --commission-per-lot-side 3.5

Cost inputs are in quote-currency units:
- --spread: FULL spread in XAUUSD price units (e.g. 0.20 means $0.20).
- --slippage: adverse slippage per order in XAUUSD price units.
- --commission-per-lot-side: commission for one side, per 1.0 lot.
Check your broker's contract size and costs before comparing money PnL.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import math
import pandas as pd


def load_ohlc(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, sep=None, engine="python")
    original_cols = list(df.columns)
    lower_to_original = {str(c).strip().lower(): c for c in original_cols}

    def col(name: str):
        return lower_to_original.get(name)

    # Support common MT5 exports with separate Date and Time columns.
    if col("date") is not None and col("time") is not None:
        dt = pd.to_datetime(
            df[col("date")].astype(str).str.strip() + " " +
            df[col("time")].astype(str).str.strip(),
            errors="coerce"
        )
    else:
        time_col = col("datetime") or col("timestamp") or col("time")
        if time_col is None:
            raise ValueError("CSV must contain a time/datetime column (or Date + Time).")
        dt = pd.to_datetime(df[time_col], errors="coerce")

    missing = [name for name in ("open", "high", "low", "close") if col(name) is None]
    if missing:
        raise ValueError("CSV is missing required columns: " + ", ".join(missing))

    out = pd.DataFrame({
        "time": dt,
        "open": pd.to_numeric(df[col("open")], errors="coerce"),
        "high": pd.to_numeric(df[col("high")], errors="coerce"),
        "low": pd.to_numeric(df[col("low")], errors="coerce"),
        "close": pd.to_numeric(df[col("close")], errors="coerce"),
    })
    out = out.dropna().sort_values("time").drop_duplicates("time").reset_index(drop=True)
    if len(out) < 30:
        raise ValueError(f"Only {len(out)} valid candles found; provide more history.")
    return out


def adverse_price(raw_price: float, order_side: str, half_spread: float, slippage: float) -> float:
    """Buy at ask + slippage; sell at bid - slippage."""
    if order_side == "buy":
        return raw_price + half_spread + slippage
    return raw_price - half_spread - slippage


def run_backtest(
    df: pd.DataFrame,
    lots: float,
    contract_size: float,
    spread: float,
    slippage: float,
    commission_per_lot_side: float,
    initial_balance: float,
    warmup_bars: int,
):
    df = df.copy()
    df["ema9"] = df["close"].ewm(span=9, adjust=False).mean()
    df["ema20"] = df["close"].ewm(span=20, adjust=False).mean()

    half_spread = spread / 2.0
    position = None
    trades = []

    def close_position(raw_price: float, timestamp, reason: str):
        nonlocal position
        if position is None:
            return
        exit_side = "sell" if position["direction"] == 1 else "buy"
        exit_price = adverse_price(raw_price, exit_side, half_spread, slippage)
        gross = (
            position["direction"] *
            (exit_price - position["entry_price"]) *
            contract_size * lots
        )
        commission = 2.0 * commission_per_lot_side * lots
        net = gross - commission
        trades.append({
            "direction": "BUY" if position["direction"] == 1 else "SELL",
            "entry_time": position["entry_time"],
            "exit_time": timestamp,
            "entry_price": position["entry_price"],
            "exit_price": exit_price,
            "bars_held": 0,  # calculated immediately below
            "gross_pnl": gross,
            "commission": commission,
            "net_pnl": net,
            "exit_reason": reason,
        })
        trades[-1]["bars_held"] = max(0, current_index - position["entry_index"])
        position = None

    # A signal is read from candle i-1 (already closed) and executed at candle i open.
    start_i = max(2, warmup_bars)
    for i in range(start_i, len(df)):
        signal_bar = i - 1
        fast_prev = df.at[signal_bar - 1, "ema9"]
        slow_prev = df.at[signal_bar - 1, "ema20"]
        fast_now = df.at[signal_bar, "ema9"]
        slow_now = df.at[signal_bar, "ema20"]

        bullish_cross = fast_prev <= slow_prev and fast_now > slow_now
        bearish_cross = fast_prev >= slow_prev and fast_now < slow_now

        if not bullish_cross and not bearish_cross:
            continue

        signal_direction = 1 if bullish_cross else -1
        execution_time = df.at[i, "time"]
        execution_open = float(df.at[i, "open"])
        current_index = i

        if position is not None and position["direction"] == signal_direction:
            continue

        # Close existing position on the opposite cross.
        if position is not None:
            close_position(execution_open, execution_time, "opposite_ema_cross")

        # Open the new direction at the same next-candle open.
        entry_side = "buy" if signal_direction == 1 else "sell"
        entry_price = adverse_price(execution_open, entry_side, half_spread, slippage)
        position = {
            "direction": signal_direction,
            "entry_time": execution_time,
            "entry_price": entry_price,
            "entry_index": i,
        }

    # Mark-to-market close at the end of the test so all trades are realized in the report.
    if position is not None:
        current_index = len(df) - 1
        close_position(
            float(df.iloc[-1]["close"]),
            df.iloc[-1]["time"],
            "end_of_data"
        )

    trades_df = pd.DataFrame(trades)
    if trades_df.empty:
        trades_df = pd.DataFrame(columns=[
            "direction", "entry_time", "exit_time", "entry_price", "exit_price",
            "bars_held", "gross_pnl", "commission", "net_pnl", "exit_reason"
        ])

    equity_rows = [{"time": None, "balance": initial_balance, "drawdown": 0.0}]
    balance = initial_balance
    peak = initial_balance
    max_dd_money = 0.0
    for _, trade in trades_df.iterrows():
        balance += float(trade["net_pnl"])
        peak = max(peak, balance)
        dd = peak - balance
        max_dd_money = max(max_dd_money, dd)
        equity_rows.append({
            "time": trade["exit_time"],
            "balance": balance,
            "drawdown": dd,
        })
    equity_df = pd.DataFrame(equity_rows)

    net_profit = float(trades_df["net_pnl"].sum()) if len(trades_df) else 0.0
    wins = int((trades_df["net_pnl"] > 0).sum()) if len(trades_df) else 0
    losses = int((trades_df["net_pnl"] < 0).sum()) if len(trades_df) else 0
    gross_profit = float(trades_df.loc[trades_df["net_pnl"] > 0, "net_pnl"].sum()) if len(trades_df) else 0.0
    gross_loss = abs(float(trades_df.loc[trades_df["net_pnl"] < 0, "net_pnl"].sum())) if len(trades_df) else 0.0
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else (math.inf if gross_profit > 0 else 0.0)
    win_rate = 100.0 * wins / len(trades_df) if len(trades_df) else 0.0

    summary = {
        "candles": len(df),
        "trades": len(trades_df),
        "wins": wins,
        "losses": losses,
        "win_rate_pct": win_rate,
        "net_profit": net_profit,
        "profit_factor": profit_factor,
        "max_drawdown_money": max_dd_money,
        "final_balance": initial_balance + net_profit,
    }
    return trades_df, equity_df, summary


def main():
    parser = argparse.ArgumentParser(description="Simple EMA 9/20 crossover backtest.")
    parser.add_argument("csv", type=Path, help="OHLC CSV file")
    parser.add_argument("--lots", type=float, default=0.01, help="Fixed lot size (default: 0.01)")
    parser.add_argument("--contract-size", type=float, default=100.0,
                        help="Contract size per 1 lot (verify with broker; default: 100)")
    parser.add_argument("--spread", type=float, default=0.0,
                        help="Full spread in XAUUSD price units, e.g. 0.20 (default: 0)")
    parser.add_argument("--slippage", type=float, default=0.0,
                        help="Adverse slippage per order in price units (default: 0)")
    parser.add_argument("--commission-per-lot-side", type=float, default=0.0,
                        help="Commission per 1 lot per side in account currency (default: 0)")
    parser.add_argument("--initial-balance", type=float, default=10000.0)
    parser.add_argument("--warmup-bars", type=int, default=200,
                        help="Ignore signals until this many candles are available (default: 200)")
    parser.add_argument("--output-prefix", type=Path, default=None,
                        help="Output file prefix; defaults to CSV name + _ema9_20")
    args = parser.parse_args()

    if args.lots <= 0 or args.contract_size <= 0:
        parser.error("--lots and --contract-size must be positive.")
    if min(args.spread, args.slippage, args.commission_per_lot_side) < 0:
        parser.error("Cost inputs cannot be negative.")

    df = load_ohlc(args.csv)
    trades, equity, summary = run_backtest(
        df=df,
        lots=args.lots,
        contract_size=args.contract_size,
        spread=args.spread,
        slippage=args.slippage,
        commission_per_lot_side=args.commission_per_lot_side,
        initial_balance=args.initial_balance,
        warmup_bars=args.warmup_bars,
    )

    prefix = args.output_prefix or args.csv.with_suffix("")
    trades_path = Path(str(prefix) + "_trades.csv")
    equity_path = Path(str(prefix) + "_equity.csv")
    trades.to_csv(trades_path, index=False)
    equity.to_csv(equity_path, index=False)

    print("\n=== EMA 9/20 BACKTEST ===")
    for key, value in summary.items():
        if isinstance(value, float):
            print(f"{key}: {value:.4f}")
        else:
            print(f"{key}: {value}")
    print(f"\nTrades saved: {trades_path}")
    print(f"Equity saved: {equity_path}")
    print("\nNote: final open position is closed at the final candle close for reporting.")


# ---------------------------------------------------------------------------
# Quant Lab adapter (light edit): strategy-owned entrypoint + declared UI
# inputs. The standalone CLI above is untouched. Only genuine strategy
# inputs are declared here; instrument/account economics come from ctx.
# ---------------------------------------------------------------------------

NAME = "EMA 9/20 Cross"
VERSION = "1.0"
DESCRIPTION = (
    "EMA9/EMA20 crossover on closed candles; entry at next candle open; "
    "exit on opposite cross. No SL/TP."
)
PARAMETERS = {
    "warmup_bars": 200,
    "spread": 0.0,
    "slippage": 0.0,
    "commission_per_lot_side": 0.0,
}


def run_strategy(data, ctx):
    """Quant Lab strategy-owned entrypoint. Returns (trades_df, equity_df, summary)."""
    params = ctx.parameters or {}
    frame = data.candles.reset_index()
    frame = frame.rename(columns={frame.columns[0]: "time"})
    lots = getattr(ctx.account, "position_size_lots", 0.01) or 0.01
    contract_size = getattr(ctx.instrument, "contract_size", 100.0) or 100.0
    initial_balance = getattr(ctx.account, "starting_balance", 10000.0)
    return run_backtest(
        df=frame,
        lots=float(lots),
        contract_size=float(contract_size),
        spread=float(params.get("spread", 0.0)),
        slippage=float(params.get("slippage", 0.0)),
        commission_per_lot_side=float(params.get("commission_per_lot_side", 0.0)),
        initial_balance=float(initial_balance),
        warmup_bars=int(params.get("warmup_bars", 200)),
    )


if __name__ == "__main__":
    main()
