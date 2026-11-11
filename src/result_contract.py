"""BacktestResult contract — communication layer between engine and UI/reports.

Reference: ``ARCHITECTURE.md`` Section 4 (Result Contract).

A ``BacktestResult`` is a plain-data, JSON-serializable record. It is the
only thing the engine hands to downstream consumers (UI, report archive).
The engine itself is untouched by this module; ``trades_from_engine``
adapts the engine's trade DataFrame into contract records, including the
exit-reason normalization ``END_OF_DATA -> EOD``.

Conventions (shared with the frontend ``BacktestRun`` schema):

- Timestamps are ISO-8601 UTC strings (``...Z``), never naive datetimes.
- PnL is in instrument price terms unless an account model says otherwise.
  Account-currency fields stay ``None`` until an account model exists.
- MAE/MFE are optional for backward compatibility: current engine output
  carries observed excursions (MAE ``<= 0``, MFE ``>= 0``, price terms),
  while legacy archives without excursions keep ``mae=None, mfe=None``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
import json
from pathlib import Path


#: Additive contract change for strategy-owned execution: the enum stays
#: closed, the producer model is recorded, and raw strategy reasons are kept.
CONTRACT_VERSION = "1.1"

#: Engine-owned exits: TP | SL | TSL (trailing) | BE (break-even stop) |
#: TIME (time exit) | EOD.
ENGINE_EXIT_REASONS = ("TP", "SL", "TSL", "BE", "TIME", "EOD")

#: Full closed enum. ``STRATEGY`` covers any strategy-owned exit reason; the
#: original text is preserved in ``TradeRecord.exit_reason_detail`` so nothing
#: is silently relabeled (e.g. ``opposite_breakout`` stays inspectable).
EXIT_REASONS = ("TP", "SL", "TSL", "BE", "TIME", "EOD", "STRATEGY")

#: Who produced the trades: ``engine`` (tick engine, default, backward
#: compatible) or ``strategy`` (strategy-owned execution).
EXECUTION_MODELS = ("engine", "strategy", "candle")

# Engine emits "END_OF_DATA" for a position closed at the end of the data.
# The contract canonicalizes it to "EOD" so engine output, stored runs and
# the UI vocabulary stay identical. The engine itself is not modified.
ENGINE_EXIT_REASON_MAP = {
    "TP": "TP", "SL": "SL", "TSL": "TSL", "BE": "BE", "TIME": "TIME",
    "END_OF_DATA": "EOD",
}

# Strategy-owned exit reasons canonicalized to the closed enum. Unknown
# tokens fall through to ``STRATEGY`` with the raw text preserved.
STRATEGY_EXIT_REASON_MAP = {
    "STOP_LOSS": "SL",
    "STOP_LOSS_GAP": "SL",
    "TAKE_PROFIT": "TP",
    "TAKE_PROFIT_GAP": "TP",
    "END_OF_DATA": "EOD",
    "BREAKEVEN": "BE",
    "BREAK_EVEN": "BE",
}

END_OF_DATA_REASONS = {"END_OF_DATA", "end_of_data", "EOD"}


def utc_now_iso() -> str:
    """Current UTC instant as an ISO-8601 ``Z`` string."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class StrategyInfo:
    """Strategy metadata (ARCHITECTURE.md Section 2)."""

    name: str
    version: str
    description: str = ""


@dataclass
class RunConfig:
    """Configuration this run was executed with."""

    symbol: str
    timeframe: str
    start: str  # ISO-8601 UTC: actual first observed tick (data coverage)
    end: str  # ISO-8601 UTC: actual last observed tick (data coverage)
    parameters: dict
    data_source: str = ""
    data_fingerprint: str = ""
    # The user's requested backtest window (distinct from actual tick coverage
    # above). Optional and additive: absent for legacy runs, in which case the
    # requested window safely falls back to the observed coverage.
    requested_start: str = ""
    requested_end: str = ""
    #: Optional instrument metadata snapshot (data-driven registry or explicit).
    instrument: dict | None = None


@dataclass
class TradeRecord:
    """One closed trade. Prices/PnL are price terms; MAE/MFE optional."""

    trade_id: int
    direction: str  # LONG | SHORT
    signal_candle_start: str
    signal_available_time: str
    entry_time: str
    entry_price: float
    #: None when that exit is disabled for the run (optional SL/TP).
    stop_loss: float | None
    take_profit: float | None
    exit_time: str
    exit_price: float
    exit_reason: str  # TP | SL | EOD | STRATEGY
    pnl_price: float
    duration_seconds: float
    spread_at_entry: float
    spread_at_exit: float
    mae: float | None = None
    mfe: float | None = None
    #: Raw strategy-owned reason (e.g. ``opposite_breakout``). None for
    #: engine-owned trades (whose canonical reason is self-describing) and
    #: for every archive written before this field existed.
    exit_reason_detail: str | None = None
    #: Per-trade size multiplier (default 1.0). Scales account economics
    #: only; price-term fields are always per unit and never scaled.
    size_multiplier: float = 1.0


