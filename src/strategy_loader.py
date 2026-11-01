"""Generic strategy loader.

Reference: ``ARCHITECTURE.md`` Sections 1-2 (Strategy layer / interface).

Load a trusted local Python strategy file and expose it through the approved
``Strategy`` interface. This is a trusted local research tool: normal Python
module loading is used (no sandbox, no ``eval``/``exec`` custom mechanisms).

A strategy file may expose either:

1. a ``Strategy`` subclass or instance named ``STRATEGY`` / ``strategy`` /
   ``Strategy``, or
2. a legacy ``generate_signals(candles)`` function with optional module
   metadata (``NAME``/``VERSION``/``DESCRIPTION``/``PARAMETERS``), which is
   adapted with the existing ``adapt_strategy`` helper, or
3. an external ``run_strategy(data, ctx)`` entrypoint owned by the strategy
   itself (strategy-owned execution): the file must also declare ``PARAMETERS``
   as a mapping. The downstream trade pipeline is shared; only the producer
   differs.

Signal paths (1-2) are never silently substituted by the external path and
vice versa: detection is capability-based and ordered — explicit ``Strategy``
first, then ``generate_signals``, then ``run_strategy``.

The loader validates the interface and metadata before any run: it rejects a
missing file, an unimportable module, a missing interface, invalid metadata or
parameters, and (at signal time) signals that violate the normalized contract.
It never silently falls back to another strategy.
"""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path

from src.strategies import base as strategy_base


class StrategyLoadError(ValueError):
    """Raised when a strategy file cannot be loaded or is not valid."""


#: Loader detection order: explicit Strategy -> generate_signals -> run_strategy.
STRATEGY_KIND_SIGNAL = "signal"
STRATEGY_KIND_EXTERNAL = "external"


def manifest_for(strategy) -> dict:
    """One shared strategy manifest for catalog, config and reports.

    ``kind`` is ``"signal"`` (engine executes) or ``"external"``
    (strategy-owned execution via ``run_strategy(data, ctx)``).
    """
    meta = strategy.metadata()
    parameters = strategy.parameters()
    kind = getattr(strategy, "kind", STRATEGY_KIND_SIGNAL)
    if kind not in (STRATEGY_KIND_SIGNAL, STRATEGY_KIND_EXTERNAL):
        kind = STRATEGY_KIND_SIGNAL
    return {
        "name": str(meta.get("name", "")),
        "version": str(meta.get("version", "")),
        "description": str(meta.get("description", "")),
        "kind": kind,
        "parameters": dict(parameters) if isinstance(parameters, dict) else {},
    }


def _accepts_parameters(fn) -> bool:
    """True when a function strategy declares a ``parameters`` keyword.

    The convention is explicit: a function is passed the effective parameters
    only if it names a ``parameters`` argument (or accepts ``**kwargs``).
    Plain ``generate_signals(candles)`` functions stay legacy.
    """
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
    if "parameters" in params:
        return True
    return any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())


def _accepts_timeframe(fn) -> bool:
    """True when a strategy accepts the market ``timeframe``.

    A function/class method declaring a ``timeframe`` keyword (or
    ``**kwargs``) receives it; plain ``generate_signals(candles)`` strategies
    keep their current call signature and are never passed a timeframe.
    """
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
    if "timeframe" in params:
        return True
    return any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())


class _ExternalStrategy(strategy_base.Strategy):
    """Adapter for strategy-owned execution via ``run_strategy(data, ctx)``.

    It intentionally does NOT implement the tick engine contract: it has no
    ``generate_signals`` semantics and is never passed to the engine. The
    adapter only carries metadata, declared parameters, and the entrypoint.
    """

    kind = STRATEGY_KIND_EXTERNAL

    def __init__(self, fn, name: str, version: str, description: str, params: dict):
        self._fn = fn
        self.name = name
        self.version = version
        self.description = description
        self._params = dict(params)

    def metadata(self):
        return {"name": self.name, "version": self.version, "description": self.description}

    def parameters(self) -> dict:
        return dict(self._params)

    def configure(self, params: dict) -> None:
        merged = dict(self._params)
        merged.update(params or {})
        self._params = merged

    def effective_parameters(self) -> dict:
        return dict(self._params)

    def run_strategy(self, data, ctx):
        return self._fn(data, ctx)


def _import_module(file_path: Path):
    module_name = f"qbl_strategy_{file_path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, str(file_path.resolve()))
    if spec is None or spec.loader is None:
        raise StrategyLoadError(f"cannot load strategy module from {file_path}")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001 - surface the real import failure
        raise StrategyLoadError(
            f"failed to import strategy {file_path}: {type(exc).__name__}: {exc}"
        ) from exc
    return module


def _attr(module, *names, default=None):
    for name in names:
        if hasattr(module, name):
            value = getattr(module, name)
            if value is not None:
                return value
    return default


