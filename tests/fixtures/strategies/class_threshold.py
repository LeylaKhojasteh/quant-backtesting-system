"""Deterministic fixture strategy implemented as a ``Strategy`` subclass.

Proves the loader + interface also work for class-based strategies: the
effective parameters are read via ``effective_parameters()``.
"""

import pandas as pd

from src.strategies.base import Strategy

SIGNAL_COLUMNS = ["signal_candle_start", "signal_available_time", "direction"]
BAR = pd.Timedelta("15min")


class ThresholdStrategy(Strategy):
    name = "Threshold Class"
    version = "test-1.0"
    description = "Class-based strategy using a configurable `threshold`."

    def parameters(self) -> dict:
        return {"threshold": 0.0}

    def generate_signals(self, candles):
        threshold = float(self.effective_parameters()["threshold"])
        rows = []
        previous = None
        for candle_start, row in candles.iterrows():
            close = row["close"]
            if previous is not None:
                change = close - previous
                if change != 0 and abs(change) >= threshold:
                    rows.append(
                        {
                            "signal_candle_start": candle_start,
                            "signal_available_time": candle_start + BAR,
                            "direction": "LONG" if change > 0 else "SHORT",
                        }
                    )
            previous = close
        return pd.DataFrame(rows, columns=SIGNAL_COLUMNS)


STRATEGY = ThresholdStrategy()