@dataclass
class EquityPoint:
    """One equity-curve point. Balance/equity need an account model."""

    timestamp: str
    cumulative_pnl: float
    balance: float | None = None
    equity: float | None = None


@dataclass
class Metrics:
    """Aggregate statistics, price terms. No account synthesis."""

    total_trades: int
    wins: int
    losses: int
    breakeven: int
    win_rate: float
    net_pnl: float
    gross_profit: float
    gross_loss: float
    profit_factor: float | None
    expectancy: float
    avg_win: float
    avg_loss: float
    max_drawdown: float
    avg_duration_seconds: float


@dataclass
class DataQuality:
    """Data quality information (ARCHITECTURE.md Section 4)."""

    data_source: str
    tick_count: int | None = None
    candle_count: int | None = None
    validation: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)


@dataclass
class ExecutionInfo:
    """Execution information: what ran, when, and signal accounting."""

    engine: str = "src.backtest"
    started_at: str | None = None
    finished_at: str | None = None
    signals_received: int | None = None
    signals_executed: int | None = None
    signals_ignored: int | None = None
    #: Who produced the trades: ``engine`` (default, every archive before this
    #: field existed) or ``strategy`` (strategy-owned execution).
    execution_model: str = "engine"
    #: Observed maximum number of simultaneously open positions. A measurement
    #: of what the run did, never a configured limit (legacy runs: None).
    max_concurrent_positions: int | None = None
    #: Candle model only: trades whose stop and target were both touched in the
    #: same bar, so the exit order is unknown (None for tick/strategy runs).
    ambiguous_exits: int | None = None


@dataclass
class BacktestResult:
    """The full structured result of one backtest run."""

    contract_version: str
    run_id: str
    created_at: str
    strategy: StrategyInfo
    config: RunConfig
    trades: list
    equity: list
    metrics: Metrics
    data_quality: DataQuality
    execution: ExecutionInfo
    #: Optional account/economics report (see ``src.accounting``). ``None`` for
    #: price-term-only runs and for every archive written before this field
    #: existed. Never affects the authoritative price-term fields above.
    account: dict | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_dict(cls, data: dict) -> "BacktestResult":
        return cls(
            contract_version=data["contract_version"],
            run_id=data["run_id"],
            created_at=data["created_at"],
            strategy=StrategyInfo(**data["strategy"]),
            config=RunConfig(**data["config"]),
            trades=[_trade_from_dict(t) for t in data["trades"]],
            equity=[EquityPoint(**e) for e in data["equity"]],
            metrics=Metrics(**data["metrics"]),
            data_quality=DataQuality(**data["data_quality"]),
            execution=_execution_from_dict(data["execution"]),
            account=data.get("account"),
        )

    @classmethod
    def from_json(cls, payload: str) -> "BacktestResult":
        return cls.from_dict(json.loads(payload))

    def save(self, path: str | Path) -> Path:
        target = Path(path)
        target.write_text(self.to_json(), encoding="utf-8")
        return target

    @classmethod
    def load(cls, path: str | Path) -> "BacktestResult":
        return cls.from_json(Path(path).read_text(encoding="utf-8"))

    def validate(self) -> list[str]:
        """Return a list of contract violations (empty = valid)."""
        errors: list[str] = []
        if not self.run_id:
            errors.append("run_id is required")
        if not self.strategy.name:
            errors.append("strategy.name is required")
        if not self.strategy.version:
            errors.append("strategy.version is required")
        if not self.config.symbol:
            errors.append("config.symbol is required")
        if not self.config.timeframe:
            errors.append("config.timeframe is required")
        if not isinstance(self.config.parameters, dict):
            errors.append("config.parameters must be a mapping")
        for t in self.trades:
            if t.direction not in ("LONG", "SHORT"):
                errors.append(f"trade {t.trade_id}: invalid direction {t.direction!r}")
            if t.exit_reason not in EXIT_REASONS:
                errors.append(f"trade {t.trade_id}: invalid exit_reason {t.exit_reason!r}")
        if len(self.equity) < len(self.trades):
            errors.append("equity must hold at least one point per trade")
        if self.execution.execution_model not in EXECUTION_MODELS:
            errors.append(
                f"invalid execution_model {self.execution.execution_model!r}"
            )
        return errors


def _trade_from_dict(data: dict) -> TradeRecord:
    """Tolerate trade dicts from before ``exit_reason_detail`` existed."""
    payload = dict(data)
    payload.setdefault("exit_reason_detail", None)
    # Ignore forward-compat extras so newer archives stay loadable.
    known = {f.name for f in fields(TradeRecord)}
    return TradeRecord(**{k: v for k, v in payload.items() if k in known})


