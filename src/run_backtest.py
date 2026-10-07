"""Strategy Runner — Strategy -> Engine -> BacktestResult -> runs/<run_id>/.

Reference: ``ARCHITECTURE.md`` Core Architecture flow.

This is the single entry point that wires the approved layers together
without changing any of them:

1. Strategy (``src/strategies``) produces normalized signals.
2. Engine (``src.backtest.run_backtest``) executes signals into trades.
3. Result contract (``src.result_contract``) structures the outcome.
4. Run store (``src.run_store``) archives the run on the filesystem.

Verified research files (``src/backtest.py``, ``src/strategies/*``,
``notebooks/*``, ``data/*``) are imported and read, never modified.

Usage::

    # built-in strategy, archived locally
    python -m src.run_backtest --strategy candle_direction --symbol XAUUSD \\
        --timeframe 15min --data data/XAUUSD_DUKAS_2025_01_ticks.csv \\
        --start 2025-01-02 --end 2025-01-02 --out runs \\
        --stop-distance 5 --take-profit-distance 10

    # any trusted local strategy file: --param overrides its declared
    # parameters, then the run is archived + published to the frontend
    python -m src.run_backtest --strategy path/to/strategy.py --data ticks.csv \\
        --start 2025-01-02 --end 2025-01-02 --out runs \\
        --param fast_period=20 --param slow_period=50 --publish

``--param`` sets strategy parameters only; ``stop_distance`` and
``take_profit_distance`` are execution settings and use their own flags.
"""

from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import backtest as engine  # noqa: E402
from src import accounting  # noqa: E402
from src import publish_run as publish  # noqa: E402
from src import run_errors  # noqa: E402
from src import run_request  # noqa: E402
from src import run_store  # noqa: E402
from src import strategy_loader  # noqa: E402
from src.result_contract import (  # noqa: E402
    CONTRACT_VERSION,
    BacktestResult,
    DataQuality,
    ExecutionInfo,
    RunConfig,
    StrategyInfo,
    compute_metrics,
    equity_from_trades,
    trades_from_engine,
    utc_now_iso,
)
from src.strategies import base as strategy_base  # noqa: E402
from src.strategies.candle_direction import generate_signals as candle_direction_signals  # noqa: E402

# Built-in strategy aliases. These reuse the existing adapter so verified
# strategies keep their identity. Any other --strategy value is treated as a
# path to a trusted local strategy file and loaded by src.strategy_loader.
# Values are factories so each run gets a fresh instance (no shared state).
# candle_direction is fixed logic, so it declares no configurable parameters;
# timeframe is market configuration (--timeframe), not a strategy parameter.
BUILTIN_STRATEGIES = {
    "candle_direction": lambda: strategy_base.adapt_strategy(
        candle_direction_signals,
        name="Candle Direction",
        version="1.0",
        description="Close > Open -> LONG, Close < Open -> SHORT. Signal tradable after candle close.",
        params={},
    ),
}


def resolve_strategy(selector: str):
    """Return ``(strategy, source_path_or_None)`` for a built-in name or file."""
    if selector in BUILTIN_STRATEGIES:
        return BUILTIN_STRATEGIES[selector](), None
    return strategy_loader.load_strategy(selector), Path(selector)


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Run a strategy backtest and archive the result.")
    parser.add_argument(
        "--strategy",
        default="candle_direction",
        help="Built-in strategy name (candle_direction) or path to a strategy .py file",
    )
    parser.add_argument("--symbol", default="XAUUSD")
    parser.add_argument("--timeframe", default="15min")
    parser.add_argument("--data", required=True, help="Tick CSV with timestamp,bid,ask columns")
    parser.add_argument("--start", default=None, help="UTC start, e.g. 2025-01-01")
    parser.add_argument("--end", default=None, help="UTC end, e.g. 2025-01-31")
    parser.add_argument("--out", default="runs")
    parser.add_argument("--run-id", default=None)
    parser.add_argument(
        "--param",
        action="append",
        default=[],
        help="strategy parameter override key=value (repeatable; must be declared by the strategy)",
    )
    parser.add_argument("--stop-distance", type=float, default=5.0,
                        help="execution stop distance in price terms")
    parser.add_argument("--take-profit-distance", type=float, default=10.0,
                        help="execution take-profit distance in price terms")
    parser.add_argument("--no-report", action="store_true",
                        help="skip writing report.html (default: report is generated)")
    parser.add_argument("--publish", action="store_true",
                        help="after archiving + report, publish the run to the frontend public artifacts")
    parser.add_argument("--public", default=publish.DEFAULT_PUBLIC,
                        help="frontend public directory used by --publish")
    args = parser.parse_args(argv)
    if args.publish and args.no_report:
        parser.error("--publish requires report generation; do not combine with --no-report")
    return args


