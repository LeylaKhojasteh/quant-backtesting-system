"""Import a trusted local strategy file into the approved strategy directory.

Reference: ``ARCHITECTURE.md`` — the existing Strategy Loader (``strategy_loader``)
remains authoritative; this module only validates and stores the file.

Flow::

    uploaded source -> temporary candidate -> load_strategy(...)
        -> metadata/parameters/interface validation -> atomic save

Invalid input never touches the permanent directory and never leaves temp
files. This is a trusted LOCAL research tool: no sandbox/container isolation.
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

from src import strategy_loader

#: Maximum accepted strategy source size (bytes).
MAX_STRATEGY_BYTES = 64 * 1024
#: Plain file name: starts alphanumeric, letters/digits/_/- then ``.py``.
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]*\.py$")


class StrategyImportError(ValueError):
    """Base class for expected import failures (structured code + status)."""

    code = "strategy_import_failed"
    http_status = 400

    def __init__(self, message: str = ""):
        super().__init__(message)
        self.message = str(message)


class InvalidStrategyFileError(StrategyImportError):
    code = "invalid_strategy_file"
    http_status = 400


class StrategyConflictError(StrategyImportError):
    code = "strategy_conflict"
    http_status = 409


def validate_filename(filename) -> str:
    """Return a safe plain ``*.py`` file name, or raise."""
    if not isinstance(filename, str) or not filename.strip():
        raise InvalidStrategyFileError("filename is required")
    name = filename.strip()
    if name != Path(name).name or "/" in name or "\\" in name or ":" in name or ".." in name:
        raise InvalidStrategyFileError("filename must be a plain file name (no path)")
    if not name.endswith(".py"):
        raise InvalidStrategyFileError("only .py strategy files are accepted")
    if name.startswith("_"):
        raise InvalidStrategyFileError("filename must not start with '_' (reserved)")
    if not _NAME_RE.match(name):
        raise InvalidStrategyFileError(
            "filename may only contain letters, digits, '_', '-' and must end with .py"
        )
    return name


def import_strategy(filename, source, *, strategy_dir: str | Path):
    """Validate and atomically store one strategy file.

    Returns ``(strategy, target_path)``. Raises ``StrategyImportError`` (or a
    subclass) on any rejection; the permanent directory is only written on
    success, and no temp files remain either way.
    """
    name = validate_filename(filename)
    if not isinstance(source, str):
        raise InvalidStrategyFileError("source must be a string")
    if not source.strip():
        raise InvalidStrategyFileError("strategy source is empty")
    size = len(source.encode("utf-8"))
    if size > MAX_STRATEGY_BYTES:
        raise InvalidStrategyFileError(
            f"strategy file too large ({size} bytes > {MAX_STRATEGY_BYTES})"
        )

    base = Path(strategy_dir)
    base.mkdir(parents=True, exist_ok=True)
    target = base / name
    if target.exists():
        raise StrategyConflictError(
            f"strategy {name!r} already exists; choose another filename"
        )

    # 1) Validate a temporary candidate using the authoritative loader. The
    #    candidate keeps the real filename so metadata fallbacks stay correct.
    with tempfile.TemporaryDirectory(prefix="qbl-import-") as tmpdir:
        candidate = Path(tmpdir) / name
        candidate.write_text(source, encoding="utf-8", newline="")
        try:
            strategy = strategy_loader.load_strategy(candidate)
        except strategy_loader.StrategyLoadError as exc:
            raise InvalidStrategyFileError(f"invalid strategy: {exc}") from exc

    # 2) Atomic save into the approved directory (same-dir temp + os.replace).
    fd, tmp_save = tempfile.mkstemp(prefix=".qbl-save-", suffix=".tmp", dir=str(base))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(source)
        os.replace(tmp_save, target)
    except Exception:
        Path(tmp_save).unlink(missing_ok=True)
        raise

    return strategy, target
