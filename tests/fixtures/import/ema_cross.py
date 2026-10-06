"""Deterministic importable strategy fixture: simple moving-average crossover.

Declared parameters change the signals, so an imported run proves parameter
handling. Uses the function convention (``parameters`` keyword).
"""

import pandas as pd

NAME = "EMA Cross"
VERSION = "import-1.0"
DESCRIPTION = "Long while the fast SMA is above the slow SMA, short otherwise."
PARAMETERS = {"fast_period": 3, "slow_period": 8}

SIGNAL_COLUMNS = ["signal_candle_start", "signal_available_time", "direction"]
BAR = pd.Timedelta("15min")


def _mean(values):
    return sum(values) / len(values)


def generate_signals(candles, parameters):
    fast = int(parameters["fast_period"])
    slow = int(parameters["slow_period"])
    if fast < 1 or slow < 1:
        raise ValueError("periods must be >= 1")
    if fast > slow:
        raise ValueError("fast_period must be <= slow_period")

    closes = list(candles["close"])
    index = list(candles.index)
    rows = []
    for i in range(slow - 1, len(closes)):
        fast_ma = _mean(closes[i - fast + 1 : i + 1])
        slow_ma = _mean(closes[i - slow + 1 : i + 1])
        rows.append(
            {
                "signal_candle_start": index[i],
                "signal_available_time": index[i] + BAR,
                "direction": "LONG" if fast_ma >= slow_ma else "SHORT",
            }
        )
    return pd.DataFrame(rows, columns=SIGNAL_COLUMNS)