def _load_ticks(data_path: Path):
    """Load ticks from CSV. Accepts the engine's ``timestamp,bid,ask`` shape
    or headerless Dukascopy exports (``date,time,bid,ask,...``)."""
    import pandas as pd

    probe = pd.read_csv(data_path, nrows=1)
    if {"timestamp", "bid", "ask"} <= set(probe.columns):
        return pd.read_csv(data_path, parse_dates=["timestamp"])
    raw = pd.read_csv(data_path, header=None)
    if raw.shape[1] < 4:
        raise ValueError(f"tick file has {raw.shape[1]} columns; expected timestamp,bid,ask or Dukascopy date,time,bid,ask")
    stamps = pd.to_datetime(
        raw[0].astype(str) + " " + raw[1].astype(str), format="%Y.%m.%d %H:%M:%S.%f", utc=True
    )
    return pd.DataFrame({"timestamp": stamps, "bid": raw[2].astype(float), "ask": raw[3].astype(float)})


def _fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _data_fingerprint(market) -> str:
    """Provenance fingerprint: raw CSV hash, or the derived cache fingerprint."""
    if str(market.data_source).startswith("cache:"):
        from src import data_engine
        return data_engine.cache_fingerprint(
            market.data_source, symbol=market.symbol, start=market.start, end=market.end)
    return _fingerprint(Path(market.data_source))


