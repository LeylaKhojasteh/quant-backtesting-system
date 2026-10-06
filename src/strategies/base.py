"""Strategy interface specification.

Reference: ``ARCHITECTURE.md`` Sections 1-2.

A strategy creates signals and nothing else:

- ``metadata()`` — name, version, description.
- ``parameters()`` — declared defaults for reproducible runs.
- ``configure(effective)`` / ``effective_parameters()`` — receive the validated
  effective parameter values for a run.
- ``generate_signals(candles)`` — normalized signal DataFrame with columns
  ``signal_candle_start``, ``signal_available_time``, ``direction``.

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
    invalid = sorted(set(signals["direction"].unique()) - set(VALID_DIRECTIONS))
    if invalid:
        errors.append(f"signals contains invalid directions: {invalid}")
    return errors


class _FunctionStrategy(Strategy):
    """Adapter so legacy ``generate_signals`` functions fit the interface.

    A function that declares a ``parameters`` keyword (or ``**kwargs``) receives
    the effective parameters dict; a plain ``generate_signals(candles)``
    function keeps its current call signature.
    """

    def __init__(
        self,
        fn,
        name: str,
        version: str,
        description: str,
        params: dict,
        passes_parameters: bool = False,
    ):
        self._fn = fn
        self.name = name
        self.version = version
        self.description = description
        self._params = dict(params)
        self._passes_parameters = passes_parameters

    def parameters(self) -> dict:
        return dict(self._params)

    def generate_signals(self, candles):
        if self._passes_parameters:
            return self._fn(candles, parameters=self.effective_parameters())
        return self._fn(candles)


def adapt_strategy(
    fn,
    *,
    name: str,
    version: str,
    description: str = "",
    params: dict | None = None,
    passes_parameters: bool = False,
) -> Strategy:
    """Wrap a legacy ``generate_signals(candles)`` function as a Strategy."""
    return _FunctionStrategy(fn, name, version, description, params or {}, passes_parameters)
