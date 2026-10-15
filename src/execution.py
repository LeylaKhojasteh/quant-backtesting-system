"""Pip -> price-distance conversion (v1.0.3).

The engine (``src/backtest.py``) always receives SL/TP as **price distances**.
Users express them as **pips**. This module is the single, shared conversion
boundary used by every workflow (normal run, async job, optimization,
walk-forward): convert once, here, using the resolved ``InstrumentSpec.pip_size``.

Rules:
- pip size is instrument metadata (``InstrumentSpec.pip_size``); never inferred
  from decimal digits or tick size.
- an unknown pip size is an explicit error, not a silent fallback.
- ``pip_size`` must be a positive whole multiple of ``tick_size`` when a tick
  size is known, so a pip is representable on the instrument's tick grid.
- legacy ``stop_distance`` / ``take_profit_distance`` (price units) pass through
  untouched, preserving the exact previous behavior.
"""

from __future__ import annotations

import math

from src.accounting import InstrumentSpec
from src.run_errors import InvalidExecutionError


def price_distance_from_pips(pips: float, pip_size: float) -> float:
    """``pips * pip_size`` — the one and only pip conversion."""
    return float(pips) * float(pip_size)


def validate_pip_size(pip_size: float, tick_size: float | None = None) -> float:
    """Validate instrument pip metadata and tick-grid compatibility."""
    if isinstance(pip_size, bool) or not isinstance(pip_size, (int, float)):
        raise InvalidExecutionError(f"instrument.pip_size must be a number, got {pip_size!r}")
    value = float(pip_size)
    if not math.isfinite(value) or value <= 0:
        raise InvalidExecutionError(f"instrument.pip_size must be > 0, got {pip_size!r}")
    if tick_size is not None:
        tick = float(tick_size)
        if tick <= 0:
            raise InvalidExecutionError(f"instrument.tick_size must be > 0, got {tick_size!r}")
        ratio = value / tick
        if abs(ratio - round(ratio)) > 1e-6:
            raise InvalidExecutionError(
                f"instrument.pip_size {value!r} is not a whole multiple of tick_size {tick!r}"
            )
    return value


def require_pip_size(instrument: InstrumentSpec | None, symbol: str | None = None) -> float:
    """Resolve and validate the pip size for an instrument, or fail clearly."""
    if instrument is None or instrument.pip_size is None:
        name = (symbol or (instrument.symbol if instrument else "") or "instrument")
        raise InvalidExecutionError(
            f"pip size is unknown for {name!r}; SL/TP in pips requires instrument.pip_size "
            "(add it to the instrument catalog or pass an explicit instrument spec)"
        )
    return validate_pip_size(instrument.pip_size, instrument.tick_size)


def resolve_distances(
    *,
    stop_loss_pips: float | None,
    take_profit_pips: float | None,
    stop_distance: float,
    take_profit_distance: float,
    instrument: InstrumentSpec | None,
    symbol: str | None = None,
) -> tuple[float, float, float | None]:
    """Resolve SL/TP to **price distances** (what the engine consumes).

    - pip intent present -> convert once via the resolved pip size.
    - otherwise -> legacy price distances returned unchanged (``pip_size`` None).
    Returns ``(effective_stop, effective_take, pip_size_used)``.
    """
    if stop_loss_pips is None and take_profit_pips is None:
        return float(stop_distance), float(take_profit_distance), None
    pip_size = require_pip_size(instrument, symbol)
    effective_stop = (
        price_distance_from_pips(stop_loss_pips, pip_size)
        if stop_loss_pips is not None
        else float(stop_distance)
    )
    effective_take = (
        price_distance_from_pips(take_profit_pips, pip_size)
        if take_profit_pips is not None
        else float(take_profit_distance)
    )
    return effective_stop, effective_take, pip_size