def _execution_from_dict(data: dict) -> ExecutionInfo:
    """Tolerate execution dicts from before ``execution_model`` existed."""
    payload = dict(data)
    if "execution_model" not in payload or payload["execution_model"] is None:
        payload["execution_model"] = "engine"
    return ExecutionInfo(**payload)


def canonicalize_strategy_exit_reason(raw: object) -> tuple[str, str | None]:
    """Map a strategy-owned reason to the closed enum.

    Returns ``(canonical, detail)`` where ``detail`` keeps the raw text when
    the reason is not one of the engine-level tokens.
    """
    text = "" if raw is None else str(raw).strip()
    upper = text.upper()
    if upper in ("TP", "SL", "TSL", "BE", "TIME", "EOD"):
        return upper, None
    if upper in END_OF_DATA_REASONS:
        return "EOD", None
    mapped = STRATEGY_EXIT_REASON_MAP.get(upper)
    if mapped is not None:
        return mapped, None if mapped in ("TP", "SL", "EOD") else text or None
    if text:
        return "STRATEGY", text
    raise ValueError("exit_reason must be a non-empty string")


def compute_metrics(trades: list[TradeRecord]) -> Metrics:
    """Aggregate price-term metrics from contract trade records."""
    pnls = [t.pnl_price for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gross_profit = sum(wins)
    gross_loss = sum(losses)
    net = gross_profit + gross_loss
    peak = 0.0
    running = 0.0
    max_dd = 0.0
    for p in pnls:
        running += p
        peak = max(peak, running)
        max_dd = max(max_dd, peak - running)
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    return Metrics(
        total_trades=len(trades),
        wins=len(wins),
        losses=len(losses),
        breakeven=len(trades) - len(wins) - len(losses),
        win_rate=(len(wins) / len(trades) * 100.0) if trades else 0.0,
        net_pnl=net,
        gross_profit=gross_profit,
        gross_loss=gross_loss,
        profit_factor=(gross_profit / abs(gross_loss)) if gross_loss else None,
        expectancy=(net / len(trades)) if trades else 0.0,
        avg_win=(sum(wins) / len(wins)) if wins else 0.0,
        avg_loss=avg_loss,
        max_drawdown=max_dd,
        avg_duration_seconds=(sum(t.duration_seconds for t in trades) / len(trades)) if trades else 0.0,
    )


def equity_from_trades(trades: list[TradeRecord]) -> list[EquityPoint]:
    """Cumulative price-term PnL, one point per trade exit, ordered by exit."""
    ordered = sorted(trades, key=lambda t: (t.exit_time, t.trade_id))
    running = 0.0
    points = []
    for t in ordered:
        running += t.pnl_price
        points.append(EquityPoint(timestamp=t.exit_time, cumulative_pnl=running))
    return points


def _iso(value) -> str:
    text = str(value)
    return text.replace("+00:00", "Z") if text.endswith("+00:00") else text


def _positive_multiplier(value) -> float:
    """Per-trade size multiplier: missing/null/invalid stays 1.0, never zero."""
    if value is None:
        return 1.0
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 1.0
    return number if number == number and number > 0 else 1.0


def _optional_float(value) -> float | None:
    """Optional numeric engine column: missing/NaN stays None, never zero."""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if number != number else number  # NaN check


def trades_from_engine(trades_df) -> list[TradeRecord]:
    """Adapt the engine's trade DataFrame to contract records.

    The engine module is imported lazily so this contract stays
    dependency-free; pandas is only needed when this helper runs.
    """
    records = []
    for _, row in trades_df.iterrows():
        reason = ENGINE_EXIT_REASON_MAP.get(row["exit_reason"], row["exit_reason"])
        if reason not in EXIT_REASONS:
            raise ValueError(
                f"engine exit_reason {row['exit_reason']!r} is not in the closed enum"
            )
        records.append(
            TradeRecord(
                trade_id=int(row["trade_id"]),
                direction=str(row["direction"]),
                signal_candle_start=_iso(row["signal_candle_start"]),
                signal_available_time=_iso(row["signal_available_time"]),
                entry_time=_iso(row["entry_time"]),
                entry_price=float(row["entry_price"]),
                stop_loss=_optional_float(row.get("stop_loss")),
                take_profit=_optional_float(row.get("take_profit")),
                exit_time=_iso(row["exit_time"]),
                exit_price=float(row["exit_price"]),
                exit_reason=reason,
                pnl_price=float(row["pnl_price"]),
                duration_seconds=float(row["duration_seconds"]),
                spread_at_entry=float(row["spread_at_entry"]),
                spread_at_exit=float(row["spread_at_exit"]),
                mae=_optional_float(row.get("mae")),
                mfe=_optional_float(row.get("mfe")),
                size_multiplier=_positive_multiplier(row.get("size_multiplier")),
            )
        )
    return records
