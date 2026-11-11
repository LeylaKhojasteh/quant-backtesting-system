"""Account / trading-economics layer (OPTIONAL).

Reference: ARCHITECTURE (existing Engine -> price-term BacktestResult ->
Account / Trading Economics Layer -> optional account metrics).

This module converts an existing, authoritative price-term ``BacktestResult``
into account-currency economics. It never touches the verified engine and never
mutates execution prices.

Truthfulness rules enforced here:

- The execution engine already fills at BID/ASK, so spread is already reflected
  in ``pnl_price``. Spread is NEVER subtracted again.
- Account PnL is computed only when the instrument's quote currency equals the
  account currency (deterministic 1:1). No FX rate is ever invented.
- No slippage, swap/financing, leverage or margin simulation is added.
- Metrics that cannot be combined honestly stay ``None`` (never zero-padded).

Pure data + pure functions (standard library only).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Optional

from src.instruments import (
    InstrumentSpec,
    instrument_catalog as _instrument_catalog,
    load_registry,
    resolve_instrument as _resolve_instrument,
)

# --------------------------------------------------------------------------
# Contracts
# --------------------------------------------------------------------------


@dataclass
class AccountModel:
    """A small, explicit account model (v1: fixed lot sizing, no risk %)."""

    account_currency: str = "USD"
    starting_balance: float = 10000.0
    position_size_lots: float = 1.0
    commission_per_lot_round_turn: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


#: Compatibility snapshot for callers/tests that import ``INSTRUMENT_CATALOG``.
#: The authoritative registry is ``config/instruments.json`` (editable data).
INSTRUMENT_CATALOG: dict[str, InstrumentSpec] = load_registry()


def instrument_catalog() -> list[dict]:
    """Catalog entries for the frontend instrument helper (metadata only)."""
    return _instrument_catalog()


def resolve_instrument(symbol: str, explicit: Optional[InstrumentSpec]) -> Optional[InstrumentSpec]:
    """Prefer an explicit spec; otherwise resolve from the data-driven registry."""
    return _resolve_instrument(symbol, explicit)


# --------------------------------------------------------------------------
# Compatibility
# --------------------------------------------------------------------------


def compatibility_reason(account: AccountModel, instrument: InstrumentSpec) -> Optional[str]:
    """Return why account PnL cannot be computed, or None when compatible.

    v1 supports only the deterministic case where the instrument's quote
    currency equals the account currency. FX conversion is out of scope.
    """
    if (instrument.quote_currency or "").strip().upper() != (account.account_currency or "").strip().upper():
        return (
            "FX conversion unavailable: instrument quote currency "
            f"{instrument.quote_currency!r} does not match account currency "
            f"{account.account_currency!r}. Account PnL is not converted."
        )
    return None


# --------------------------------------------------------------------------
# Per-trade economics
# --------------------------------------------------------------------------


def trade_account_pnl(pnl_price: float, instrument: InstrumentSpec, account: AccountModel, size_multiplier: float = 1.0) -> tuple[float, float, float]:
    """Return ``(gross, commission, net)`` in account currency for one trade.

    gross = price-term PnL x contract size x lots x size_multiplier. Spread is
    already inside ``pnl_price`` and is not applied again. commission is a
    per-lot round-turn charge, applied exactly once per closed trade, scaled
    by the same multiplier. Price-term metrics are never scaled.
    """
    lots = float(account.position_size_lots) * float(size_multiplier)
    gross = float(pnl_price) * float(instrument.contract_size) * lots
    commission = float(account.commission_per_lot_round_turn) * lots
    return gross, commission, gross - commission


def _get(obj, key, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _round(value, digits: int = 6):
    if value is None:
        return None
    return round(float(value), digits)


# --------------------------------------------------------------------------
# Report builder
# --------------------------------------------------------------------------


def unavailable_report(reason: str, account, instrument) -> dict:
    return {
        "available": False,
        "reason_unavailable": reason,
        "account_currency": getattr(account, "account_currency", None),
        "model": account.to_dict() if account is not None else None,
        "instrument": instrument.to_dict() if instrument is not None else None,
        "metrics": None,
        "trades": [],
        "balance_curve": [],
    }


def build_account_report(trades: list, account: AccountModel, instrument: InstrumentSpec) -> dict:
    """Build the account report from already-validated price-term trade records.

    ``trades`` may be ``TradeRecord`` objects or plain dicts. Trade order in the
    output follows exit order (ascending exit_time, then trade_id), matching the
    archived equity curve so the frontend can align balance points by index.
    """
    reason = compatibility_reason(account, instrument)
    if reason is not None:
        return unavailable_report(reason, account, instrument)

    ordered = sorted(
        trades,
        key=lambda t: (str(_get(t, "exit_time", "")), int(_get(t, "trade_id", 0))),
    )

    starting = float(account.starting_balance)
    balance = starting
    rows: list[dict] = []
    curve: list[dict] = []
    nets: list[float] = []
    grosses: list[float] = []
    commissions: list[float] = []

    for t in ordered:
        gross, commission, net = trade_account_pnl(
            float(_get(t, "pnl_price", 0.0)), instrument, account,
            float(_get(t, "size_multiplier", 1.0) or 1.0),
        )
        balance += net
        nets.append(net)
        grosses.append(gross)
        commissions.append(commission)
        rows.append({
            "trade_id": int(_get(t, "trade_id", 0)),
            "gross_account_pnl": _round(gross),
            "commission": _round(commission),
            "net_account_pnl": _round(net),
            "balance_after": _round(balance),
        })
        curve.append({
            "timestamp": str(_get(t, "exit_time", "")),
            "realized_balance": _round(balance),
        })

    peak = starting
    running = starting
    max_dd = 0.0
    for net in nets:
        running += net
        peak = max(peak, running)
        max_dd = max(max_dd, peak - running)

    net_total = sum(nets)
    gross_profit = sum(g for g in grosses if g > 0)
    gross_loss = sum(g for g in grosses if g < 0)
    total = len(rows)

    metrics = {
        "starting_balance": _round(starting),
        "ending_balance": _round(balance),
        "net_pnl": _round(net_total),
        "gross_profit": _round(gross_profit),
        "gross_loss": _round(gross_loss),
        "total_commission": _round(sum(commissions)),
        "total_return_pct": _round((net_total / starting) * 100.0) if starting else None,
        "avg_trade_pnl": _round(net_total / total) if total else None,
        "max_drawdown": _round(max_dd),
        "max_drawdown_pct": _round((max_dd / peak) * 100.0) if peak > 0 else None,
        "recovery_factor": _round(net_total / max_dd) if max_dd else None,
    }

    return {
        "available": True,
        "reason_unavailable": None,
        "account_currency": account.account_currency,
        "model": account.to_dict(),
        "instrument": instrument.to_dict(),
        "metrics": metrics,
        "trades": rows,
        "balance_curve": curve,
        "notes": [
            "Realized balance from closed trades; not tick-by-tick floating equity.",
            "Spread is already inside the engine's BID/ASK fills and is not re-applied.",
            "No slippage, swap/financing, leverage or margin simulation is applied.",
        ],
    }