def execute_run(
    request: run_request.BacktestRunRequest,
    *,
    out: str = "runs",
    report: bool = True,
    public: str = publish.DEFAULT_PUBLIC,
    run_id: str | None = None,
    resolved=None,
) -> dict:
    """Execute one ``BacktestRunRequest`` end-to-end and archive it.

    This is the single reusable run workflow shared by the CLI and the local
    HTTP bridge: resolve strategy -> validate parameters -> run the engine ->
    validate the result -> archive -> report -> optional publish. It is
    orchestration only and adds no strategy, execution or metric logic.

    ``resolved`` optionally passes an already-resolved ``(strategy, path)`` so
    the CLI does not load the strategy module twice. Raises ``RunError``
    subclasses for expected failures.
    """
    if resolved is None:
        resolved = resolve_strategy(request.strategy.selector)
    strategy, strategy_path = resolved

    declared = strategy.parameters()
    overrides = run_request.normalize_parameter_overrides(request.strategy.parameters, declared)
    effective_parameters = strategy_base.resolve_parameters(declared, overrides)
    # Record the normalized inputs on the request so the archive config and the
    # strategy file reference are exact.
    request.strategy.parameters = overrides
    request.strategy.file = str(strategy_path) if strategy_path is not None else None

    import pandas as pd  # local import: contract/store modules stay dependency-free

    data_path = Path(request.market.data_source)
    try:
        if str(request.market.data_source).startswith("cache:"):
            # Derived Parquet tick store (Data Engine). The engine still receives
            # the same canonical tick frame; src/backtest.py is untouched.
            from src import data_engine
            ticks = data_engine.load_ticks_frame(
                request.market.data_source,
                symbol=request.market.symbol,
                start=request.market.start,
                end=request.market.end,
            )
        elif data_path.is_file():
            ticks = _load_ticks(data_path)
        else:
            raise run_errors.InvalidDatasetError(f"dataset not found: {data_path}")
        if getattr(ticks["timestamp"].dt, "tz", None) is None:
            ticks["timestamp"] = ticks["timestamp"].dt.tz_localize("UTC")
        else:
            ticks["timestamp"] = ticks["timestamp"].dt.tz_convert("UTC")
        if request.market.start:
            ticks = ticks[ticks["timestamp"] >= pd.Timestamp(request.market.start, tz="UTC")]
        if request.market.end:
            ticks = ticks[ticks["timestamp"] < pd.Timestamp(request.market.end, tz="UTC") + pd.Timedelta(days=1)]
        ticks = ticks.sort_values("timestamp", kind="stable").reset_index(drop=True)
        if ticks.empty:
            raise run_errors.InvalidDatasetError(
                f"no ticks in {data_path} for the requested range"
            )
        timeframe = request.market.timeframe
        if timeframe not in run_request.SUPPORTED_TIMEFRAME_VALUES:
            raise run_errors.UnsupportedTimeframeError(
                "unsupported timeframe; supported: "
                + ", ".join(run_request.SUPPORTED_TIMEFRAME_VALUES)
            )
        candles = ticks.set_index("timestamp")["bid"].resample(timeframe).ohlc().dropna()
    except run_errors.RunError:
        raise
    except Exception as exc:
        raise run_errors.InvalidDatasetError(f"failed to load dataset {data_path}: {exc}") from exc

    # Hand the strategy its effective parameters, then generate normalized
    # signals. Strategy parameters influence signals only.
    strategy.configure(effective_parameters)
    signals = strategy_loader.generate_validated_signals(strategy, candles)

    started = utc_now_iso()
    # The engine receives only normalized signals plus execution settings.
    try:
        trades_df = engine.run_backtest(
            ticks,
            signals,
            request.execution.stop_distance,
            request.execution.take_profit_distance,
        )
    except run_errors.RunError:
        raise
    except Exception as exc:
        raise run_errors.BacktestFailedError(f"backtest failed: {exc}") from exc
    finished = utc_now_iso()

    records = trades_from_engine(trades_df)
    metrics = compute_metrics(records)
    equity = equity_from_trades(records)
    meta = strategy.metadata()

    start_iso = ticks["timestamp"].iloc[0].isoformat().replace("+00:00", "Z")
    end_iso = ticks["timestamp"].iloc[-1].isoformat().replace("+00:00", "Z")

    result = BacktestResult(
        contract_version=CONTRACT_VERSION,
        run_id=run_id or f"run_{uuid.uuid4().hex[:8]}",
        created_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        strategy=StrategyInfo(name=meta["name"], version=meta["version"], description=meta.get("description", "")),
        config=RunConfig(
            symbol=request.market.symbol,
            timeframe=str(timeframe),
            start=start_iso,
            end=end_iso,
            parameters=effective_parameters,
            data_source=str(data_path),
            data_fingerprint=_data_fingerprint(request.market),
        ),
        trades=records,
        equity=equity,
        metrics=metrics,
        data_quality=DataQuality(
            data_source=str(data_path),
            tick_count=len(ticks),
            candle_count=len(candles),
            validation={"signals_valid": True},
        ),
        execution=ExecutionInfo(
            started_at=started,
            finished_at=finished,
            signals_received=len(signals),
            signals_executed=len(records),
            signals_ignored=max(0, len(signals) - len(records)),
        ),
    )

    # Optional account/economics layer. Strictly post-processing of the
    # authoritative price-term result: it never touches execution prices or the
    # price-term metrics. No account model -> account stays None (unchanged).
    resolved_instrument = None
    if request.account is not None:
        resolved_instrument = accounting.resolve_instrument(request.market.symbol, request.instrument)
        if resolved_instrument is None:
            result.account = accounting.unavailable_report(
                f"no instrument economics configured for symbol {request.market.symbol!r}; "
                "provide an explicit instrument spec",
                request.account,
                request.instrument,
            )
        else:
            result.account = accounting.build_account_report(
                records, request.account, resolved_instrument
            )

    errors = result.validate()
    if errors:
        raise run_errors.ResultValidationError(f"result contract invalid: {errors}")

    config = run_request.build_run_config(
        request,
        run_id=result.run_id,
        metadata=meta,
        effective_parameters=effective_parameters,
        data_fingerprint=result.config.data_fingerprint,
        start=result.config.start,
        end=result.config.end,
        instrument=resolved_instrument,
    )
    strategy_source = None
    if strategy_path is not None and strategy_path.is_file():
        # Archive the actual strategy file for a generic (path-loaded) strategy.
        strategy_source = strategy_path.read_text(encoding="utf-8")
    else:
        try:
            strategy_source = inspect.getsource(strategy.generate_signals)
        except (OSError, TypeError):
            strategy_source = None

    try:
        directory = run_store.save_run(
            out,
            result.run_id,
            config=config,
            result=result.to_dict(),
            trades=[r.__dict__ for r in records],
            equity=[e.__dict__ for e in equity],
            strategy_source=strategy_source,
        )
    except Exception as exc:
        raise run_errors.ArchiveFailedError(
            f"failed to archive run {result.run_id}: {exc}"
        ) from exc

    report_path = None
    if report:
        # Presentation-only artifact: rendered from the validated result,
        # never recalculated. Keeps runs/<run_id>/ self-contained.
        try:
            from src.report import generate_report

            report_path = generate_report(directory, overwrite=True)
        except Exception as exc:
            raise run_errors.ReportFailedError(f"report generation failed: {exc}") from exc

    publish_info = None
    if request.publish:
        # Only reached after the backtest, contract validation, archive write
        # and report generation all succeeded. Reuses src.publish_run.
        try:
            publish_info = publish.publish_run(result.run_id, runs=out, public=public)
        except Exception as exc:
            raise run_errors.PublishFailedError(f"publish failed: {exc}") from exc

    return {
        "run_id": result.run_id,
        "status": "completed",
        "directory": str(directory),
        "archive": str(directory / "result.json"),
        "report": str(report_path) if report_path is not None else None,
        "published": publish_info is not None,
        "published_file": publish_info.get("published") if publish_info else None,
        "manifest": publish_info.get("manifest") if publish_info else None,
        "publish_info": publish_info,
        "strategy": {
            "name": meta["name"],
            "version": meta["version"],
            "description": meta.get("description", ""),
        },
        "parameters": dict(effective_parameters),
        "market": {
            "symbol": request.market.symbol,
            "timeframe": str(timeframe),
            "start": result.config.start,
            "end": result.config.end,
        },
        "execution": {
            "stop_distance": request.execution.stop_distance,
            "take_profit_distance": request.execution.take_profit_distance,
        },
        "summary": {
            "total_trades": metrics.total_trades,
            "win_rate": metrics.win_rate,
            "net_pnl": metrics.net_pnl,
            "signals_received": result.execution.signals_received,
        },
        # The full contract payload is available to callers; the HTTP bridge
        # returns a compact reference instead of embedding it.
        "result": result,
    }


