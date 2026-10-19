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


def _effective_bounds(market: dict) -> tuple[str, str]:
    """Non-degenerate window for reading candles/ticks.

    The archived requested range is preferred, but a date-only request stores
    midnight for both ends of a single day, which is a zero-length window. Fall
    back to the run's observed tick coverage (real first/last tick) in that case
    so the chart still shows the run's real candles.
    """
    rs, re_ = market.get("start"), market.get("end")
    obs_s, obs_e = market.get("observed_start"), market.get("observed_end")
    try:
        if rs and re_ and pd.Timestamp(rs) < pd.Timestamp(re_):
            return str(rs), str(re_)
    except Exception:
        pass
    try:
        if obs_s and obs_e and pd.Timestamp(obs_s) < pd.Timestamp(obs_e):
            return str(obs_s), str(obs_e)
    except Exception:
        pass
    return str(rs or ""), str(re_ or "")


def _window_bounds(market: dict, start: str | None, end: str | None,
                   *, limit: int, timeframe: str) -> tuple[str, str]:
    """Resolve a bounded ``[start, end)`` window for a candle/tick request.

    Explicit caller bounds win (validated non-degenerate); otherwise the window
    is the last ``limit`` timeframes ending at the run's effective end.
    """
    eff_start, eff_end = _effective_bounds(market)
    if start or end:
        win_start = start or eff_start
        win_end = end or eff_end
        if not win_start or not win_end:
            raise run_errors.InvalidMarketRequestError("start and end are required for a windowed request")
        # Guard a degenerate explicit window by extending it one timeframe.
        if pd.Timestamp(win_start) >= pd.Timestamp(win_end):
            win_end = (pd.Timestamp(win_start)
                       + pd.tseries.frequencies.to_offset(timeframe)).isoformat().replace("+00:00", "Z")
        return str(win_start), str(win_end)
    if not eff_end:
        raise run_errors.InvalidMarketRequestError("run has no archived end time to window from")
    span = pd.Timestamp(eff_end) - pd.tseries.frequencies.to_offset(timeframe) * max(1, limit)
    win_start = max(pd.Timestamp(eff_start), span) if eff_start else span
    return str(win_start), str(eff_end)


def _source_candidates(market: dict) -> list[str]:
    """Data Engine sources to try, most specific first.

    The archived dataset id is preferred; a symbol-only source is a safe
    fallback so a run whose archive records a stale/renamed dataset id still
    renders its real candles/ticks instead of a hard failure.
    """
    sources = [market["source"]]
    if market.get("dataset_id"):
        sources.append("cache:")
    return sources


def run_candles(runs_dir, run_id: str, public_dir, *,
                start: str | None = None, end: str | None = None,
                limit: int = DEFAULT_CANDLE_LIMIT) -> dict:
    """Bounded real candle window for one archived run (OHLC, BID + ASK)."""
    market = resolve_run_market(runs_dir, run_id, public_dir)
    cap = max(1, min(int(limit), data_engine.MAX_CANDLE_WINDOW))
    win_start, win_end = _window_bounds(market, start, end, limit=cap, timeframe=market["timeframe"])
    frame = None
    last_exc: Exception | None = None
    empty_window = False
    for source in _source_candidates(market):
        try:
            frame = data_engine.load_candles_window(
                source, timeframe=market["timeframe"], symbol=market["symbol"],
                start=win_start, end=win_end, limit=cap,
            )
            break
        except data_engine.PartitionMissingError as exc:
            # The derived candle cache is not prepared for this symbol/timeframe.
            raise run_errors.DataNotPreparedError(str(exc)) from exc
        except (data_engine.CoverageError, data_engine.DataEngineError) as exc:
            # The window simply holds no candles (e.g. navigation past the
            # cached coverage). That is a truthful empty answer, not a failure.
            last_exc = exc
            empty_window = True
    if frame is None and not empty_window:
        raise run_errors.DataNotPreparedError(str(last_exc) if last_exc else "candles unavailable")
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
        for row in (frame.itertuples(index=False) if frame is not None else [])
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


def _load_ticks(market: dict, *, start, end, limit):
    """Load a real tick window, falling back to a symbol-only source."""
    last_exc: Exception | None = None
    for source in _source_candidates(market):
        try:
            return data_engine.load_ticks_window(
                source, symbol=market["symbol"], start=start, end=end, limit=limit,
            )
        except data_engine.PartitionMissingError as exc:
            # The derived tick cache is not prepared for this symbol.
            raise run_errors.DataNotPreparedError(str(exc)) from exc
        except (data_engine.CoverageError, data_engine.DataEngineError) as exc:
            # No ticks in this window: a truthful empty answer, not a failure.
            last_exc = exc
            return None
    raise run_errors.DataNotPreparedError(str(last_exc) if last_exc else "ticks unavailable")


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
        frame = _load_ticks(market, start=str(win_start), end=str(win_end), limit=read_cap)
        if frame is not None:
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
        frame = _load_ticks(market, start=win_start, end=win_end, limit=cap)
    ticks = [
        {
            "timestamp": _iso(row.timestamp),
            "bid": float(row.bid),
            "ask": float(row.ask),
            "bid_volume": float(row.bid_volume),
            "ask_volume": float(row.ask_volume),
            "spread": float(row.ask) - float(row.bid),
        }
        for row in (frame.itertuples(index=False) if frame is not None else [])
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
