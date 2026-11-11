"""Regenerate the strategy comparison package from the bundled sample session.

Read-only with respect to the product: it runs the existing runner on the
committed sample CSV (no engine, data, API or strategy changes) and writes a
small, deterministic package under ``docs/comparison/``:

- ``run_manifest.json``  — settings, input fingerprint, coverage and totals
- ``reference_trades.csv`` — one row per trade, ready to compare with an
  independent reference system.

Usage:
    python scripts/export_comparison_package.py
"""

from __future__ import annotations

import csv
import hashlib
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import run_backtest, run_request  # noqa: E402

SAMPLE = ROOT / "data" / "sample" / "XAUUSD_SAMPLE_2024_12_ticks.csv"
OUT = ROOT / "docs" / "comparison"

#: Fixed comparison settings. Do not change these without updating the package.
SETTINGS = {
    "symbol": "XAUUSD",
    "timeframe": "15min",
    "start": "2024-12-04",
    "end": "2024-12-04",
    "stop_distance": 2.0,
    "take_profit_distance": 4.0,
    "spread_policy": "observed ask - bid from each tick; no additional spread",
    "volume": 1.0,
    "commission": 0.0,
    "commission_note": "not modelled by the engine; price-term PnL",
}

COLUMNS = [
    "trade_id",
    "direction",
    "signal_candle_start",
    "signal_available_time",
    "entry_time",
    "entry_price",
    "exit_time",
    "exit_price",
    "exit_reason",
    "pnl_price",
    "spread_at_entry",
    "spread_at_exit",
]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _iso(value) -> str:
    ts = pd.Timestamp(value)
    if ts.tz is None:
        ts = ts.tz_localize("UTC")
    return ts.tz_convert("UTC").isoformat().replace("+00:00", "Z")


def _sample_stats(path: Path) -> dict:
    frame = pd.read_csv(
        path,
        names=["date", "time", "bid", "ask", "bid_volume", "ask_volume"],
    )
    stamps = pd.to_datetime(
        frame["date"] + " " + frame["time"],
        format="%Y.%m.%d %H:%M:%S.%f",
        utc=True,
    )
    spread = frame["ask"] - frame["bid"]
    return {
        "ticks": int(len(frame)),
        "first_tick_utc": _iso(stamps.iloc[0]),
        "last_tick_utc": _iso(stamps.iloc[-1]),
        "spread_min": round(float(spread.min()), 5),
        "spread_mean": round(float(spread.mean()), 5),
        "spread_max": round(float(spread.max()), 5),
        "bid_min": round(float(frame["bid"].min()), 5),
        "bid_max": round(float(frame["bid"].max()), 5),
    }


def main() -> None:
    if not SAMPLE.is_file():
        raise SystemExit(f"sample data not found: {SAMPLE} (run scripts/install_sample_data.py)")

    request = run_request.BacktestRunRequest(
        strategy=run_request.StrategyRequest("candle_direction"),
        market=run_request.MarketRequest(
            symbol=SETTINGS["symbol"],
            timeframe=SETTINGS["timeframe"],
            start=SETTINGS["start"],
            end=SETTINGS["end"],
            data_source=str(SAMPLE),
        ),
        execution=run_request.ExecutionRequest(
            SETTINGS["stop_distance"], SETTINGS["take_profit_distance"]
        ),
    )

    with tempfile.TemporaryDirectory(prefix="qbl-comparison-") as work:
        info = run_backtest.execute_run(
            request, out=str(Path(work) / "runs"), report=False,
            public=str(Path(work) / "public"),
        )
    result = info["result"]

    OUT.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "trade_id": trade.trade_id,
            "direction": trade.direction,
            "signal_candle_start": trade.signal_candle_start,
            "signal_available_time": trade.signal_available_time,
            "entry_time": trade.entry_time,
            "entry_price": trade.entry_price,
            "exit_time": trade.exit_time,
            "exit_price": trade.exit_price,
            "exit_reason": trade.exit_reason,
            "pnl_price": round(trade.pnl_price, 10),
            "spread_at_entry": round(trade.spread_at_entry, 10),
            "spread_at_exit": round(trade.spread_at_exit, 10),
        }
        for trade in result.trades
    ]

    with (OUT / "reference_trades.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    manifest = {
        "package": "strategy comparison package",
        "settings": SETTINGS,
        "strategy": {
            "id": "candle_direction",
            "name": result.strategy.name,
            "version": result.strategy.version,
            "description": result.strategy.description,
            "logic": "Close > Open -> LONG; Close < Open -> SHORT; Close == Open -> no signal. "
                     "Signal available at the source candle close (bar start + timeframe).",
        },
        "input": {
            "path": "data/sample/XAUUSD_SAMPLE_2024_12_ticks.csv",
            "sha256": _sha256(SAMPLE),
            "format": "headerless: date,time,bid,ask,bid_volume,ask_volume (UTC)",
            **_sample_stats(SAMPLE),
        },
        "execution_contract": {
            "long_entry": "ASK of the first tick at/after signal availability",
            "short_entry": "BID of the first tick at/after signal availability",
            "long_exit": "BID of the triggering tick",
            "short_exit": "ASK of the triggering tick",
            "stop_checked_before_take_profit": True,
            "one_position_at_a_time": True,
            "signals_while_open": "dropped, not queued",
            "end_of_data": "open position closed at the last tick's BID (LONG) / ASK (SHORT)",
        },
        "result": {
            "trades": len(result.trades),
            "net_pnl_price": round(sum(trade.pnl_price for trade in result.trades), 10),
            "signals_received": result.execution.signals_received,
            "signals_executed": result.execution.signals_executed,
            "signals_ignored": result.execution.signals_ignored,
            "candles": result.data_quality.candle_count,
            "ticks_used": result.data_quality.tick_count,
        },
    }
    (OUT / "run_manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    print(f"wrote {OUT / 'run_manifest.json'}")
    print(f"wrote {OUT / 'reference_trades.csv'} ({len(rows)} trades)")
    print(
        f"trades={len(result.trades)} net_pnl={manifest['result']['net_pnl_price']:.5f} "
        f"candles={result.data_quality.candle_count} ticks={result.data_quality.tick_count}"
    )
    print(
        f"signals received/executed/ignored="
        f"{result.execution.signals_received}/{result.execution.signals_executed}/"
        f"{result.execution.signals_ignored}"
    )


if __name__ == "__main__":
    main()
