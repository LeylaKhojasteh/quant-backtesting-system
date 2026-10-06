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
   adapted with the existing ``adapt_strategy`` helper.

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
    if not callable(getattr(strategy, "generate_signals", None)):
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
        )
        return _validate(wrapped, file_path)

    raise StrategyLoadError(
        f"strategy {file_path} does not expose a Strategy or generate_signals(candles)"
    )


def generate_validated_signals(strategy, candles):
    """Run a strategy and reject signals that violate the normalized contract."""
    try:
        signals = strategy.generate_signals(candles)
    except Exception as exc:  # noqa: BLE001
        name = strategy.metadata().get("name", "strategy")
        raise StrategyLoadError(f"{name} generate_signals failed: {exc}") from exc
    violations = strategy_base.validate_signals(signals)
    if violations:
        raise StrategyLoadError(f"strategy produced invalid signals: {violations}")
    return signals
