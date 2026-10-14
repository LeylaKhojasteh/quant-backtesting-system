"""Run-scoped real market context for visualization (v1.0.2).

The archived run is authoritative for its own market context: dataset/symbol/
timeframe/range come from the run archive (``config.json`` + ``result.json``),
never from the frontend and never from a demo fixture. Real candles/ticks are
then read from the canonical Data Engine cache through bounded window readers.

This module performs **no** research and **no** resampling: it only resolves the
run's provenance and projects real cached market data into structured JSON for
the product UI. Nothing here can change a backtest result.
"""

from __future__ import annotations

import pandas as pd

from src import data_engine, run_errors, run_library

#: Default bounded windows (candles/ticks returned per request).
DEFAULT_CANDLE_LIMIT = 800
DEFAULT_TICK_LIMIT = 400
DEFAULT_TICK_BEFORE_MS = 4000
DEFAULT_TICK_AFTER_MS = 4000


def resolve_run_market(runs_dir, run_id: str, public_dir) -> dict:
    """Market context for one archived run, from the archive itself.

    Raises ``RunNotFoundError`` when the run/result is missing. The returned
    ``source`` is the Data Engine source reference (``cache:<dataset_id>`` for
    logical runs, ``cache:`` + symbol for legacy CSV-path runs).
    """
    detail = run_library.run_detail(runs_dir, run_id, public_dir)
    market = detail.get("market") or {}
    symbol = market.get("symbol")
    timeframe = market.get("timeframe")
    if not symbol or not timeframe:
        raise run_errors.RunNotFoundError(
            f"run {run_id} has no archived market context (symbol/timeframe)")
    if timeframe not in data_engine.SUPPORTED_CANDLE_TIMEFRAMES:
        raise run_errors.DataNotPreparedError(
            f"run {run_id} timeframe {timeframe!r} is not a supported candle timeframe")
    dataset_id = market.get("dataset_id")
    source = f"cache:{dataset_id}" if dataset_id else "cache:"
    return {
        "run_id": run_id,
        "dataset_id": dataset_id,
        "symbol": symbol,
        "timeframe": timeframe,
        # Requested window is authoritative for display/windowing; actual tick
        # coverage is kept separately for reference.
        "start": market.get("requested_start") or market.get("start"),
        "end": market.get("requested_end") or market.get("end"),
        "observed_start": market.get("start"),
        "observed_end": market.get("end"),
        "source": source,
    }


def _window_bounds(market: dict, start: str | None, end: str | None,
                   *, limit: int, timeframe: str) -> tuple[str, str]:
    """Resolve an explicit ``[start, end)`` window for a bounded request.

    Defaults to the last ``limit`` timeframes ending at the run's end, so the
    first screen is real, bounded, and never the whole run.
    """
    run_start = str(market.get("start") or "")
    run_end = str(market.get("end") or "")
    if start or end:
        win_start = start or run_start
        win_end = end or run_end
        if not win_start or not win_end:
            raise run_errors.InvalidMarketRequestError("start and end are required for a windowed request")
        return str(win_start), str(win_end)
    if not run_end:
        raise run_errors.InvalidMarketRequestError("run has no archived end time to window from")
    span = pd.Timestamp(run_end) - pd.tseries.frequencies.to_offset(timeframe) * max(1, limit)
    win_start = max(pd.Timestamp(run_start), span) if run_start else span
    return str(win_start), str(run_end)