def _validate(strategy, file_path: Path):
    if getattr(strategy, "kind", STRATEGY_KIND_SIGNAL) == STRATEGY_KIND_EXTERNAL:
        if not callable(getattr(strategy, "run_strategy", None)):
            raise StrategyLoadError(
                f"strategy {file_path} has no callable run_strategy(data, ctx)"
            )
    elif not callable(getattr(strategy, "generate_signals", None)):
        raise StrategyLoadError(
            f"strategy {file_path} has no callable generate_signals(candles)"
        )
    try:
        meta = strategy.metadata()
    except Exception as exc:  # noqa: BLE001
        raise StrategyLoadError(f"strategy {file_path} metadata() failed: {exc}") from exc
    if not isinstance(meta, dict):
        raise StrategyLoadError(f"strategy {file_path} metadata() must return a mapping")
    if not str(meta.get("name", "")).strip():
        raise StrategyLoadError(f"strategy {file_path} metadata.name is required")
    if not str(meta.get("version", "")).strip():
        raise StrategyLoadError(f"strategy {file_path} metadata.version is required")
    try:
        params = strategy.parameters()
    except Exception as exc:  # noqa: BLE001
        raise StrategyLoadError(f"strategy {file_path} parameters() failed: {exc}") from exc
    if not isinstance(params, dict):
        raise StrategyLoadError(f"strategy {file_path} parameters() must return a mapping")
    return strategy


def load_strategy(path: str | Path):
    """Load and validate a strategy from a trusted local ``.py`` file."""
    file_path = Path(path)
    if not file_path.is_file():
        raise StrategyLoadError(f"strategy file not found: {path}")
    if file_path.suffix != ".py":
        raise StrategyLoadError(f"strategy file must be a .py file: {path}")

    module = _import_module(file_path)

    # 1) An explicit Strategy instance or subclass.
    for attr in ("STRATEGY", "strategy", "Strategy"):
        obj = getattr(module, attr, None)
        if obj is None:
            continue
        if isinstance(obj, strategy_base.Strategy):
            return _validate(obj, file_path)
        if isinstance(obj, type) and issubclass(obj, strategy_base.Strategy):
            return _validate(obj(), file_path)

    # 2) A legacy generate_signals function, adapted with the existing helper.
    fn = getattr(module, "generate_signals", None)
    if callable(fn):
        name = _attr(module, "NAME", "name", default=file_path.stem)
        version = _attr(module, "VERSION", "version", default="1.0")
        description = _attr(module, "DESCRIPTION", "description", default="")
        params = _attr(module, "PARAMETERS", "parameters", default={})
        if not isinstance(params, dict):
            raise StrategyLoadError(f"strategy {file_path} PARAMETERS must be a mapping")
        wrapped = strategy_base.adapt_strategy(
            fn,
            name=str(name),
            version=str(version),
            description=str(description),
            params=dict(params),
            passes_parameters=_accepts_parameters(fn),
            passes_timeframe=_accepts_timeframe(fn),
        )
        return _validate(wrapped, file_path)

    # 3) An external strategy-owned entrypoint: run_strategy(data, ctx).
    run_fn = getattr(module, "run_strategy", None)
    if callable(run_fn):
        name = _attr(module, "NAME", "name", default=file_path.stem)
        version = _attr(module, "VERSION", "version", default="1.0")
        description = _attr(module, "DESCRIPTION", "description", default="")
        params = _attr(module, "PARAMETERS", "parameters", default=None)
        if params is None:
            raise StrategyLoadError(
                f"strategy {file_path} exposes run_strategy(data, ctx) but no "
                "PARAMETERS mapping (declare strategy inputs, even if empty)"
            )
        if not isinstance(params, dict):
            raise StrategyLoadError(f"strategy {file_path} PARAMETERS must be a mapping")
        return _validate(
            _ExternalStrategy(
                run_fn, name=str(name), version=str(version),
                description=str(description), params=dict(params),
            ),
            file_path,
        )

    raise StrategyLoadError(
        f"strategy {file_path} does not expose a Strategy, "
        "generate_signals(candles) or run_strategy(data, ctx)"
    )


def generate_validated_signals(strategy, candles, timeframe=None):
    """Run a strategy and reject signals that violate the normalized contract.

    ``timeframe`` is the market timeframe that produced ``candles``. It is
    forwarded only to strategies that declare a ``timeframe`` keyword (or
    ``**kwargs``); legacy ``generate_signals(candles)`` strategies are called
    exactly as before.
    """
    try:
        import inspect as _inspect

        try:
            params = _inspect.signature(strategy.generate_signals).parameters
            wants_timeframe = "timeframe" in params or any(
                p.kind is _inspect.Parameter.VAR_POSITIONAL
                or p.kind is _inspect.Parameter.VAR_KEYWORD
                for p in params.values()
            )
        except (TypeError, ValueError):
            wants_timeframe = False
        if wants_timeframe and timeframe is not None:
            signals = strategy.generate_signals(candles, timeframe=timeframe)
        else:
            signals = strategy.generate_signals(candles)
    except Exception as exc:  # noqa: BLE001
        name = strategy.metadata().get("name", "strategy")
        raise StrategyLoadError(f"{name} generate_signals failed: {exc}") from exc
    violations = strategy_base.validate_signals(signals)
    if violations:
        raise StrategyLoadError(f"strategy produced invalid signals: {violations}")
    return signals
