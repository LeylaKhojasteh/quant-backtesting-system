import pandas as pd


SIGNAL_COLUMNS = ["signal_candle_start", "signal_available_time", "direction"]


def _infer_bar(index) -> pd.Timedelta:
    """Infer one bar from the median spacing of the candle index.

    The median is used (not the mode) so weekend/session gaps and a few
    missing candles do not change the inferred bar. Only strictly positive
    spacings count; anything else is rejected explicitly.
    """
    diffs = pd.Series(index).diff().dropna()
    positive = diffs[diffs > pd.Timedelta(0)]
    if positive.empty:
        raise ValueError("cannot infer timeframe: need at least 2 ordered candles")
    return positive.median()


def generate_signals(candles, timeframe=None):
    """Return normalized signals from BID candle direction.

    Close > Open -> LONG, Close < Open -> SHORT, Close == Open -> no signal.
    A signal only becomes available once the source candle has fully closed:
    ``signal_available_time = candle_start + timeframe``.

    ``timeframe`` is the market timeframe that produced ``candles`` (e.g.
    ``"15min"``, ``"1h"``). It has no default so multi-timeframe runs cannot
    silently reuse a stale bar size. When omitted, the bar is inferred from
    the median spacing of the candle index (at least 2 candles required).
    """
    if timeframe is None:
        timeframe = _infer_bar(candles.index)
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
