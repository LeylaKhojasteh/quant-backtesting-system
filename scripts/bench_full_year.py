"""Phase 3 developer benchmark: full-year 2025 XAUUSD M15 backtest.

Measures machine context, prepared-data preflight, stage timings, total runtime,
peak RSS, archive sizes, and determinism; emits a compact Full-Year Reference v1.

Read-only over data/ and cache/. Developer tooling only (not product).
"""

from __future__ import annotations

import hashlib
import json
import platform
import statistics
import sys
import tempfile
import threading
import time
from pathlib import Path

import pandas as pd
import pyarrow
import psutil

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import data_engine as de  # noqa: E402
from src import run_backtest, strategy_loader, run_request  # noqa: E402

DATASET_ID = "xauusd_dukas_ticks"
SOURCE = f"cache:{DATASET_ID}"
START, END = "2025-01-01", "2025-12-31"
TIMEFRAME = "15min"
REFERENCE_PATH = ROOT / "tests" / "fixtures" / "full_year_reference_v1.json"


def machine_info() -> dict:
    cpu = platform.processor()
    try:
        import subprocess
        out = subprocess.run(["wmic", "cpu", "get", "name"], capture_output=True, text=True)
        line = [l.strip() for l in out.stdout.splitlines() if l.strip() and l.strip().lower() != "name"]
        if line:
            cpu = line[0]
    except Exception:  # noqa: BLE001
        pass
    return {
        "os": f"{platform.system()} {platform.release()}",
        "cpu": cpu,
        "logical_cpus": psutil.cpu_count(logical=True),
        "physical_ram_bytes": psutil.virtual_memory().total,
        "python": platform.python_version(),
        "pandas": pd.__version__,
        "pyarrow": pyarrow.__version__,
    }


def preflight() -> dict:
    ds = de._resolve_dataset(SOURCE, symbol="XAUUSD")
    months = ds.get("months", [])
    tick_ok = all(
        de.partition_parquet(de.DEFAULT_CACHE_ROOT, de.PartitionId("DUKAS", "XAUUSD", int(m[:4]), int(m[5:7]))).is_file()
        for m in months
    )
    candle_missing = []
    for m in months:
        pid = de.PartitionId("DUKAS", "XAUUSD", int(m[:4]), int(m[5:7]))
        for tf in de.SUPPORTED_CANDLE_TIMEFRAMES:
            if not de.candle_partition_parquet(de.DEFAULT_CACHE_ROOT, pid, tf).is_file():
                candle_missing.append(f"{m}/{tf}")
    # raw fingerprints vs partition metadata
    fingerprint_mismatch = []
    for m in months:
        pid = de.PartitionId("DUKAS", "XAUUSD", int(m[:4]), int(m[5:7]))
        meta = de._read_json(de.partition_metadata(de.DEFAULT_CACHE_ROOT, pid), {})
        raw = ROOT / "data" / str(meta.get("source_relative_path"))
        if not raw.is_file() or de.sha256_file(raw) != meta.get("source_sha256"):
            fingerprint_mismatch.append(m)
    catalog = de.load_catalog()["datasets"][DATASET_ID]
    return {
        "dataset_id": ds.get("dataset_id"),
        "months": months,
        "tick_partitions_ok": tick_ok and len(months) == 13,
        "candle_partitions_missing": candle_missing,
        "fingerprint_mismatch": fingerprint_mismatch,
        "catalog_2025_complete": all(
            d["completeness"] == "complete" for d in catalog["month_details"] if d["month"].startswith("2025-")
        ),
        "catalog_2026_01": next(d["completeness"] for d in catalog["month_details"] if d["month"] == "2026-01"),
        "partial_months": catalog["partial_months"],
        "all_excluded": de.load_catalog()["datasets"][DATASET_ID].get("aggregate_references_included") is False,
    }


def _digest(value) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def run_once(out_root: Path, public_root: Path):
    request = run_request.BacktestRunRequest(
        strategy=run_request.StrategyRequest("candle_direction"),
        market=run_request.MarketRequest(
            symbol="XAUUSD", timeframe=TIMEFRAME, start=START, end=END,
            data_source=SOURCE, dataset_id=DATASET_ID),
        execution=run_request.ExecutionRequest(stop_distance=5.0, take_profit_distance=10.0),
    )
    t0 = time.perf_counter()
    info = run_backtest.execute_run(request, out=str(out_root), report=True, public=str(public_root))
    total = time.perf_counter() - t0
    return info, total


def deterministic_digest(result: dict) -> str:
    trades = [
        (t["direction"], t["signal_available_time"], t["entry_time"], t["entry_price"],
         t["exit_time"], t["exit_price"], t["exit_reason"], t["pnl_price"])
        for t in result["trades"]
    ]
    payload = {
        "candles": result["data_quality"]["candle_count"],
        "signals": result["execution"]["signals_received"],
        "trades": len(trades),
        "trade_rows": trades,
        "metrics": {k: result["metrics"][k] for k in sorted(result["metrics"])},
    }
    return _digest(payload)


