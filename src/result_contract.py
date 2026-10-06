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
- MAE/MFE are optional: the current engine does not produce excursions,
  so a result built from engine output carries ``mae=None, mfe=None``
  rather than invented values.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path


CONTRACT_VERSION = "1.0"

EXIT_REASONS = ("TP", "SL", "EOD")

# Engine emits "END_OF_DATA" for a position closed at the end of the data.
# The contract canonicalizes it to "EOD" so engine output, stored runs and
# the UI vocabulary stay identical. The engine itself is not modified.
ENGINE_EXIT_REASON_MAP = {"TP": "TP", "SL": "SL", "END_OF_DATA": "EOD"}


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
    start: str  # ISO-8601 UTC, inclusive
    end: str  # ISO-8601 UTC, inclusive
    parameters: dict
    data_source: str = ""
    data_fingerprint: str = ""


@dataclass
class TradeRecord:
    """One closed trade. Prices/PnL are price terms; MAE/MFE optional."""

    trade_id: int
    direction: str  # LONG | SHORT
    signal_candle_start: str
    signal_available_time: str
    entry_time: str
    entry_price: float
    stop_loss: float
    take_profit: float
    exit_time: str
    exit_price: float
    exit_reason: str  # TP | SL | EOD
    pnl_price: float
    duration_seconds: float
    spread_at_entry: float
    spread_at_exit: float
    mae: float | None = None
    mfe: float | None = None


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
            trades=[TradeRecord(**t) for t in data["trades"]],
            equity=[EquityPoint(**e) for e in data["equity"]],
            metrics=Metrics(**data["metrics"]),
            data_quality=DataQuality(**data["data_quality"]),
            execution=ExecutionInfo(**data["execution"]),
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
        return errors


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


def trades_from_engine(trades_df) -> list[TradeRecord]:
    """Adapt the engine's trade DataFrame to contract records.

    The engine module is imported lazily so this contract stays
    dependency-free; pandas is only needed when this helper runs.
    """
    records = []
    for _, row in trades_df.iterrows():
        reason = ENGINE_EXIT_REASON_MAP.get(row["exit_reason"], row["exit_reason"])
        records.append(
            TradeRecord(
                trade_id=int(row["trade_id"]),
                direction=str(row["direction"]),
                signal_candle_start=_iso(row["signal_candle_start"]),
                signal_available_time=_iso(row["signal_available_time"]),
                entry_time=_iso(row["entry_time"]),
                entry_price=float(row["entry_price"]),
                stop_loss=float(row["stop_loss"]),
                take_profit=float(row["take_profit"]),
                exit_time=_iso(row["exit_time"]),
                exit_price=float(row["exit_price"]),
                exit_reason=reason,
                pnl_price=float(row["pnl_price"]),
                duration_seconds=float(row["duration_seconds"]),
                spread_at_entry=float(row["spread_at_entry"]),
                spread_at_exit=float(row["spread_at_exit"]),
            )
        )
    return records
