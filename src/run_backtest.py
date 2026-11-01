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
import shutil
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
    parser.add_argument("--symbol", default=None,
                        help="instrument symbol; inferred from the monthly filename when omitted")
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
    parser.add_argument("--stop-distance", type=float, default=None,
                        help="execution stop distance in price terms (omit = no stop)")
    parser.add_argument("--take-profit-distance", type=float, default=None,
                        help="execution take-profit distance in price terms (omit = no target)")
    parser.add_argument("--no-report", action="store_true",
                        help="skip writing report.html (default: report is generated)")
    parser.add_argument("--publish", action="store_true",
                        help="after archiving + report, publish the run to the frontend public artifacts")
    parser.add_argument("--public", default=publish.DEFAULT_PUBLIC,
                        help="frontend public directory used by --publish")
    args = parser.parse_args(argv)
    if args.publish and args.no_report:
        parser.error("--publish requires report generation; do not combine with --no-report")
    if args.stop_distance is not None and args.stop_distance <= 0:
        parser.error("--stop-distance must be > 0 when provided")
    if args.take_profit_distance is not None and args.take_profit_distance <= 0:
        parser.error("--take-profit-distance must be > 0 when provided")
    if not args.symbol:
        from src import data_engine
        meta = data_engine.parse_source_filename(Path(args.data).name)
        if not meta or not meta.get("symbol"):
            parser.error(
                "--symbol is required when the filename is not "
                "<symbol>_<provider>_<YYYY>_<MM>_ticks.csv"
            )
        args.symbol = meta["symbol"]
    return args


