"""Instrument metadata registry (data-driven, no symbol hardcodes in code).

The registry is a small JSON file so adding a symbol does not require a code
change. ``config/instruments.json`` ships with the common reference symbols;
set ``QBL_INSTRUMENTS_FILE`` to use another registry (tests/ops).
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INSTRUMENTS_PATH = ROOT / "config" / "instruments.json"


class InstrumentRegistryError(ValueError):
    """The instrument registry is missing required/invalid metadata."""


@dataclass
class InstrumentSpec:
    """Execution/account metadata for one symbol (not a market-data adapter)."""

    symbol: str
    quote_currency: str
    contract_size: float
    tick_size: Optional[float] = None
    pip_size: Optional[float] = None
    asset_class: str = "unknown"
    digits: Optional[int] = None
    price_scale: Optional[float] = None
    session: Optional[str] = None
    leverage: Optional[float] = None
    margin_rate: Optional[float] = None
    swap: Optional[float] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict) -> "InstrumentSpec":
        symbol = str(raw.get("symbol", "")).strip().upper()
        quote = str(raw.get("quote_currency", "")).strip().upper()
        if not symbol:
            raise InstrumentRegistryError("instrument.symbol is required")
        if not quote:
            raise InstrumentRegistryError(f"instrument {symbol}: quote_currency is required")
        contract = _positive(raw.get("contract_size"), f"instrument {symbol}: contract_size")
        tick = _optional_non_negative(raw.get("tick_size"), f"instrument {symbol}: tick_size")
        pip = _optional_non_negative(raw.get("pip_size"), f"instrument {symbol}: pip_size")
        digits = raw.get("digits")
        if digits is not None:
            if not isinstance(digits, int) or isinstance(digits, bool) or digits < 0:
                raise InstrumentRegistryError(f"instrument {symbol}: digits must be a non-negative integer")
        scale = _optional_positive(raw.get("price_scale"), f"instrument {symbol}: price_scale")
        leverage = _optional_positive(raw.get("leverage"), f"instrument {symbol}: leverage")
        margin = _optional_non_negative(raw.get("margin_rate"), f"instrument {symbol}: margin_rate")
        swap = _optional_number(raw.get("swap"), f"instrument {symbol}: swap")
        return cls(
            symbol=symbol,
            quote_currency=quote,
            contract_size=contract,
            tick_size=tick,
            pip_size=pip,
            asset_class=str(raw.get("asset_class") or "unknown"),
            digits=digits,
            price_scale=scale,
            session=str(raw["session"]) if raw.get("session") else None,
            leverage=leverage,
            margin_rate=margin,
            swap=swap,
        )


def _number(value, label: str) -> float:
    try:
        n = float(value)
    except (TypeError, ValueError) as exc:
        raise InstrumentRegistryError(f"{label} must be numeric") from exc
    if not math.isfinite(n):
        raise InstrumentRegistryError(f"{label} must be finite")
    return n


def _positive(value, label: str) -> float:
    n = _number(value, label)
    if n <= 0:
        raise InstrumentRegistryError(f"{label} must be > 0")
    return n


def _optional_number(value, label: str) -> Optional[float]:
    return None if value is None else _number(value, label)


def _optional_non_negative(value, label: str) -> Optional[float]:
    n = _optional_number(value, label)
    if n is not None and n < 0:
        raise InstrumentRegistryError(f"{label} must be >= 0")
    return n


def _optional_positive(value, label: str) -> Optional[float]:
    n = _optional_number(value, label)
    if n is not None and n <= 0:
        raise InstrumentRegistryError(f"{label} must be > 0")
    return n


def registry_path() -> Path:
    env = os.environ.get("QBL_INSTRUMENTS_FILE")
    return Path(env) if env else DEFAULT_INSTRUMENTS_PATH


def load_registry(path: str | Path | None = None) -> dict[str, InstrumentSpec]:
    """Load and validate the registry. A missing file means no metadata."""
    target = Path(path) if path is not None else registry_path()
    if not target.is_file():
        return {}
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InstrumentRegistryError(f"cannot read instrument registry {target}: {exc}") from exc
    entries = payload.get("instruments", payload) if isinstance(payload, dict) else None
    if not isinstance(entries, dict):
        raise InstrumentRegistryError(f"instrument registry {target} must contain an 'instruments' object")
    out: dict[str, InstrumentSpec] = {}
    for key, raw in entries.items():
        if not isinstance(raw, dict):
            raise InstrumentRegistryError(f"instrument registry entry {key!r} must be an object")
        item = dict(raw)
        item.setdefault("symbol", key)
        spec = InstrumentSpec.from_dict(item)
        out[spec.symbol] = spec
    return out


def instrument_map(path: str | Path | None = None) -> dict[str, InstrumentSpec]:
    return load_registry(path)


def instrument_catalog(path: str | Path | None = None) -> list[dict]:
    return [spec.to_dict() for spec in load_registry(path).values()]


def resolve_instrument(
    symbol: str,
    explicit: Optional[InstrumentSpec] = None,
    path: str | Path | None = None,
) -> Optional[InstrumentSpec]:
    """Explicit metadata wins; otherwise resolve the symbol from the registry."""
    if explicit is not None:
        return explicit
    return load_registry(path).get((symbol or "").strip().upper())
