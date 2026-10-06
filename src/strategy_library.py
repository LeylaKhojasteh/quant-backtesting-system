"""Strategy catalog + safe management of imported strategies.

Reference: ARCHITECTURE (Imported Strategy Directory -> Strategy Management
Service -> Local Bridge -> Frontend).

Built-in strategies come from ``run_backtest.BUILTIN_STRATEGIES``; imported
strategies are plain ``*.py`` files in the approved strategy directory. The
existing ``strategy_loader`` remains authoritative for validation.

Only files physically inside the approved directory may be deleted; built-in
strategies can never be deleted. Historical run archives are never touched.
"""

from __future__ import annotations

from pathlib import Path

from src import run_backtest, run_errors, strategy_loader


def parameter_descriptor(default) -> dict:
    if isinstance(default, bool):
        kind = "bool"
    elif isinstance(default, int):
        kind = "int"
    elif isinstance(default, float):
        kind = "float"
    elif isinstance(default, str):
        kind = "string"
    else:
        kind = "unknown"
    return {"type": kind, "default": default}


def catalog_entry(identifier: str, strategy, *, source: str, file: str | None) -> dict:
    meta = strategy.metadata()
    parameters = strategy.parameters()
    return {
        "id": identifier,
        "name": meta.get("name", ""),
        "version": meta.get("version", ""),
        "description": meta.get("description", ""),
        "source": source,
        "file": file,
        "parameters": {key: parameter_descriptor(value) for key, value in parameters.items()},
    }


def list_strategies(strategy_dir: str | Path = "strategies") -> list[dict]:
    """Deterministic catalog: built-in aliases first, then trusted files."""
    entries = []
    for alias in sorted(run_backtest.BUILTIN_STRATEGIES):
        entries.append(
            catalog_entry(alias, run_backtest.BUILTIN_STRATEGIES[alias](), source="builtin", file=None)
        )
    base = Path(strategy_dir)
    if base.is_dir():
        for path in sorted(base.glob("*.py")):
            if path.name.startswith("_"):
                continue
            try:
                strategy = strategy_loader.load_strategy(path)
            except Exception:
                # Not a valid strategy file; discovery is explicit and tolerant.
                continue
            entries.append(catalog_entry(str(path), strategy, source="file", file=str(path)))
    return entries


def is_builtin(selector: str) -> bool:
    return selector in run_backtest.BUILTIN_STRATEGIES


def delete_imported_strategy(selector, strategy_dir: str | Path) -> dict:
    """Delete one imported strategy file inside the approved directory.

    Rejects built-in selectors and any path outside ``strategy_dir``. Historical
    run archives (strategy.py snapshots, results, reports) are not affected.
    """
    if not isinstance(selector, str) or not selector.strip():
        raise run_errors.StrategyNotFoundError("strategy selector is required")
    selector = selector.strip()
    if is_builtin(selector):
        raise run_errors.BuiltinStrategyError(
            f"{selector!r} is a built-in strategy and cannot be deleted"
        )

    base = Path(strategy_dir)
    if Path(selector).name != selector or "/" in selector or "\\" in selector:
        raise run_errors.StrategyDeleteError(
            "only plain strategy filenames inside the approved directory can be deleted"
        )
    target = base / selector
    try:
        target.resolve().relative_to(base.resolve())
    except ValueError as exc:
        raise run_errors.StrategyDeleteError(
            "only strategies inside the approved directory can be deleted"
        ) from exc
    if not target.is_file():
        raise run_errors.StrategyNotFoundError(f"strategy not found: {selector}")
    target.unlink()
    return {"deleted": True, "selector": selector, "file": str(target)}
