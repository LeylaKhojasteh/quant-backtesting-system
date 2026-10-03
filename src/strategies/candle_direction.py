import pandas as pd


SIGNAL_COLUMNS = ["signal_candle_start", "signal_available_time", "direction"]


def generate_signals(candles, timeframe="15min"):
    """Return normalized signals from BID candle direction.

    Close > Open -> LONG, Close < Open -> SHORT, Close == Open -> no signal.
    A signal only becomes available once the source candle has fully closed.
    """
    bar = pd.Timedelta(timeframe)
    rows = []
    for candle_start, row in candles.iterrows():
        if row["close"] > row["open"]:
            direction = "LONG"
        elif row["close"] < row["open"]:
            direction = "SHORT"
        else:
            continue
        rows.append(
            {
                "signal_candle_start": candle_start,
                "signal_available_time": candle_start + bar,
                "direction": direction,
            }
        )
    return pd.DataFrame(rows, columns=SIGNAL_COLUMNS)
