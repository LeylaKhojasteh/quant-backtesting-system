"""
Random 3-indicator trading setup
Timeframe: M1 (or any timeframe)
Indicators: EMA(50), RSI(14), MACD(12, 26, 9), ATR(14)

Input CSV columns:
    time, open, high, low, close
Optional:
    volume

Install:
    pip install pandas numpy ta

Run:
    python random_setup.py EURUSD_M1.csv

Signal values:
     1 = BUY
    -1 = SELL
     0 = no signal

Note: This script generates signals only. It is not a full backtester.
"""

import sys
import pandas as pd
from ta.trend import EMAIndicator, MACD
from ta.momentum import RSIIndicator
from ta.volatility import AverageTrueRange


def trading_strategy(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    required = {"high", "low", "close"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Missing required CSV columns: {', '.join(sorted(missing))}")

    # Indicators
    df["ema_50"] = EMAIndicator(close=df["close"], window=50).ema_indicator()
    df["rsi_14"] = RSIIndicator(close=df["close"], window=14).rsi()

    macd = MACD(
        close=df["close"],
        window_fast=12,
        window_slow=26,
        window_sign=9,
    )
    df["macd"] = macd.macd()
    df["macd_signal"] = macd.macd_signal()

    df["atr_14"] = AverageTrueRange(
        high=df["high"],
        low=df["low"],
        close=df["close"],
        window=14,
    ).average_true_range()

    # BUY: uptrend + oversold RSI + bullish MACD crossover
    buy = (
        (df["close"] > df["ema_50"])
        & (df["rsi_14"] < 35)
        & (df["macd"].shift(1) < df["macd_signal"].shift(1))
        & (df["macd"] > df["macd_signal"])
    )

    # SELL: downtrend + overbought RSI + bearish MACD crossover
    sell = (
        (df["close"] < df["ema_50"])
        & (df["rsi_14"] > 65)
        & (df["macd"].shift(1) > df["macd_signal"].shift(1))
        & (df["macd"] < df["macd_signal"])
    )

    df["trade_signal"] = 0
    df.loc[buy, "trade_signal"] = 1
    df.loc[sell, "trade_signal"] = -1

    # Reference SL/TP distances; execution and position sizing are not included.
    df["sl_distance"] = 1.5 * df["atr_14"]
    df["tp_distance"] = 2.0 * df["sl_distance"]

    return df


def main():
    if len(sys.argv) != 2:
        print("Usage: python random_setup.py path/to/data.csv")
        raise SystemExit(1)

    input_file = sys.argv[1]
    df = pd.read_csv(input_file)

    result = trading_strategy(df)

    signals = result.loc[
        result["trade_signal"] != 0,
        ["time", "close", "ema_50", "rsi_14", "macd", "macd_signal",
         "atr_14", "sl_distance", "tp_distance", "trade_signal"],
    ] if "time" in result.columns else result.loc[
        result["trade_signal"] != 0,
        ["close", "ema_50", "rsi_14", "macd", "macd_signal",
         "atr_14", "sl_distance", "tp_distance", "trade_signal"],
    ]

    output_file = "signals_output.csv"
    signals.to_csv(output_file, index=False)

    print(f"Found {len(signals)} signals.")
    print(f"Saved signals to: {output_file}")
    if not signals.empty:
        print(signals.tail(10).to_string(index=False))


# ---------------------------------------------------------------------------
# Quant Lab adapter (light edit): signal-path entrypoint + declared UI inputs.
# The standalone CLI and trading_strategy() logic above are untouched.
# ---------------------------------------------------------------------------

NAME = "Random 3-Indicator Setup"
VERSION = "1.0"
DESCRIPTION = (
    "EMA(50) + RSI(14) + MACD(12,26,9) crossover signals; engine executes "
    "with the run SL/TP."
)
PARAMETERS = {
    "ema_window": 50,
    "rsi_window": 14,
    "rsi_buy_max": 35,
    "rsi_sell_min": 65,
    "macd_fast": 12,
    "macd_slow": 26,
    "macd_sign": 9,
}
SIGNAL_COLUMNS = [
    "signal_candle_start", "signal_available_time", "direction",
    "stop_distance", "take_profit_distance",
]


def generate_signals(candles, parameters=None, timeframe=None):
    """Normalized signals from the 3-indicator logic on closed candles."""
    import pandas as pd

    params = dict(PARAMETERS)
    if parameters:
        params.update(parameters)

    frame = candles.reset_index()
    frame = frame.rename(columns={frame.columns[0]: "time"})
    out = trading_strategy(frame)

    if timeframe is None:
        diffs = pd.Series(pd.to_datetime(out["time"], utc=True)).diff().dropna()
        positive = diffs[diffs > pd.Timedelta(0)]
        bar = positive.median() if not positive.empty else pd.Timedelta("15min")
    else:
        bar = pd.Timedelta(timeframe)

    rows = []
    for _, row in out.iterrows():
        signal = int(row["trade_signal"])
        if signal == 0:
            continue
        start = pd.Timestamp(row["time"])
        if start.tzinfo is None:
            start = start.tz_localize("UTC")
        rows.append({
            "signal_candle_start": start,
            "signal_available_time": start + bar,
            "direction": "LONG" if signal > 0 else "SHORT",
            # Per-signal ATR-based levels: the engine prefers these over the
            # run-level SL/TP, so each trade carries its own stop/target.
            "stop_distance": float(row["sl_distance"]),
            "take_profit_distance": float(row["tp_distance"]),
        })
    return pd.DataFrame(rows, columns=SIGNAL_COLUMNS)


if __name__ == "__main__":
    main()
