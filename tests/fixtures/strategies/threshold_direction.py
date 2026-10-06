"""Deterministic fixture strategy with configurable parameters.

Signals depend on declared strategy parameters, so the same data with different
parameter values produces different signals (and therefore a different result).
This proves generic parameter passing without touching the engine.

Convention: a function strategy that declares a ``parameters`` keyword receives
the effective parameters dict.
"""

import pandas as pd

NAME = "Threshold Direction"
VERSION = "test-1.0"
DESCRIPTION = "Emit a signal only when the close moves at least `threshold`."

# Declared defaults; types drive CLI `--param` parsing.
PARAMETERS = {
    "threshold": 0.0,   # float: minimum absolute close-to-close move
    "side": "both",     # str: both | long | short
    "enabled": True,    # bool: master switch
}

SIGNAL_COLUMNS = ["signal_candle_start", "signal_available_time", "direction"]

BAR = pd.Timedelta("15min")


def generate_signals(candles, parameters):
    """Return normalized signals for the configured effective parameters."""
    threshold = float(parameters["threshold"])
    side = str(parameters["side"])
    enabled = bool(parameters["enabled"])
    if side not in ("both", "long", "short"):
        raise ValueError(f"side must be both|long|short, got {side!r}")
    if not enabled:
        return pd.DataFrame(columns=SIGNAL_COLUMNS)

    rows = []
    previous = None
    for candle_start, row in candles.iterrows():
        close = row["close"]
        if previous is not None:
            change = close - previous
            if change != 0 and abs(change) >= threshold:
                direction = "LONG" if change > 0 else "SHORT"
                if side == "both" or (side == "long" and direction == "LONG") or (
                    side == "short" and direction == "SHORT"
                ):
                    rows.append(
                        {
                            "signal_candle_start": candle_start,
                            "signal_available_time": candle_start + BAR,
                            "direction": direction,
                        }
                    )
        previous = close
    return pd.DataFrame(rows, columns=SIGNAL_COLUMNS)
