"""Strategy interface specification.

Reference: ``ARCHITECTURE.md`` Sections 1-2.

A strategy creates signals and nothing else:

- ``metadata()`` — name, version, description.
- ``parameters()`` — declared defaults for reproducible runs.
- ``configure(effective)`` / ``effective_parameters()`` — receive the validated
  effective parameter values for a run.
- ``generate_signals(candles)`` — normalized signal DataFrame with columns
  ``signal_candle_start``, ``signal_available_time``, ``direction``, plus the
  optional per-signal ``stop_distance`` / ``take_profit_distance`` (positive
  price distances or null; null falls back to the run-level SL/TP).

The strategy must NOT execute trades, calculate portfolio results, modify
market data, or know frontend logic. Execution belongs to ``src.backtest``
(the engine), which stays strategy-independent and only consumes the
normalized signal columns.

``Strategy`` is a plain base class (no third-party dependency) so existing
function-based strategies keep working; ``adapt_strategy`` lets the runner
accept either a ``Strategy`` instance or a legacy ``generate_signals``
function plus metadata/parameters.
"""

from __future__ import annotations


SIGNAL_COLUMNS = ["signal_candle_start", "signal_available_time", "direction"]
#: Optional per-signal execution levels. When a strategy supplies them, the
#: engine prefers them over the run-level SL/TP; when a signal omits them
#: (or they are null), the run-level values apply.
#: - ``stop_distance`` / ``take_profit_distance``: positive price distances.
#: - ``trail_distance``: positive price distance for a simple trailing stop.
#: - ``max_holding_seconds``: positive seconds for a time exit.
POSITIVE_SIGNAL_COLUMNS = (
    "stop_distance",
    "take_profit_distance",
    "trail_distance",
    "trail_after_distance",
    "trail_step",
    "breakeven_after_distance",
    "max_holding_seconds",
    "size_multiplier",
)
#: ``breakeven_offset`` may be zero (exact entry) but never negative.
NON_NEGATIVE_SIGNAL_COLUMNS = ("breakeven_offset",)
OPTIONAL_SIGNAL_COLUMNS = POSITIVE_SIGNAL_COLUMNS + NON_NEGATIVE_SIGNAL_COLUMNS

#: Every column a signal frame may contain. Anything else is a typo or an
#: unsupported feature, and is rejected instead of being silently ignored.
KNOWN_SIGNAL_COLUMNS = tuple(SIGNAL_COLUMNS) + OPTIONAL_SIGNAL_COLUMNS
VALID_DIRECTIONS = ("LONG", "SHORT")


class Strategy:
    """Base class every strategy extends."""

    #: Human-readable strategy name, e.g. "Candle Direction".
    name: str = "Unnamed Strategy"
    #: Strategy version recorded on every run, e.g. "1.0".
    version: str = "0.0"
    #: One-paragraph description for reports and run archives.
    description: str = ""
    #: Effective parameters set by ``configure`` (None until configured).
    _effective: dict | None = None

    def metadata(self) -> dict:
        """Return ``{"name", "version", "description"}`` for this strategy."""
        return {"name": self.name, "version": self.version, "description": self.description}

    def parameters(self) -> dict:
        """Return declared parameter defaults, e.g. ``{"timeframe": "15min"}``.

        Runs store the effective parameters; defaults here document what a
        strategy accepts and keep runs reproducible.
        """
        return {}

    def configure(self, parameters: dict) -> None:
        """Set the effective parameters this instance will run with.

        The runner applies validated overrides on top of ``parameters()`` and
        passes the merged result here before signal generation.
        """
        self._effective = dict(parameters)

    def effective_parameters(self) -> dict:
        """Effective parameters: configured values, else declared defaults."""
        if self._effective is not None:
            return dict(self._effective)
        return dict(self.parameters())

    def generate_signals(self, candles):
        """Return normalized signals from market candles.

        Output columns: ``signal_candle_start``, ``signal_available_time``,
        ``direction`` (``LONG`` / ``SHORT``). A signal becomes tradable only
        once its source candle has closed. Must not modify ``candles``.
        """
        raise NotImplementedError("Strategies must implement generate_signals(candles)")


def resolve_parameters(declared: dict, overrides: dict) -> dict:
    """Merge declared strategy defaults with requested overrides.

    - declared defaults remain when no override is supplied;
    - every override must name a declared parameter (unknown names are
      rejected, never silently ignored);
    - engine/execution settings are not accepted here (see ``src.run_request``).
    """
    declared = dict(declared or {})
    overrides = dict(overrides or {})
    unknown = sorted(str(k) for k in overrides if k not in declared)
    if unknown:
        raise ValueError(
            f"unknown strategy parameter(s): {unknown}; "
            f"declared parameters: {sorted(declared)}"
        )
    effective = dict(declared)
    effective.update(overrides)
    return effective