def main() -> int:
    out = {"machine": machine_info()}
    print("MACHINE", json.dumps(out["machine"], indent=2))
    print("RAW_2025_TICKS", 71_413_977)

    pf = preflight()
    out["preflight"] = pf
    print("PREFLIGHT", json.dumps(pf, indent=2))
    if not (pf["tick_partitions_ok"] and not pf["candle_partitions_missing"] and not pf["fingerprint_mismatch"]):
        print("PREFLIGHT FAILED")
        return 1

    # ---- stage timings (single instrumented pass) --------------------------
    strategy, _ = run_backtest.resolve_strategy("candle_direction")
    strategy.configure(strategy.parameters())
    t0 = time.perf_counter()
    ticks = de.load_ticks_frame(SOURCE, symbol="XAUUSD", start=START, end=END)
    t_tick = time.perf_counter() - t0
    t0 = time.perf_counter()
    candles = de.load_candles_frame(SOURCE, timeframe=TIMEFRAME, symbol="XAUUSD", start=START, end=END)
    t_candle = time.perf_counter() - t0
    t0 = time.perf_counter()
    signals = strategy_loader.generate_validated_signals(strategy, candles)
    t_sig = time.perf_counter() - t0
    t0 = time.perf_counter()
    trades = run_backtest.engine.run_backtest(ticks, signals, 5.0, 10.0)
    t_eng = time.perf_counter() - t0
    out["stages"] = {
        "tick_rows": int(len(ticks)), "candle_rows": int(len(candles)),
        "signals": int(len(signals)), "trades": int(len(trades)),
        "tick_load_sec": round(t_tick, 3), "candle_load_sec": round(t_candle, 3),
        "signal_sec": round(t_sig, 3), "engine_sec": round(t_eng, 3),
        "ticks_per_sec_engine": int(len(ticks) / t_eng) if t_eng else None,
    }
    print("STAGES", json.dumps(out["stages"], indent=2))
    del ticks, candles, signals, trades

    # ---- total end-to-end + peak RSS + archive sizes -----------------------
    tmp = Path(tempfile.mkdtemp(prefix="qbl-fy-bench-"))
    runs = tmp / "runs"; runs.mkdir()
    pub = tmp / "public"; pub.mkdir()
    proc = psutil.Process()
    peak = [0]
    stop = [False]

    def sample():
        while not stop[0]:
            peak[0] = max(peak[0], proc.memory_info().rss)
            time.sleep(0.2)

    th = threading.Thread(target=sample, daemon=True); th.start()
    baseline_rss = proc.memory_info().rss
    info, total = run_once(runs, pub)
    stop[0] = True
    th.join(timeout=1)
    result = info["result"].to_dict()
    d1 = deterministic_digest(result)
    out["baseline"] = {
        "total_sec": round(total, 3),
        "peak_rss_bytes": peak[0],
        "baseline_rss_bytes": baseline_rss,
        "ram_pct": round(peak[0] / out["machine"]["physical_ram_bytes"] * 100, 2),
        "trades": result["metrics"]["total_trades"],
        "net_pnl": round(result["metrics"]["net_pnl"], 6),
        "digest": d1,
    }
    run_dir = Path(info["directory"])
    sizes = {p.name: p.stat().st_size for p in run_dir.iterdir() if p.is_file()}
    out["archive"] = {"files": sizes, "total_bytes": sum(sizes.values())}
    print("BASELINE", json.dumps(out["baseline"], indent=2))
    print("ARCHIVE", json.dumps(out["archive"], indent=2))

    # ---- determinism (second run) ------------------------------------------
    runs2 = tmp / "runs2"; runs2.mkdir()
    info2, total2 = run_once(runs2, pub)
    d2 = deterministic_digest(info2["result"].to_dict())
    out["determinism"] = {"digest_1": d1, "digest_2": d2, "match": d1 == d2, "second_total_sec": round(total2, 3)}
    print("DETERMINISM", json.dumps(out["determinism"], indent=2))

    # ---- compact Full-Year Reference v1 ------------------------------------
    metrics = result["metrics"]
    tr = result["trades"]
    reference = {
        "name": "Full-Year Reference v1",
        "dataset_id": DATASET_ID, "symbol": "XAUUSD", "timeframe": TIMEFRAME,
        "range": [START, END], "strategy": "candle_direction",
        "execution": {"stop_distance": 5.0, "take_profit_distance": 10.0},
        "tick_count": out["stages"]["tick_rows"], "candle_count": result["data_quality"]["candle_count"],
        "signal_count": result["execution"]["signals_received"], "trade_count": metrics["total_trades"],
        "long_trades": sum(1 for t in tr if t["direction"] == "LONG"),
        "short_trades": sum(1 for t in tr if t["direction"] == "SHORT"),
        "total_pnl": round(metrics["net_pnl"], 6),
        "profit_factor": metrics["profit_factor"], "max_drawdown": metrics["max_drawdown"],
        "win_rate": metrics["win_rate"],
        "first_trade": {k: tr[0][k] for k in ("direction", "signal_available_time", "entry_time", "entry_price", "exit_time", "exit_price", "exit_reason", "pnl_price")} if tr else None,
        "last_trade": {k: tr[-1][k] for k in ("direction", "signal_available_time", "entry_time", "entry_price", "exit_time", "exit_price", "exit_reason", "pnl_price")} if tr else None,
        "trades_digest": d1,
    }
    REFERENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
    REFERENCE_PATH.write_text(json.dumps(reference, indent=2), encoding="utf-8")
    out["reference"] = reference
    print("REFERENCE_WRITTEN", str(REFERENCE_PATH))

    Path(tempfile.gettempdir(), "qbl-fy-bench.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print("BENCH_DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