def _load_ticks(data_path: Path):
    """Load ticks from CSV. Accepts the engine's ``timestamp,bid,ask`` shape
    or headerless Dukascopy exports (``date,time,bid,ask,...``)."""
    import pandas as pd
    from src import data_engine

    source_meta = data_engine.parse_source_filename(Path(data_path).name)
    source_tz = data_engine.source_timezone_for(
        Path(data_path).name, source_meta.get("provider") if source_meta else None
    )
    probe = pd.read_csv(data_path, nrows=1)
    probe.columns = [str(c).strip().lower() for c in probe.columns]
    columns = set(probe.columns)
    if {"timestamp", "bid", "ask"} <= columns:
        frame = pd.read_csv(data_path)
        frame.columns = [str(c).strip().lower() for c in frame.columns]
        frame["timestamp"] = data_engine.parse_timestamps(
            frame["timestamp"], timezone_name=source_tz
        )
        frame["bid"] = pd.to_numeric(frame["bid"], errors="coerce")
        frame["ask"] = pd.to_numeric(frame["ask"], errors="coerce")
        return frame
    if {"open", "high", "low", "close"} <= columns:
        raise ValueError(
            "OHLCV candles are not supported; provide tick timestamp,bid,ask"
        )
    if "price" in columns:
        raise ValueError(
            "trade-only price/volume data is not supported; provide bid and ask"
        )
    raw = pd.read_csv(data_path, header=None)
    if raw.shape[1] < 4:
        raise ValueError(f"tick file has {raw.shape[1]} columns; expected timestamp,bid,ask or Dukascopy date,time,bid,ask")
    stamps = data_engine._timestamp_from_strings(
        raw[0], raw[1], timezone_name=source_tz
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


def _empty_external_signals():
    """Empty signal frame for strategy-owned runs (signal accounting stays valid)."""
    import pandas as pd

    return pd.DataFrame(columns=["signal_candle_start", "signal_available_time", "direction"])


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

    data_source = str(request.market.data_source)
    data_path = Path(data_source)
    cache_run = data_source.startswith("cache:")
    try:
        timeframe = request.market.timeframe
        if timeframe not in run_request.SUPPORTED_TIMEFRAME_VALUES:
            raise run_errors.UnsupportedTimeframeError(
                "unsupported timeframe; supported: "
                + ", ".join(run_request.SUPPORTED_TIMEFRAME_VALUES)
            )
        if cache_run:
            # Canonical Parquet ticks are the execution source (Data Engine).
            from src import data_engine
            ticks = data_engine.load_ticks_frame(
                data_source, symbol=request.market.symbol,
                start=request.market.start, end=request.market.end,
                # Execution only needs timestamp + BID/ASK (the canonical store
                # keeps volumes/source_row; the hot frame does not carry them).
                columns=["timestamp", "bid", "ask"],
            )
        elif data_path.is_file():
            ticks = _load_ticks(data_path)
        else:
            raise run_errors.InvalidDatasetError(f"dataset not found: {data_path}")
        if cache_run:
            # The Data Engine reader already returns canonical ticks: UTC tz-aware,
            # filtered to the requested range, and chronologically ordered. Avoid
            # redundant full-frame copies (tz convert / range filter / sort).
            # The sorted-frame contract is recorded so downstream stages can
            # skip their own defensive re-sort without changing any logic.
            ticks.attrs["qbl_sorted"] = "timestamp"
            if getattr(ticks["timestamp"].dt, "tz", None) is None:
                ticks["timestamp"] = ticks["timestamp"].dt.tz_localize("UTC")
        else:
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
                f"no ticks in {data_source} for the requested range"
            )
        if cache_run:
            # Strategy candles come from the derived candle cache (never raw CSV,
            # never on-the-fly) for logical-dataset runs.
            candles = data_engine.load_candles_frame(
                data_source, timeframe=timeframe, symbol=request.market.symbol,
                start=request.market.start, end=request.market.end,
            )
        else:
            candles = ticks.set_index("timestamp")["bid"].resample(timeframe).ohlc().dropna()
    except run_errors.RunError:
        raise
    except Exception as exc:
        if cache_run:
            from src import data_engine
            if isinstance(exc, data_engine.DataEngineError):
                raise run_errors.DataNotPreparedError(
                    "Derived market data has not been prepared for "
                    f"{request.market.dataset_id or request.market.symbol} "
                    f"{request.market.timeframe}. Run the data preparation command."
                ) from exc
        raise run_errors.InvalidDatasetError(f"failed to load dataset {data_path}: {exc}") from exc

    # Read-only validation before the engine. It never mutates ticks, candles
    # or the raw source; gap/duplicate/order findings are warnings, while
    # non-finite/crossed/invalid timestamps stop the run.
    # Imported at call time so the async worker can wrap it with an honest
    # "Validating ticks" stage event (no silent gap after Candles loaded).
    # Cached-partition path: per-month reports are reused by fingerprint, so
    # repeat runs over unchanged data validate in seconds. Raw-CSV runs keep
    # the full direct validation (no cache exists for them).
    if cache_run:
        from src import data_engine as _de_mod

        tick_report = _de_mod.validate_range(
            data_source, symbol=request.market.symbol,
            start=request.market.start, end=request.market.end, frame=ticks,
        )
    else:
        from src import tick_validation as _tick_validation_mod

        tick_report = _tick_validation_mod.validate_ticks(ticks)
    if tick_report.fatal:
        raise run_errors.InvalidDatasetError("; ".join(tick_report.errors))
    data_warnings = list(tick_report.warnings)
    data_validation = dict(tick_report.validation)

    resolved_instrument = accounting.resolve_instrument(
        request.market.symbol, request.instrument
    )

    # Hand the strategy its effective parameters, then generate normalized
    # signals. Strategy parameters influence signals only; the market
    # timeframe is forwarded so candle-close timing matches the resampled
    # candles (no hardcoded bar size inside strategies).
    strategy_kind = getattr(strategy, "kind", "signal")
    if strategy_kind == "external":
        # Strategy-owned execution: the file produces the trades itself from
        # Quant-owned data. One branch only; everything after trades_df is the
        # shared pipeline (records -> metrics -> equity -> result -> archive).
        from src import external_strategy as _external

        strategy.configure(effective_parameters)
        started = utc_now_iso()
        try:
            trades_df, _external_output = _external.run_external(
                strategy, candles,
                parameters=effective_parameters,
                instrument=resolved_instrument,
                account=request.account,
                symbol=request.market.symbol,
                timeframe=request.market.timeframe,
                range_start=request.market.start,
                range_end=request.market.end,
                ticks=ticks,
            )
        except run_errors.RunError:
            raise
        except Exception as exc:  # noqa: BLE001 - strategy failures surface loudly
            raise run_errors.BacktestFailedError(f"external strategy failed: {exc}") from exc
        finished = utc_now_iso()
        records = trades_from_engine(trades_df)
        for record, (_, row) in zip(records, trades_df.iterrows()):
            detail = row.get("_exit_reason_detail")
            if record.exit_reason == "STRATEGY" and detail:
                record.exit_reason_detail = str(detail)
        signals = _empty_external_signals()
    else:
        strategy.configure(effective_parameters)
        signals = strategy_loader.generate_validated_signals(
            strategy, candles, timeframe=request.market.timeframe
        )
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

    # The user's requested window is preserved separately from the observed
    # tick coverage above, so the UI can show the requested range without
    # confusing it with the actual first/last tick.
    def _requested_iso(value: str) -> str:
        import pandas as pd  # local import: keep the hot path dependency-free
        try:
            ts = pd.Timestamp(value)
        except Exception:
            return str(value or "")
        return (ts if ts.tz is not None else ts.tz_localize("UTC")).tz_convert("UTC").isoformat().replace("+00:00", "Z")

    requested_start = _requested_iso(request.market.start)
    requested_end = _requested_iso(request.market.end)

    # Archived data source: a friendly logical id for Data Engine runs (never the
    # internal `cache:` resolver key), else the raw path (legacy runs).
    archive_source = request.market.dataset_id if cache_run else str(data_path)
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
            data_source=archive_source,
            data_fingerprint=_data_fingerprint(request.market),
            requested_start=requested_start,
            requested_end=requested_end,
            instrument=resolved_instrument.to_dict() if resolved_instrument is not None else None,
        ),
        trades=records,
        equity=equity,
        metrics=metrics,
        data_quality=DataQuality(
            data_source=archive_source,
            tick_count=len(ticks),
            candle_count=len(candles),
            validation={
                "signals_valid": True,
                **data_validation,
            },
            warnings=data_warnings,
        ),
        execution=ExecutionInfo(
            started_at=started,
            finished_at=finished,
            signals_received=len(signals),
            signals_executed=len(records),
            signals_ignored=max(0, len(signals) - len(records)),
            execution_model="strategy" if strategy_kind == "external" else "engine",
        ),
    )

    # Optional account/economics layer. Strictly post-processing of the
    # authoritative price-term result: it never touches execution prices or the
    # price-term metrics. No account model -> account stays None (unchanged).
    if request.account is not None:
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
    if cache_run:
        from src import data_engine
        config["market"]["data_source"] = archive_source
        config["data_provenance"] = data_engine.data_provenance(
            data_source, symbol=request.market.symbol, timeframe=request.market.timeframe,
            start=request.market.start, end=request.market.end,
        )
    else:
        from src import data_engine
        source_meta = data_engine.parse_source_filename(data_path.name)
        config["market"]["source_timezone"] = data_engine.source_timezone_for(
            data_path.name, source_meta.get("provider") if source_meta else None
        )
    strategy_source = None
    if strategy_path is not None and strategy_path.is_file():
        # Archive the actual strategy file for a generic (path-loaded) strategy.
        strategy_source = strategy_path.read_text(encoding="utf-8")
    else:
        try:
            if strategy_kind == "external":
                strategy_source = inspect.getsource(strategy.run_strategy)
            else:
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
            # An archive without its promised report is not a completed run.
            shutil.rmtree(directory, ignore_errors=True)
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