def run_candles(runs_dir, run_id: str, public_dir, *,
                start: str | None = None, end: str | None = None,
                limit: int = DEFAULT_CANDLE_LIMIT) -> dict:
    """Bounded real candle window for one archived run (OHLC, BID + ASK)."""
    market = resolve_run_market(runs_dir, run_id, public_dir)
    cap = max(1, min(int(limit), data_engine.MAX_CANDLE_WINDOW))
    win_start, win_end = _window_bounds(market, start, end, limit=cap, timeframe=market["timeframe"])
    try:
        frame = data_engine.load_candles_window(
            market["source"], timeframe=market["timeframe"], symbol=market["symbol"],
            start=win_start, end=win_end, limit=cap,
        )
    except data_engine.PartitionMissingError as exc:
        raise run_errors.DataNotPreparedError(str(exc)) from exc
    except data_engine.CoverageError as exc:
        raise run_errors.DataNotPreparedError(str(exc)) from exc
    candles = [
        {
            "timestamp": _iso(row.timestamp),
            "bid_open": float(row.open),
            "bid_high": float(row.high),
            "bid_low": float(row.low),
            "bid_close": float(row.close),
            "ask_open": float(row.ask_open),
            "ask_high": float(row.ask_high),
            "ask_low": float(row.ask_low),
            "ask_close": float(row.ask_close),
        }
        for row in frame.itertuples(index=False)
    ]
    return {
        "run_id": run_id,
        "dataset_id": market["dataset_id"],
        "symbol": market["symbol"],
        "timeframe": market["timeframe"],
        "start": candles[0]["timestamp"] if candles else win_start,
        "end": candles[-1]["timestamp"] if candles else win_end,
        "count": len(candles),
        "candles": candles,
    }


def run_ticks(runs_dir, run_id: str, public_dir, *,
              start: str | None = None, end: str | None = None,
              around: str | None = None,
              before_ms: int = DEFAULT_TICK_BEFORE_MS,
              after_ms: int = DEFAULT_TICK_AFTER_MS,
              limit: int = DEFAULT_TICK_LIMIT) -> dict:
    """Bounded real BID/ASK tick window for one archived run.

    Accepts either an explicit ``[start, end)`` window or ``around`` a fill
    timestamp (window = center ± before/after ms), always capped by ``limit``.
    """
    market = resolve_run_market(runs_dir, run_id, public_dir)
    cap = max(1, min(int(limit), data_engine.MAX_TICK_WINDOW))
    if around:
        center = pd.Timestamp(around)
        win_start = center - pd.Timedelta(milliseconds=max(1, int(before_ms)))
        win_end = center + pd.Timedelta(milliseconds=max(1, int(after_ms)))
        read_cap = min(data_engine.MAX_TICK_WINDOW, max(cap * 4, cap + 200))
        try:
            frame = data_engine.load_ticks_window(
                market["source"], symbol=market["symbol"], start=str(win_start),
                end=str(win_end), limit=read_cap,
            )
        except data_engine.PartitionMissingError as exc:
            raise run_errors.DataNotPreparedError(str(exc)) from exc
        except data_engine.CoverageError as exc:
            raise run_errors.DataNotPreparedError(str(exc)) from exc
        split = cap // 2
        before = frame[frame["timestamp"] < center]
        after = frame[frame["timestamp"] >= center]
        frame = pd.concat([before.tail(split), after.head(cap - split)], ignore_index=True)
        frame = frame.sort_values("timestamp", kind="stable").reset_index(drop=True)
    else:
        if not start and not end:
            raise run_errors.InvalidMarketRequestError(
                "provide either start/end or an around timestamp for ticks")
        win_start, win_end = _window_bounds(
            market, start, end, limit=cap,
            timeframe="1min" if not market.get("timeframe") else market["timeframe"])
        try:
            frame = data_engine.load_ticks_window(
                market["source"], symbol=market["symbol"], start=win_start,
                end=win_end, limit=cap,
            )
        except data_engine.PartitionMissingError as exc:
            raise run_errors.DataNotPreparedError(str(exc)) from exc
        except data_engine.CoverageError as exc:
            raise run_errors.DataNotPreparedError(str(exc)) from exc
    ticks = [
        {
            "timestamp": _iso(row.timestamp),
            "bid": float(row.bid),
            "ask": float(row.ask),
            "bid_volume": float(row.bid_volume),
            "ask_volume": float(row.ask_volume),
            "spread": float(row.ask) - float(row.bid),
        }
        for row in frame.itertuples(index=False)
    ]
    return {
        "run_id": run_id,
        "dataset_id": market["dataset_id"],
        "symbol": market["symbol"],
        "timeframe": market["timeframe"],
        "count": len(ticks),
        "ticks": ticks,
    }


def _iso(value) -> str:
    ts = pd.Timestamp(value)
    if ts.tz is None:
        ts = ts.tz_localize("UTC")
    return ts.tz_convert("UTC").isoformat().replace("+00:00", "Z")