def main(argv=None) -> str:
    args = _parse_args(argv)

    # Resolve and validate the strategy, then parse/validate parameter overrides
    # before touching data or the engine. Invalid strategies, unknown parameter
    # names and malformed values are all rejected up front.
    strategy, strategy_path = resolve_strategy(args.strategy)
    declared = strategy.parameters()
    overrides = run_request.parse_parameter_overrides(args.param, declared)

    # The complete, reproducible request: strategy + market + execution.
    request = run_request.BacktestRunRequest(
        strategy=run_request.StrategyRequest(
            selector=args.strategy,
            parameters=overrides,
            file=str(strategy_path) if strategy_path is not None else None,
        ),
        market=run_request.MarketRequest(
            symbol=args.symbol,
            timeframe=args.timeframe,
            start=args.start,
            end=args.end,
            data_source=str(Path(args.data)),
        ),
        execution=run_request.ExecutionRequest(
            stop_distance=args.stop_distance,
            take_profit_distance=args.take_profit_distance,
        ),
        publish=args.publish,
    )

    info = execute_run(
        request,
        out=args.out,
        report=not args.no_report,
        public=args.public,
        run_id=args.run_id,
        resolved=(strategy, strategy_path),
    )
    if request.publish and info.get("publish_info") is not None:
        print(json.dumps(info["publish_info"], ensure_ascii=True))
    print(info["directory"])
    return info["directory"]


if __name__ == "__main__":
    main()
