"""Deterministic fixture strategy for loader tests (legacy, no parameters).

Direction follows the candle close versus the previous candle close. It is a
plain ``generate_signals(candles)`` strategy with no configurable parameters,
used to prove the loader still accepts the legacy interface.
"""

import pandas as pd

NAME = "Trend Direction"
VERSION = "test-1.0"
DESCRIPTION = "Close above the previous close -> LONG, below -> SHORT."
PARAMETERS: dict = {}

SIGNAL_COLUMNS = ["signal_candle_start", "signal_available_time", "direction"]

BAR = pd.Timedelta("15min")


def generate_signals(candles):
    """Return normalized signals from consecutive BID candle closes."""
    rows = []
    previous = None
    for candle_start, row in candles.iterrows():
        close = row["close"]
        if previous is not None:
            if close > previous:
                direction = "LONG"
            elif close < previous:
                direction = "SHORT"
            else:
                direction = None
            if direction is not None:
                rows.append(
                    {
                        "signal_candle_start": candle_start,
                        "signal_available_time": candle_start + BAR,
                        "direction": direction,
                    }
                )
        previous = close
    return pd.DataFrame(rows, columns=SIGNAL_COLUMNS)