def validate_signals(signals) -> list[str]:
    """Check a signal frame against the normalized contract.

    Returns a list of violations (empty = valid). Works with pandas
    DataFrames without importing pandas at module load.
    """
    errors: list[str] = []
    missing = [c for c in SIGNAL_COLUMNS if c not in list(signals.columns)]
    if missing:
        errors.append(f"signals is missing required columns: {missing}")
        return errors
    # A misspelled optional column would otherwise disable its feature with no
    # error at all, so unknown columns are rejected explicitly.
    unknown = [c for c in list(signals.columns) if c not in KNOWN_SIGNAL_COLUMNS]
    if unknown:
        errors.append(
            f"signals contains unknown columns: {unknown}; "
            f"allowed columns: {list(KNOWN_SIGNAL_COLUMNS)}"
        )
    invalid = sorted(set(signals["direction"].unique()) - set(VALID_DIRECTIONS))
    if invalid:
        errors.append(f"signals contains invalid directions: {invalid}")
    if signals.empty:
        return errors
    import pandas as pd

    for col in ("signal_candle_start", "signal_available_time"):
        series = signals[col]
        if not pd.api.types.is_datetime64_any_dtype(series):
            errors.append(f"{col} must be datetime64")
            continue
        if series.dt.tz is None:
            errors.append(f"{col} must be timezone-aware")
            continue
        if series.isna().any():
            errors.append(f"{col} contains NaT")
    if not errors:
        if (signals["signal_available_time"] < signals["signal_candle_start"]).any():
            errors.append("signal_available_time must be >= signal_candle_start")
    def _values(col):
        if col not in list(signals.columns):
            return None
        return signals[col].tolist()

    import numbers

    def _is_num(v):
        return isinstance(v, numbers.Real) and v == v

    for col in POSITIVE_SIGNAL_COLUMNS:
        values = _values(col)
        if values is None or all(v is None for v in values):
            continue
        if any(v is not None and not (_is_num(v) and v > 0) for v in values):
            errors.append(f"{col} must be a positive number or null")

    for col in NON_NEGATIVE_SIGNAL_COLUMNS:
        values = _values(col)
        if values is None or all(v is None for v in values):
            continue
        if any(v is not None and not (_is_num(v) and v >= 0) for v in values):
            errors.append(f"{col} must be zero or a positive number, or null")

    # Dependent options must not be silently ignored: they only make sense
    # together with the feature they modify.
    def _has(col, index):
        values = _values(col)
        return values is not None and values[index] is not None

    trail = _values("trail_distance")
    if _values("trail_step") is not None:
        for index, step in enumerate(_values("trail_step")):
            if step is not None and (trail is None or trail[index] is None):
                errors.append("trail_step requires trail_distance on the same signal")
                break
    if _values("breakeven_offset") is not None:
        for index, offset in enumerate(_values("breakeven_offset")):
            if offset is not None and not _has("breakeven_after_distance", index):
                errors.append(
                    "breakeven_offset requires breakeven_after_distance on the same signal"
                )
                break
    return errors


class _FunctionStrategy(Strategy):
    """Adapter so legacy ``generate_signals`` functions fit the interface.

    A function that declares a ``parameters`` keyword (or ``**kwargs``) receives
    the effective parameters dict; a function that declares a ``timeframe``
    keyword (or ``**kwargs``) receives the market timeframe when the caller
    supplies one. A plain ``generate_signals(candles)`` function keeps its
    current call signature.
    """

    def __init__(
        self,
        fn,
        name: str,
        version: str,
        description: str,
        params: dict,
        passes_parameters: bool = False,
        passes_timeframe: bool = False,
    ):
        self._fn = fn
        self.name = name
        self.version = version
        self.description = description
        self._params = dict(params)
        self._passes_parameters = passes_parameters
        self._passes_timeframe = passes_timeframe

    def parameters(self) -> dict:
        return dict(self._params)

    def generate_signals(self, candles, timeframe=None):
        kwargs: dict = {}
        if self._passes_parameters:
            kwargs["parameters"] = self.effective_parameters()
        if self._passes_timeframe and timeframe is not None:
            kwargs["timeframe"] = timeframe
        if self._passes_parameters or ("timeframe" in kwargs):
            return self._fn(candles, **kwargs)
        return self._fn(candles)


def adapt_strategy(
    fn,
    *,
    name: str,
    version: str,
    description: str = "",
    params: dict | None = None,
    passes_parameters: bool = False,
    passes_timeframe: bool = False,
) -> Strategy:
    """Wrap a legacy ``generate_signals(candles)`` function as a Strategy."""
    return _FunctionStrategy(fn, name, version, description, params or {}, passes_parameters, passes_timeframe)
