"""Candle dataset ingestion (OHLC only markets, e.g. crypto 1m klines).

Raw layout (recursive, any nesting is fine)::

    data_candles/<SYMBOL>/<TF>/BTCUSDT_1m_2025.csv

The CSV must have a header with a timestamp column (``open_time_utc``,
``timestamp``, ``time`` or ``date``) plus ``open``, ``high``, ``low``,
``close``; extra columns (volume, trades) are ignored. Timestamps are UTC and
label the bar START.

Derived store (parquet, one file per symbol/timeframe/year)::

    cache/market_data/candles_src/provider=<P>/symbol=<S>/tf=<TF>/year=<YYYY>/candles.parquet

Optional ``_dataset.json`` at the candle root or a symbol folder declares
``provider`` / ``price_type`` honestly; otherwise ``UNSPECIFIED`` / ``unknown``.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

DEFAULT_CANDLE_DIR = Path(os.environ.get(
    "QBL_CANDLE_DIR", Path(__file__).resolve().parent.parent / "data_candles"))
DEFAULT_CACHE_ROOT = Path(__file__).resolve().parent.parent / "cache" / "market_data"
MANIFEST_NAME = "candles_manifest.json"
SCHEMA_VERSION = 1

CANDLE_COLUMNS = ("timestamp", "open", "high", "low", "close")
CANDLE_TF_ALIASES = {
    "1m": "1min", "5m": "5min", "15m": "15min", "30m": "30min",
    "1h": "1h", "4h": "4h", "1min": "1min", "5min": "5min",
    "15min": "15min", "30min": "30min",
}
SUPPORTED_CANDLE_TIMEFRAMES = ("1min", "5min", "15min", "30min", "1h", "4h")
ARROW_SCHEMA = pa.schema(
    [("timestamp", pa.timestamp("ns", tz="UTC"))]
    + [(name, pa.float64()) for name in CANDLE_COLUMNS[1:]]
)

_NAME_RE = re.compile(
    r"^(?P<symbol>[A-Za-z0-9._-]+)_(?P<tf>\d+(?:min|m|h))_(?P<year>\d{4})\.csv$",
    re.IGNORECASE,
)
_TIMESTAMP_ALIASES = ("open_time_utc", "timestamp", "time", "date", "datetime", "open_time")


class CandleDataError(ValueError):
    """Raised when a candle source or store is invalid."""


def parse_candle_filename(name: str) -> dict | None:
    match = _NAME_RE.match(name)
    if not match:
        return None
    timeframe = CANDLE_TF_ALIASES.get(match.group("tf").lower())
    if timeframe is None:
        raise CandleDataError(
            f"{name}: unsupported candle timeframe {match.group('tf')!r}; "
            f"supported: {sorted(set(CANDLE_TF_ALIASES))}"
        )
    return {
        "symbol": match.group("symbol").upper(),
        "timeframe": timeframe,
        "year": int(match.group("year")),
    }


def _declared_metadata(base: Path, path: Path) -> dict:
    """Optional ``_dataset.json`` declarations from base and parent folders."""
    declared: dict = {}
    folders = [base]
    try:
        parents = list(path.parent.relative_to(base).parents)
    except ValueError:
        parents = []
    for relative in reversed(parents):
        folders.append(base / relative)
    folders.append(path.parent)
    for folder in folders:
        sidecar = folder / "_dataset.json"
        if sidecar.is_file():
            try:
                payload = json.loads(sidecar.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise CandleDataError(f"invalid {sidecar}: {exc}") from exc
            if isinstance(payload, dict):
                declared.update(payload)
    return {
        "provider": str(declared.get("provider") or "UNSPECIFIED").upper(),
        "price_type": str(declared.get("price_type") or "unknown"),
    }


def _raw_csv_paths(base: Path) -> list[Path]:
    return sorted(
        path for path in base.rglob("*.csv")
        if path.is_file()
        and not any(part.startswith(("_", "."))
                    for part in path.relative_to(base).parts[:-1])
    )


def discover_candle_sources(candle_dir: str | Path = DEFAULT_CANDLE_DIR) -> list[dict]:
    """Monthly/yearly candle CSVs, with duplicate partitions rejected loudly."""
    base = Path(candle_dir)
    if not base.is_dir():
        return []
    found = []
    seen: dict[tuple, Path] = {}
    for path in _raw_csv_paths(base):
        meta = parse_candle_filename(path.name)
        if meta is None:
            continue
        key = (meta["symbol"], meta["timeframe"], meta["year"])
        if key in seen:
            raise CandleDataError(
                f"duplicate candle source for {key[0]} {key[1]} {key[2]}: "
                f"{seen[key].relative_to(base).as_posix()} and "
                f"{path.relative_to(base).as_posix()}"
            )
        seen[key] = path
        found.append({**meta, "path": path,
                      "relative_path": path.relative_to(base).as_posix()})
    return found


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_source(path: Path) -> pd.DataFrame:
    try:
        frame = pd.read_csv(path)
    except Exception as exc:  # noqa: BLE001 - surfaced as a data error
        raise CandleDataError(f"{path.name}: cannot read CSV: {exc}") from exc
    frame.columns = [str(c).strip().lower() for c in frame.columns]
    stamp_col = next((c for c in _TIMESTAMP_ALIASES if c in frame.columns), None)
    if stamp_col is None:
        raise CandleDataError(
            f"{path.name}: no timestamp column; found {list(frame.columns)}")
    missing = [c for c in ("open", "high", "low", "close") if c not in frame.columns]
    if missing:
        raise CandleDataError(f"{path.name}: missing OHLC columns {missing}")
    stamps = pd.to_datetime(frame[stamp_col], utc=True, errors="coerce")
    if stamps.isna().any():
        raise CandleDataError(f"{path.name}: unparseable timestamps in {stamp_col!r}")
    out = pd.DataFrame({
        "timestamp": stamps,
        **{name: pd.to_numeric(frame[name], errors="coerce").astype("float64")
           for name in ("open", "high", "low", "close")},
    })
    _validate_ohlc(out, path.name)
    if not out["timestamp"].is_monotonic_increasing:
        raise CandleDataError(f"{path.name}: timestamps are not sorted ascending")
    if out["timestamp"].duplicated().any():
        raise CandleDataError(f"{path.name}: duplicate timestamps")
    return out


def _validate_ohlc(frame: pd.DataFrame, label: str) -> None:
    values = frame[["open", "high", "low", "close"]].to_numpy(dtype="float64")
    if not np.isfinite(values).all():
        raise CandleDataError(f"{label}: non-finite OHLC values")
    o, h, low, c = (frame[name].to_numpy(dtype="float64")
                    for name in ("open", "high", "low", "close"))
    if ((h < low) | (h < o) | (h < c) | (low > o) | (low > c)).any():
        raise CandleDataError(f"{label}: inconsistent OHLC (high/low outside range)")


def candle_partition_dir(root: Path, provider: str, symbol: str,
                         timeframe: str, year: int) -> Path:
    return (root / "candles_src" / f"provider={provider}" / f"symbol={symbol}"
            / f"tf={timeframe}" / f"year={year}")


def _manifest_path(root: Path) -> Path:
    return root / MANIFEST_NAME


def _load_manifest(root: Path) -> dict:
    path = _manifest_path(root)
    if not path.is_file():
        return {"schema_version": SCHEMA_VERSION, "sources": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CandleDataError(f"invalid candle manifest {path}: {exc}") from exc
    payload.setdefault("schema_version", SCHEMA_VERSION)
    payload.setdefault("sources", {})
    return payload


def _save_manifest(root: Path, manifest: dict) -> None:
    root.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(manifest, indent=2, ensure_ascii=False)
    fd, tmp = tempfile.mkstemp(prefix=".candles-manifest-", dir=str(root))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        os.replace(tmp, _manifest_path(root))
    except Exception:
        Path(tmp).unlink(missing_ok=True)
        raise


def build_candle_year(source: dict, root: str | Path = DEFAULT_CACHE_ROOT,
                      *, allow_source_change: bool = False) -> dict:
    """Convert one yearly candle CSV into a parquet partition."""
    root = Path(root)
    path = Path(source["path"])
    symbol, timeframe, year = source["symbol"], source["timeframe"], source["year"]
    base = Path(source.get("base_dir") or DEFAULT_CANDLE_DIR)
    declared = _declared_metadata(base, path)
    provider = declared["provider"]

    manifest = _load_manifest(root)
    key = path.relative_to(base).as_posix() if base in path.parents else path.name
    current = _sha256(path)
    prior = manifest["sources"].get(key)
    if prior and prior.get("sha256") != current and not allow_source_change:
        raise CandleDataError(
            f"candle source changed unexpectedly: {key} "
            f"(expected {prior.get('sha256', '')[:12]}…, found {current[:12]}…)")

    frame = _read_source(path)
    if int(frame["timestamp"].dt.year.iloc[0]) != year:
        raise CandleDataError(
            f"{key}: declared year {year} does not match data year "
            f"{int(frame['timestamp'].dt.year.iloc[0])}")

    directory = candle_partition_dir(root, provider, symbol, timeframe, year)
    directory.mkdir(parents=True, exist_ok=True)
    parquet = directory / "candles.parquet"
    tmp = parquet.with_name(parquet.name + f".tmp-{os.getpid()}")
    table = pa.Table.from_pandas(frame, schema=ARROW_SCHEMA, preserve_index=False)
    pq.write_table(table, tmp, compression="zstd")
    os.replace(tmp, parquet)

    metadata = {
        "schema_version": SCHEMA_VERSION,
        "provider": provider, "symbol": symbol, "timeframe": timeframe,
        "year": year, "rows": int(len(frame)),
        "first_timestamp": str(frame["timestamp"].iloc[0]),
        "last_timestamp": str(frame["timestamp"].iloc[-1]),
        "source_relative_path": key, "source_sha256": current,
        "price_type": declared["price_type"],
        "timezone": "UTC",
    }
    (directory / "metadata.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")

    manifest["sources"][key] = {
        "sha256": current, "rows": int(len(frame)), "symbol": symbol,
        "provider": provider, "timeframe": timeframe, "year": year,
        "first_timestamp": metadata["first_timestamp"],
        "last_timestamp": metadata["last_timestamp"],
    }
    _save_manifest(root, manifest)
    return {"status": "BUILT", "partition": f"{symbol}/{timeframe}/{year}",
            "rows": int(len(frame))}


def build_all_candles(candle_dir: str | Path = DEFAULT_CANDLE_DIR,
                      root: str | Path = DEFAULT_CACHE_ROOT,
                      *, symbol: str | None = None,
                      allow_source_change: bool = False) -> list[dict]:
    base = Path(candle_dir)
    results = []
    for source in discover_candle_sources(base):
        if symbol and source["symbol"] != symbol.upper():
            continue
        results.append(build_candle_year(
            {**source, "base_dir": base}, root,
            allow_source_change=allow_source_change))
    return results


def _range_bounds(start, end):
    """Same window rule as the tick path.

    A date-only ``end`` (``YYYY-MM-DD``) means "include that whole day", so it
    becomes the next day at 00:00 exclusive. A datetime end is used as given.
    """
    def _ts(value):
        if not value:
            return None
        stamp = pd.Timestamp(value)
        return stamp if stamp.tzinfo is not None else stamp.tz_localize("UTC")

    start_ts = _ts(start)
    end_ts = _ts(end)
    if end_ts is not None and isinstance(end, str) and len(end.strip()) <= 10:
        end_ts = end_ts + pd.Timedelta(days=1)
    return start_ts, end_ts


def _selected_years(dataset: dict, start, end) -> list[int]:
    start_ts, end_ts = _range_bounds(start, end)
    years = list(dataset.get("years") or [])
    if start_ts is not None:
        years = [y for y in years if y >= start_ts.year]
    if end_ts is not None:
        years = [y for y in years if y <= end_ts.year]
    return years


def candle_provenance(dataset: dict, start, end,
                      root: str | Path = DEFAULT_CACHE_ROOT) -> list[dict]:
    """Per-year provenance for an archived candle run."""
    root = Path(root)
    base_tf = dataset.get("base_timeframe") or "1min"
    entries = []
    for year in _selected_years(dataset, start, end):
        directory = candle_partition_dir(
            root, dataset["provider"], dataset["symbol"], base_tf, int(year))
        meta_path = directory / "metadata.json"
        meta = {}
        if meta_path.is_file():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        entries.append({
            "year": str(year),
            "source_relative_path": meta.get("source_relative_path"),
            "source_sha256": meta.get("source_sha256"),
            "rows": meta.get("rows"),
        })
    return entries


def candle_fingerprint(dataset: dict, start, end,
                       root: str | Path = DEFAULT_CACHE_ROOT) -> str:
    """Deterministic fingerprint of the candle partitions a run used."""
    parts = [
        f"v{SCHEMA_VERSION}",
        f"{dataset['provider']}/{dataset['symbol']}/{dataset.get('base_timeframe')}",
    ]
    for entry in candle_provenance(dataset, start, end, root):
        parts.append(f"{entry['year']}@{str(entry.get('source_sha256'))[:16]}")
    return "sha256:" + hashlib.sha256("|".join(parts).encode()).hexdigest()


def _cmd_build(args) -> int:
    results = build_all_candles(
        args.candles, args.root, symbol=args.symbol,
        allow_source_change=args.accept_source_change)
    for result in results:
        print(f"BUILT     {result['partition']}  rows={result['rows']}")
    if results:
        from src import data_engine
        data_engine.build_catalog(args.root)
    print(f"built={len(results)}")
    return 0


def _cmd_inventory(args) -> int:
    sources = discover_candle_sources(args.candles)
    for source in sources:
        print(f"{source['relative_path']:<50} {source['symbol']:<10} "
              f"{source['timeframe']:<6} {source['year']}")
    print(f"files: {len(sources)}")
    return 0


def main(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Candle dataset ingestion")
    parser.add_argument("--candles", default=str(DEFAULT_CANDLE_DIR))
    parser.add_argument("--root", default=str(DEFAULT_CACHE_ROOT))
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build", help="build parquet partitions + catalog")
    build.add_argument("--symbol", default=None)
    build.add_argument("--accept-source-change", action="store_true")
    build.set_defaults(func=_cmd_build)

    inventory = sub.add_parser("inventory", help="list raw candle sources")
    inventory.set_defaults(func=_cmd_inventory)

    args = parser.parse_args(argv)
    return args.func(args)


def resolve_candle_dataset(dataset: dict, timeframe: str, start, end,
                           root: str | Path = DEFAULT_CACHE_ROOT) -> pd.DataFrame:
    """Load a candle dataset for a range, resampled to ``timeframe``.

    Bars are labeled by their START in UTC, exactly like the tick-derived
    candle frames, so a strategy sees the same convention everywhere.
    """
    root = Path(root)
    if timeframe not in SUPPORTED_CANDLE_TIMEFRAMES:
        raise CandleDataError(f"unsupported candle timeframe: {timeframe}")
    base_tf = dataset.get("base_timeframe") or "1min"
    provider, symbol = dataset["provider"], dataset["symbol"]

    start_ts, end_ts = _range_bounds(start, end)
    years = dataset.get("years") or []
    if start_ts is not None:
        years = [y for y in years if y >= start_ts.year]
    if end_ts is not None:
        years = [y for y in years if y <= end_ts.year]

    frames = []
    for year in years:
        parquet = candle_partition_dir(root, provider, symbol, base_tf, int(year)) / "candles.parquet"
        if not parquet.is_file():
            raise CandleDataError(f"candle partition missing: {parquet}")
        frames.append(pd.read_parquet(parquet))
    if not frames:
        raise CandleDataError(
            f"no candle partitions for {symbol} in the requested range")
    frame = pd.concat(frames, ignore_index=True).sort_values("timestamp", kind="stable")
    if start_ts is not None:
        frame = frame[frame["timestamp"] >= start_ts]
    if end_ts is not None:
        frame = frame[frame["timestamp"] < end_ts]
    frame = frame.set_index("timestamp")

    if timeframe == base_tf:
        return frame
    resampled = frame.resample(timeframe).agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}
    ).dropna()
    return resampled


if __name__ == '__main__':
    raise SystemExit(main())
