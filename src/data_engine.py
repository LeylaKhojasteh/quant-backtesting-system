"""Data Engine v1 (Phase 1): immutable raw CSV -> derived Parquet tick store.

RAW SOURCE IS IMMUTABLE. This module never writes inside ``data/``. It builds a
DERIVED, rebuildable, disposable Parquet tick store under ``cache/market_data/``
(monthly partitions), then reads selected date ranges back into the canonical
tick frame the verified research path already uses.

Layering:

    data/*.csv  (read-only source of truth)
        │
        ▼  canonical converter (bounded memory, atomic, idempotent)
    cache/market_data/ticks/provider=.../symbol=.../year=.../month=.../ticks.parquet
        │
        ▼  reader (partition pruning, [start, end])
    canonical tick frame  ->  existing candle/backtest path

Semantics preserved: naive wall-clock timestamps are interpreted in the
configured source timezone (default UTC), offset-aware timestamps keep their
offset, BID/ASK stay authoritative (mid is never derived), no gap filling, no
synthetic ticks, no dedup, no rounding; stable chronological order with
``source_row`` preserving equal-timestamp order. ``src/backtest.py`` is never
touched.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from src import config

# --------------------------------------------------------------------------
# Constants / contracts
# --------------------------------------------------------------------------

DATA_CACHE_SCHEMA_VERSION = 1
DEFAULT_CACHE_ROOT = config.CACHE_ROOT
DEFAULT_DATA_DIR = config.DATA_DIR
DEFAULT_COMPLETENESS_PATH = Path(__file__).resolve().parent.parent / "config" / "source_completeness.json"
DEFAULT_TIMEZONES_PATH = Path(__file__).resolve().parent.parent / "config" / "source_timezones.json"
DEFAULT_PROVIDERS_PATH = Path(__file__).resolve().parent.parent / "config" / "providers.json"

CANONICAL_COLUMNS = ("timestamp", "bid", "ask", "bid_volume", "ask_volume", "source_row")
READ_COLUMNS = ("timestamp", "bid", "ask", "bid_volume", "ask_volume", "source_row")
DEFAULT_CHUNK_ROWS = 1_000_000
#: Both sides of exact verification are streamed in this identical block size so
#: the digest is independent of read chunking (only row content matters).
VERIFY_BLOCK_ROWS = 500_000

# ---------------------------------------------------------------------------
# Candle cache (Phase 2) — derived from canonical tick partitions only.
# ---------------------------------------------------------------------------

CANDLE_CACHE_SCHEMA_VERSION = 1
CANDLE_BUILDER_VERSION = 1
#: Canonical supported timeframes (pandas offset aliases). Must match the
#: engine-supported set; asserted in tests.
SUPPORTED_CANDLE_TIMEFRAMES: tuple[str, ...] = ("1min", "5min", "15min", "30min", "1h", "4h")

CANDLE_COLUMNS = (
    "timestamp",
    "open", "high", "low", "close",
    "ask_open", "ask_high", "ask_low", "ask_close",
)
CANDLE_ARROW_SCHEMA = pa.schema([
    ("timestamp", pa.timestamp("ns", tz="UTC")),
    ("open", pa.float64()), ("high", pa.float64()), ("low", pa.float64()), ("close", pa.float64()),
    ("ask_open", pa.float64()), ("ask_high", pa.float64()), ("ask_low", pa.float64()), ("ask_close", pa.float64()),
])

ARROW_SCHEMA = pa.schema([
    ("timestamp", pa.timestamp("ns", tz="UTC")),
    ("bid", pa.float64()),
    ("ask", pa.float64()),
    ("bid_volume", pa.float64()),
    ("ask_volume", pa.float64()),
    ("source_row", pa.int64()),
])

_MONTH_RE = re.compile(
    r"^(?P<symbol>[A-Za-z0-9._-]+)_(?P<provider>[A-Za-z0-9]+)_(?P<year>\d{4})_(?P<month>\d{2})_ticks\.csv$",
    re.IGNORECASE,
)
_AGG_RE = re.compile(
    r"^(?P<symbol>[A-Za-z0-9._-]+)_(?P<provider>[A-Za-z0-9]+)_(?P<tag>[A-Za-z0-9]+)_ticks\.csv$",
    re.IGNORECASE,
)
_DUKAS_STAMP = re.compile(r"^\d{4}\.\d{2}\.\d{2}$")


# --------------------------------------------------------------------------
# Errors (structured, project-native; no raw parser tracebacks)
# --------------------------------------------------------------------------


class DataEngineError(RuntimeError):
    code = "data_engine_error"


class RawDataMissingError(DataEngineError):
    code = "raw_data_missing"


class SourceChangedError(DataEngineError):
    code = "raw_source_changed"


class CacheNotWritableError(DataEngineError):
    code = "cache_not_writable"


class CorruptCacheError(DataEngineError):
    code = "corrupt_cache"


class MalformedSchemaError(DataEngineError):
    code = "malformed_schema"


class PartitionMissingError(DataEngineError):
    code = "partition_missing"


class CoverageError(DataEngineError):
    code = "range_outside_coverage"


# --------------------------------------------------------------------------
# Layout / filenames
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class PartitionId:
    provider: str
    symbol: str
    year: int
    month: int

    @property
    def label(self) -> str:
        return f"{self.symbol}/{self.provider}/{self.year}-{self.month:02d}"


def _providers_path() -> Path:
    env = os.environ.get("QBL_PROVIDERS_FILE")
    return Path(env) if env else DEFAULT_PROVIDERS_PATH


def load_providers(path: str | Path | None = None) -> list[str]:
    """Known provider tokens, longest first, for unambiguous filename parsing."""
    target = Path(path) if path is not None else _providers_path()
    if not target.is_file():
        return []
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DataEngineError(f"invalid provider registry {target}: {exc}") from exc
    raw = payload.get("providers", payload) if isinstance(payload, dict) else None
    if isinstance(raw, dict):
        raw = list(raw.keys())
    if not isinstance(raw, list) or not all(isinstance(x, str) for x in raw):
        raise DataEngineError(f"provider registry {target} must contain a string list")
    providers = sorted({x.strip().upper() for x in raw if x.strip()},
                       key=lambda p: (-len(p), p))
    for provider in providers:
        if not re.fullmatch(r"[A-Za-z0-9_]+", provider):
            raise DataEngineError(f"provider registry {target}: invalid provider {provider!r}")
    return providers


def _parse_with_known_provider(name: str) -> dict | None:
    for provider in load_providers():
        token = re.escape(provider)
        month = re.match(
            rf"^(?P<symbol>[A-Za-z0-9._-]+)_{token}_(?P<year>\d{{4}})_(?P<month>\d{{2}})_ticks\.csv$",
            name, re.IGNORECASE,
        )
        if month:
            return {
                "symbol": month.group("symbol").upper(),
                "provider": provider,
                "year": int(month.group("year")),
                "month": int(month.group("month")),
                "kind": "month",
            }
        agg = re.match(
            rf"^(?P<symbol>[A-Za-z0-9._-]+)_{token}_(?P<tag>[A-Za-z0-9]+)_ticks\.csv$",
            name, re.IGNORECASE,
        )
        if agg:
            return {
                "symbol": agg.group("symbol").upper(),
                "provider": provider,
                "year": None,
                "month": None,
                "kind": "aggregate",
            }
    return None


def parse_source_filename(name: str) -> dict | None:
    """Return structured metadata for a monthly source file, else None."""
    known = _parse_with_known_provider(name)
    if known is not None:
        return known
    match = _MONTH_RE.match(name)
    if match:
        return {
            "symbol": match.group("symbol").upper(),
            "provider": match.group("provider").upper(),
            "year": int(match.group("year")),
            "month": int(match.group("month")),
            "kind": "month",
        }
    agg = _AGG_RE.match(name)
    if agg:
        return {
            "symbol": agg.group("symbol").upper(),
            "provider": agg.group("provider").upper(),
            "year": None,
            "month": None,
            "kind": "aggregate",
        }
    return None


def cache_root(root: str | Path = DEFAULT_CACHE_ROOT) -> Path:
    return Path(root)


def partition_dir(root: str | Path, pid: PartitionId) -> Path:
    return (Path(root) / "ticks" / f"provider={pid.provider}" / f"symbol={pid.symbol}"
            / f"year={pid.year}" / f"month={pid.month:02d}")


def partition_parquet(root: str | Path, pid: PartitionId) -> Path:
    return partition_dir(root, pid) / "ticks.parquet"


def partition_metadata(root: str | Path, pid: PartitionId) -> Path:
    return partition_dir(root, pid) / "metadata.json"


def partition_validation_path(root: str | Path, pid: PartitionId) -> Path:
    """Sidecar holding the cached per-partition tick-validation report."""
    return partition_dir(root, pid) / "validation.json"


def read_partition_validation(root: str | Path, pid: PartitionId,
                              fingerprint: str | None) -> dict | None:
    """Return the cached validation doc when it matches ``fingerprint``.

    Any mismatch (missing file, schema change, fingerprint change) means
    "not cached" — the caller re-validates that partition from Parquet.
    """
    from src import tick_validation as _tv

    if not fingerprint:
        return None
    try:
        doc = json.loads(partition_validation_path(root, pid).read_text(encoding="utf-8"))
    except OSError:
        return None
    except ValueError:
        return None
    if not isinstance(doc, dict):
        return None
    if doc.get("schema") != _tv.PARTITION_VALIDATION_SCHEMA:
        return None
    if doc.get("fingerprint") != fingerprint:
        return None
    report = doc.get("report")
    if not isinstance(report, dict):
        return None
    return doc


def write_partition_validation(root: str | Path, pid: PartitionId,
                               fingerprint: str, report) -> None:
    """Persist one partition's validation report next to its Parquet file."""
    doc = {
        "schema": report.to_dict()["schema"],
        "fingerprint": fingerprint,
        "partition": pid.label,
        "report": report.to_dict(),
    }
    _atomic_write_text(partition_validation_path(root, pid),
                       json.dumps(doc, indent=2, ensure_ascii=False))


def manifest_path(root: str | Path = DEFAULT_CACHE_ROOT) -> Path:
    return Path(root) / "manifest.json"


def catalog_path(root: str | Path = DEFAULT_CACHE_ROOT) -> Path:
    return Path(root) / "catalog.json"


# --------------------------------------------------------------------------
# Hashing / IO helpers
# --------------------------------------------------------------------------


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _ensure_writable(directory: Path) -> None:
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe = directory / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:  # noqa: BLE001
        raise CacheNotWritableError(f"cache directory not writable: {directory}") from exc


def _read_json(path: Path, default):
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise CorruptCacheError(f"unreadable cache metadata: {path}") from exc


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp-{os.getpid()}")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _is_dukascopy(path: Path) -> bool:
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        first = fh.readline()
    return bool(_DUKAS_STAMP.match(first.split(",", 1)[0].strip()))


def _tzinfo(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"unknown source timezone: {name!r}") from exc


def _localize_naive(parsed: pd.Series, timezone_name: str) -> pd.Series:
    tz = _tzinfo(timezone_name)
    if getattr(parsed.dt, "tz", None) is None:
        return parsed.dt.tz_localize(tz).dt.tz_convert("UTC")
    return parsed.dt.tz_convert("UTC")


def _timestamp_from_strings(
    dates: pd.Series, times: pd.Series, timezone_name: str = "UTC"
) -> pd.Series:
    text = dates.astype(str) + " " + times.astype(str)
    try:
        parsed = pd.to_datetime(text, format="%Y.%m.%d %H:%M:%S.%f", utc=False)
    except (TypeError, ValueError):
        # Dukascopy exports without microseconds are still the same wall clock.
        parsed = pd.to_datetime(text, utc=False, errors="coerce")
    if parsed.isna().any():
        raise ValueError("unparseable Dukascopy timestamp")
    return _localize_naive(parsed, timezone_name)


def parse_timestamps(values: pd.Series, timezone_name: str = "UTC") -> pd.Series:
    """Parse ISO strings or numeric epoch timestamps (s/ms/us/ns) to UTC.

    Naive timestamps are interpreted in the source timezone (default UTC).
    Offset-aware timestamps keep their offset. Numeric epochs are absolute and
    always UTC. Numeric YYYYMMDD strings are calendar dates in the source
    timezone. Anything unparseable is rejected instead of silently becoming NaT.
    """
    _tzinfo(timezone_name)  # fail early on a bad registry value
    text = values.astype(str).str.strip()
    numeric = pd.to_numeric(text, errors="coerce")
    parsed: pd.Series
    if len(text) and numeric.notna().all():
        date_like = text.str.fullmatch(r"\d{8}").all()
        if date_like:
            years = pd.to_numeric(text.str[:4], errors="coerce")
            if years.between(1900, 2100).all():
                parsed = _localize_naive(
                    pd.to_datetime(text, format="%Y%m%d", utc=False), timezone_name
                )
            else:
                parsed = _epoch_to_datetime(numeric)
        else:
            parsed = _epoch_to_datetime(numeric)
    else:
        offset = text.str.contains(r"(?:Z|[+-]\d{2}:?\d{2})$", regex=True)
        if offset.any() and not offset.all():
            raise ValueError("mixed timezone-aware and naive timestamps")
        if offset.all():
            parsed = pd.to_datetime(values, utc=True, errors="coerce")
        else:
            parsed = _localize_naive(
                pd.to_datetime(values, utc=False, errors="coerce"), timezone_name
            )
    if parsed.isna().any():
        bad = values[parsed.isna()].iloc[0]
        raise ValueError(f"unparseable timestamp: {bad!r}")
    return parsed


def _epoch_to_datetime(numeric: pd.Series) -> pd.Series:
    """Detect seconds / milliseconds / microseconds / nanoseconds by magnitude."""
    peak = float(numeric.abs().max())
    if peak >= 1e17:
        unit = "ns"
    elif peak >= 1e14:
        unit = "us"
    elif peak >= 1e11:
        unit = "ms"
    else:
        unit = "s"
    return pd.to_datetime(numeric, unit=unit, utc=True)


def canonical_chunks(path: str | Path, chunksize: int = DEFAULT_CHUNK_ROWS):
    """Yield canonical tick chunks (bounded memory) identical to the CSV parse.

    Reuses the verified interpretation: Dukascopy wall-clock parsed as UTC with
    no shift; header CSV (``timestamp,bid,ask``) supported too. No cleaning.
    """
    path = Path(path)
    if not path.is_file():
        raise RawDataMissingError(f"raw source not found: {path}")
    source_meta = parse_source_filename(path.name)
    source_tz = source_timezone_for(
        path.name, source_meta.get("provider") if source_meta else None
    )
    dukas = _is_dukascopy(path)
    offset = 0
    if dukas:
        reader = pd.read_csv(path, header=None, chunksize=chunksize,
                             dtype={0: str, 1: str})
        for chunk in reader:
            if chunk.shape[1] < 4:
                raise MalformedSchemaError(f"expected >=4 columns in {path.name}")
            stamps = _timestamp_from_strings(chunk[0], chunk[1], timezone_name=source_tz)
            zeros = pd.Series(0.0, index=chunk.index, dtype="float64")
            bv = chunk[4] if chunk.shape[1] > 4 else zeros
            av = chunk[5] if chunk.shape[1] > 5 else zeros
            frame = pd.DataFrame({
                "timestamp": stamps,
                "bid": pd.to_numeric(chunk[2], errors="coerce").astype("float64"),
                "ask": pd.to_numeric(chunk[3], errors="coerce").astype("float64"),
                "bid_volume": pd.to_numeric(bv, errors="coerce").astype("float64"),
                "ask_volume": pd.to_numeric(av, errors="coerce").astype("float64"),
            })
            frame["source_row"] = np.arange(offset, offset + len(frame), dtype="int64")
            offset += len(frame)
            yield frame
    else:
        probe = pd.read_csv(path, nrows=0)
        probe.columns = [str(c).strip().lower() for c in probe.columns]
        columns = set(probe.columns)
        if not {"timestamp", "bid", "ask"} <= columns:
            if {"open", "high", "low", "close"} <= columns:
                raise MalformedSchemaError(
                    f"{path.name}: OHLCV candles are not supported; provide tick timestamp,bid,ask"
                )
            if "price" in columns:
                raise MalformedSchemaError(
                    f"{path.name}: trade-only price/volume data is not supported; provide bid and ask"
                )
            raise MalformedSchemaError(
                f"{path.name}: unrecognized CSV schema; required columns are timestamp,bid,ask"
            )
        reader = pd.read_csv(path, chunksize=chunksize)
        for chunk in reader:
            chunk.columns = [str(c).strip().lower() for c in chunk.columns]
            zeros = pd.Series(0.0, index=chunk.index, dtype="float64")
            try:
                stamps = parse_timestamps(chunk["timestamp"], timezone_name=source_tz)
            except ValueError as exc:
                raise MalformedSchemaError(f"{path.name}: {exc}") from exc
            frame = pd.DataFrame({
                "timestamp": stamps,
                "bid": pd.to_numeric(chunk["bid"], errors="coerce").astype("float64"),
                "ask": pd.to_numeric(chunk["ask"], errors="coerce").astype("float64"),
                "bid_volume": pd.to_numeric(chunk["bid_volume"] if "bid_volume" in chunk else zeros, errors="coerce").astype("float64"),
                "ask_volume": pd.to_numeric(chunk["ask_volume"] if "ask_volume" in chunk else zeros, errors="coerce").astype("float64"),
            })
            frame["source_row"] = np.arange(offset, offset + len(frame), dtype="int64")
            offset += len(frame)
            yield frame


# --------------------------------------------------------------------------
# Inventory (read-only over data/)
# --------------------------------------------------------------------------


def _raw_csv_paths(base: Path) -> list[Path]:
    """Every raw CSV under ``base``, recursively, in a deterministic order.

    Symbol subfolders are supported (``data/XAUUSD/...``). Any directory whose
    name starts with ``_`` or ``.`` is skipped, so backups and archives
    (``data/_archive``, ``data/_replaced``) are never ingested by accident.
    """
    paths = [
        path
        for path in base.rglob("*.csv")
        if path.is_file()
        and not any(
            part.startswith(("_", "."))
            for part in path.relative_to(base).parts[:-1]
        )
    ]
    return sorted(paths)


def inventory(data_dir: str | Path = DEFAULT_DATA_DIR) -> list[dict]:
    """Deterministic, read-only inventory of every raw ``*.csv`` (recursive)."""
    base = Path(data_dir)
    if not base.is_dir():
        raise RawDataMissingError(f"raw data directory missing: {base}")
    entries: list[dict] = []
    for path in _raw_csv_paths(base):
        meta = parse_source_filename(path.name) or {
            "symbol": path.stem.split("_")[0].upper(), "provider": "UNKNOWN",
            "year": None, "month": None, "kind": "unknown",
        }
        dukas = _is_dukascopy(path)
        rows = 0
        first = last = None
        reader = pd.read_csv(path, header=None if dukas else 0,
                             usecols=[0, 1] if dukas else None,
                             chunksize=DEFAULT_CHUNK_ROWS,
                             dtype={0: str, 1: str} if dukas else None)
        for chunk in reader:
            if dukas:
                if first is None:
                    first = (str(chunk.iloc[0, 0]), str(chunk.iloc[0, 1]))
                last = (str(chunk.iloc[-1, 0]), str(chunk.iloc[-1, 1]))
            elif "timestamp" in chunk.columns:
                first = first or str(chunk.iloc[0]["timestamp"])
                last = str(chunk.iloc[-1]["timestamp"])
            rows += len(chunk)
        entry = {
            "relative_path": path.relative_to(base).as_posix(),
            "size_bytes": path.stat().st_size,
            "format": "dukascopy-csv" if dukas else "csv",
            "rows": rows,
            "sha256": sha256_file(path),
            **meta,
            "first_point": first,
            "last_point": last,
        }
        role = {"month": "monthly_partition", "aggregate": "aggregate_reference"}.get(
            meta.get("kind"), "reference")
        entry["source_role"] = role
        entry["included_in_logical_dataset"] = role == "monthly_partition"
        entry["completeness"] = source_completeness(path.name)
        entry["derived_partition"] = (
            f"ticks/provider={meta['provider']}/symbol={meta['symbol']}"
            f"/year={meta['year']}/month={meta['month']:02d}/ticks.parquet"
            if role == "monthly_partition"
            else None
        )
        entries.append(entry)
    return entries


# --------------------------------------------------------------------------
# Manifest
# --------------------------------------------------------------------------


def load_manifest(root: str | Path = DEFAULT_CACHE_ROOT) -> dict:
    return _read_json(manifest_path(root), {"schema_version": DATA_CACHE_SCHEMA_VERSION, "sources": {}})


def save_manifest(root: str | Path, manifest: dict) -> None:
    _atomic_write_text(manifest_path(root), json.dumps(manifest, indent=2, ensure_ascii=False))


# --------------------------------------------------------------------------
# Build (bounded memory, atomic, idempotent)
# --------------------------------------------------------------------------


def _partition_last_row(parquet_path: Path) -> int:
    pf = pq.ParquetFile(parquet_path)
    return int(pf.metadata.num_rows)


def _read_partition_bounds(parquet_path: Path) -> tuple[str, str, int]:
    pf = pq.ParquetFile(parquet_path)
    n = int(pf.metadata.num_rows)
    first = pf.read_row_group(0, columns=["timestamp"]).column("timestamp")[0].as_py()
    last_rg = pf.metadata.num_row_groups - 1
    last = pf.read_row_group(last_rg, columns=["timestamp"]).column("timestamp")[-1].as_py()
    return str(first), str(last), n


def build_partition(
    raw_path: str | Path,
    pid: PartitionId,
    *,
    root: str | Path = DEFAULT_CACHE_ROOT,
    manifest: dict | None = None,
    force: bool = False,
    allow_source_change: bool = False,
    chunk_rows: int = DEFAULT_CHUNK_ROWS,
) -> dict:
    """Convert one monthly source file to an atomic Parquet partition.

    A changed raw file is refused by default: replacing a source silently
    would rewrite history. ``allow_source_change=True`` is the explicit
    acknowledgement (CLI: ``--accept-source-change``); the manifest then
    records the new fingerprint.
    """
    raw_path = Path(raw_path)
    if not raw_path.is_file():
        raise RawDataMissingError(f"raw source not found: {raw_path}")
    manifest = manifest if manifest is not None else load_manifest(root)
    current_sha = sha256_file(raw_path)
    rel = raw_path.name
    prior = manifest["sources"].get(rel)
    if prior and prior.get("sha256") != current_sha and not allow_source_change:
        raise SourceChangedError(
            f"raw source changed unexpectedly: {rel} "
            f"(expected {prior.get('sha256')[:12]}…, found {current_sha[:12]}…)"
        )
    parquet = partition_parquet(root, pid)
    meta = partition_metadata(root, pid)
    # idempotency: unchanged raw + same schema + valid partition -> CACHE HIT
    if (not force and prior and prior.get("sha256") == current_sha and parquet.is_file()
            and meta.is_file() and prior.get("schema_version") == DATA_CACHE_SCHEMA_VERSION):
        m = _read_json(meta, {})
        if m.get("schema_version") == DATA_CACHE_SCHEMA_VERSION and _partition_last_row(parquet) == m.get("rows"):
            return {"status": "CACHE HIT", "partition": pid.label, "rows": m.get("rows")}

    _ensure_writable(partition_dir(root, pid))
    tmp = parquet.with_name(parquet.name + f".tmp-{os.getpid()}")

    rows = 0
    first_ts = last_ts = None
    writer = pq.ParquetWriter(tmp, ARROW_SCHEMA, compression="zstd")
    try:
        for frame in canonical_chunks(raw_path, chunksize=chunk_rows):
            table = pa.Table.from_pandas(frame[list(CANONICAL_COLUMNS)], schema=ARROW_SCHEMA, preserve_index=False)
            writer.write_table(table)
            rows += len(frame)
            if first_ts is None:
                first_ts = str(frame["timestamp"].iloc[0])
            last_ts = str(frame["timestamp"].iloc[-1])
        writer.close()
        writer = None
        # validate temp before promoting it
        tmp_first, tmp_last, tmp_rows = _read_partition_bounds(tmp)
        if tmp_rows != rows or not rows:
            raise CorruptCacheError(f"conversion produced {tmp_rows} rows, expected {rows}")
        os.replace(tmp, parquet)
    except Exception:
        if writer is not None:
            try:
                writer.close()
            except Exception:  # noqa: BLE001
                pass
        raise
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass

    meta_doc = {
        "schema_version": DATA_CACHE_SCHEMA_VERSION,
        "provider": pid.provider, "symbol": pid.symbol, "year": pid.year, "month": pid.month,
        "rows": rows, "first_timestamp": tmp_first, "last_timestamp": tmp_last,
        "source_relative_path": rel, "source_sha256": current_sha,
        "source_size_bytes": raw_path.stat().st_size,
        "source_role": "monthly_partition",
        "included_in_logical_dataset": True,
        "completeness": source_completeness(rel),
        "source_timezone": source_timezone_for(rel, pid.provider),
        "derived_partition": (
            f"ticks/provider={pid.provider}/symbol={pid.symbol}"
            f"/year={pid.year}/month={pid.month:02d}/ticks.parquet"
        ),
        "columns": list(CANONICAL_COLUMNS),
    }
    _atomic_write_text(meta, json.dumps(meta_doc, indent=2, ensure_ascii=False))

    manifest["schema_version"] = DATA_CACHE_SCHEMA_VERSION
    manifest["sources"][rel] = {
        "schema_version": DATA_CACHE_SCHEMA_VERSION,
        "sha256": current_sha, "size_bytes": raw_path.stat().st_size, "rows": rows,
        "symbol": pid.symbol, "provider": pid.provider, "year": pid.year, "month": pid.month,
        "first_timestamp": tmp_first, "last_timestamp": tmp_last,
        "source_role": "monthly_partition", "included_in_logical_dataset": True,
        "completeness": source_completeness(rel),
    }
    save_manifest(root, manifest)
    return {"status": "BUILT", "partition": pid.label, "rows": rows}


def discover_partitions(data_dir: str | Path = DEFAULT_DATA_DIR) -> list[tuple[Path, PartitionId]]:
    """Monthly raw files (recursive), rejecting duplicate partitions loudly.

    Two files for the same provider/symbol/year/month would silently double
    the partition, so that is an error that names both paths.
    """
    base = Path(data_dir)
    if not base.is_dir():
        raise RawDataMissingError(f"raw data directory missing: {base}")
    found: list[tuple[Path, PartitionId]] = []
    seen: dict[tuple, Path] = {}
    for path in _raw_csv_paths(base):
        meta = parse_source_filename(path.name)
        if not meta or meta["kind"] != "month":
            continue
        key = (meta["provider"], meta["symbol"], meta["year"], meta["month"])
        previous = seen.get(key)
        if previous is not None:
            raise MalformedSchemaError(
                "duplicate monthly source for "
                f"{meta['provider']}/{meta['symbol']} "
                f"{meta['year']}-{meta['month']:02d}: "
                f"{previous.relative_to(base).as_posix()} and "
                f"{path.relative_to(base).as_posix()}"
            )
        seen[key] = path
        found.append((path, PartitionId(*key)))
    return found


def build_all(
    data_dir: str | Path = DEFAULT_DATA_DIR,
    root: str | Path = DEFAULT_CACHE_ROOT,
    *,
    month: str | None = None,
    force: bool = False,
    allow_source_change: bool = False,
    chunk_rows: int = DEFAULT_CHUNK_ROWS,
) -> list[dict]:
    manifest = load_manifest(root)
    results = []
    for path, pid in discover_partitions(data_dir):
        if month and f"{pid.year}-{pid.month:02d}" != month:
            continue
        results.append(build_partition(
            path, pid, root=root, manifest=manifest, force=force,
            allow_source_change=allow_source_change, chunk_rows=chunk_rows,
        ))
    build_catalog(root)
    return results


# --------------------------------------------------------------------------
# Exact verification
# --------------------------------------------------------------------------


def _update_digest(digest: hashlib._Hash, frame: pd.DataFrame) -> None:
    n = len(frame)
    digest.update(int(n).to_bytes(8, "little"))
    ts_ns = (frame["timestamp"].dt.tz_convert("UTC").dt.tz_localize(None)
             .astype("datetime64[ns]").astype("int64").to_numpy())
    digest.update(np.ascontiguousarray(ts_ns, dtype="int64").tobytes())
    for name in ("bid", "ask", "bid_volume", "ask_volume"):
        digest.update(np.ascontiguousarray(frame[name].to_numpy(), dtype="float64").tobytes())


def _digest_source(raw_path: Path, chunk_rows: int = VERIFY_BLOCK_ROWS) -> tuple[str, int, str, str]:
    digest = hashlib.sha256()
    rows = 0
    first = last = None
    for frame in canonical_chunks(raw_path, chunksize=chunk_rows):
        _update_digest(digest, frame)
        rows += len(frame)
        if first is None:
            first = str(frame["timestamp"].iloc[0])
        last = str(frame["timestamp"].iloc[-1])
    return digest.hexdigest(), rows, first, last


def _digest_parquet(parquet_path: Path, batch_rows: int = VERIFY_BLOCK_ROWS) -> tuple[str, int, str, str]:
    digest = hashlib.sha256()
    rows = 0
    first = last = None
    pf = pq.ParquetFile(parquet_path)
    for batch in pf.iter_batches(batch_size=batch_rows, columns=list(CANONICAL_COLUMNS)):
        frame = batch.to_pandas()
        if not set(CANONICAL_COLUMNS) <= set(frame.columns):
            raise MalformedSchemaError(f"partition missing canonical columns: {parquet_path}")
        _update_digest(digest, frame)
        rows += len(frame)
        if first is None:
            first = str(frame["timestamp"].iloc[0])
        last = str(frame["timestamp"].iloc[-1])
    return digest.hexdigest(), rows, first, last


def verify_partition(
    raw_path: str | Path,
    pid: PartitionId,
    *,
    root: str | Path = DEFAULT_CACHE_ROOT,
    chunk_rows: int = VERIFY_BLOCK_ROWS,
) -> dict:
    """Exact, full-partition parity between the CSV parse and Parquet load."""
    parquet = partition_parquet(root, pid)
    if not parquet.is_file():
        raise PartitionMissingError(f"derived partition missing: {parquet}")
    src_sha = sha256_file(raw_path)
    src_hash, src_rows, src_first, src_last = _digest_source(Path(raw_path), chunk_rows)
    pq_hash, pq_rows, pq_first, pq_last = _digest_parquet(parquet)
    order_ok = pq_first == src_first and pq_last == src_last
    return {
        "partition": pid.label,
        "source_sha256": src_sha,
        "source_rows": src_rows, "derived_rows": pq_rows,
        "row_count_parity": src_rows == pq_rows,
        "value_parity": src_hash == pq_hash,
        "timestamps_match": (src_first, src_last) == (pq_first, pq_last),
        "order_parity": order_ok,
    }


def verify_all(data_dir: str | Path = DEFAULT_DATA_DIR, root: str | Path = DEFAULT_CACHE_ROOT,
               *, month: str | None = None) -> list[dict]:
    results = []
    for path, pid in discover_partitions(data_dir):
        if month and f"{pid.year}-{pid.month:02d}" != month:
            continue
        results.append(verify_partition(path, pid, root=root))
    return results


# --------------------------------------------------------------------------
# Logical dataset catalog
# --------------------------------------------------------------------------


def _read_partition_meta(root: str | Path, pid: PartitionId) -> dict:
    return _read_json(partition_metadata(root, pid), {})


#: Explicit source completeness provenance. Completeness is a KNOWN property of
#: the supplied source set — it is NEVER inferred from timestamp span / calendar
#: coverage. A monthly source not present here is ``unknown`` until its
#: completeness is explicitly declared. (Observed first/last/row_count describe
#: data; they do not determine completeness.)
def _completeness_path() -> Path:
    env = os.environ.get("QBL_COMPLETENESS_FILE")
    return Path(env) if env else DEFAULT_COMPLETENESS_PATH


def load_source_completeness(path: str | Path | None = None) -> dict[str, str]:
    """Load explicit source completeness metadata (data file, not code)."""
    target = Path(path) if path is not None else _completeness_path()
    if not target.is_file():
        return {}
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DataEngineError(f"invalid completeness registry {target}: {exc}") from exc
    files = payload.get("files", payload) if isinstance(payload, dict) else None
    if not isinstance(files, dict):
        raise DataEngineError(f"completeness registry {target} must contain a 'files' object")
    out: dict[str, str] = {}
    for key, value in files.items():
        state = str(value)
        if state not in {"complete", "partial", "unknown"}:
            raise DataEngineError(
                f"completeness registry {target}: {key!r} has invalid state {value!r}"
            )
        out[str(key)] = state
    return out


def source_completeness(name: str | None) -> str:
    """Explicit completeness for a source filename; ``unknown`` when undeclared."""
    if not name:
        return "unknown"
    return load_source_completeness().get(name, "unknown")


def _timezones_path() -> Path:
    env = os.environ.get("QBL_SOURCE_TIMEZONES_FILE")
    return Path(env) if env else DEFAULT_TIMEZONES_PATH


def load_source_timezones(path: str | Path | None = None) -> dict:
    """Load explicit source-timezone metadata (data file, not code)."""
    target = Path(path) if path is not None else _timezones_path()
    if not target.is_file():
        return {"providers": {}, "files": {}}
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DataEngineError(f"invalid timezone registry {target}: {exc}") from exc
    if not isinstance(payload, dict):
        raise DataEngineError(f"timezone registry {target} must be a JSON object")
    providers = payload.get("providers", {})
    files = payload.get("files", {})
    if not isinstance(providers, dict) or not isinstance(files, dict):
        raise DataEngineError(f"timezone registry {target} must contain objects for providers/files")
    out = {"providers": {}, "files": {}}
    for label, mapping in (("providers", providers), ("files", files)):
        for key, tz in mapping.items():
            if not isinstance(tz, str) or not tz.strip():
                raise DataEngineError(f"timezone registry {target}: {label}[{key!r}] must be a timezone name")
            _tzinfo(tz.strip())  # validate early
            key_text = str(key)
            out[label][key_text.upper() if label == "providers" else key_text] = tz.strip()
    return out


def source_timezone_for(
    name: str | None, provider: str | None = None, path: str | Path | None = None
) -> str:
    """Filename override, then provider, then UTC."""
    registry = load_source_timezones(path)
    if name and name in registry["files"]:
        return registry["files"][name]
    meta = parse_source_filename(name or "")
    resolved_provider = (provider or (meta or {}).get("provider") or "").upper()
    return registry["providers"].get(resolved_provider, "UTC")


def build_catalog(root: str | Path = DEFAULT_CACHE_ROOT) -> dict:
    root = Path(root)
    ticks = root / "ticks"
    datasets: dict[str, dict] = {}
    if ticks.is_dir():
        for provider_dir in sorted(ticks.glob("provider=*")):
            provider = provider_dir.name.split("=", 1)[1]
            for symbol_dir in sorted(provider_dir.glob("symbol=*")):
                symbol = symbol_dir.name.split("=", 1)[1]
                months = sorted(symbol_dir.glob("year=*/month=*"))
                first = last = None
                partitions = 0
                row_total = 0
                month_details = []
                years = set()
                for mdir in months:
                    meta = _read_json(mdir / "metadata.json", {})
                    if not meta:
                        continue
                    partitions += 1
                    year = int(meta["year"])
                    month = int(meta["month"])
                    years.add(year)
                    label = f"{year}-{month:02d}"
                    row_total += int(meta.get("rows", 0))
                    month_details.append({
                        "month": label,
                        "rows": int(meta.get("rows", 0)),
                        "first_timestamp": str(meta.get("first_timestamp")),
                        "last_timestamp": str(meta.get("last_timestamp")),
                        # Explicit source provenance only — never span/coverage inferred.
                        "completeness": source_completeness(meta.get("source_relative_path")),
                        "source_timezone": meta.get("source_timezone", "UTC"),
                    })
                    if first is None or str(meta["first_timestamp"]) < first:
                        first = str(meta["first_timestamp"])
                    if last is None or str(meta["last_timestamp"]) > last:
                        last = str(meta["last_timestamp"])
                # Logical identity is provider + symbol + data type (never the year).
                dataset_id = f"{symbol.lower()}_{provider.lower()}_ticks"
                datasets[dataset_id] = {
                    "dataset_id": dataset_id, "symbol": symbol, "provider": provider,
                    "data_type": "ticks", "timezone": "UTC",
                    "source_timezone": (
                        month_details[0].get("source_timezone", "UTC")
                        if month_details and len({m.get("source_timezone", "UTC") for m in month_details}) == 1
                        else ("mixed" if month_details else "UTC")
                    ),
                    "coverage_start": first, "coverage_end": last,
                    "partitions": partitions, "row_total": row_total,
                    "months": [d["month"] for d in month_details],
                    "month_details": month_details,
                    "years": sorted(years),
                    "partial_months": [d["month"] for d in month_details if d["completeness"] == "partial"],
                    "aggregate_references_included": False,
                }
    # Candle-only datasets (OHLC markets such as crypto). Stored separately
    # from ticks and never mixed with tick partitions.
    candles_src = root / "candles_src"
    if candles_src.is_dir():
        for provider_dir in sorted(candles_src.glob("provider=*")):
            provider = provider_dir.name.split("=", 1)[1]
            for symbol_dir in sorted(provider_dir.glob("symbol=*")):
                symbol = symbol_dir.name.split("=", 1)[1]
                for tf_dir in sorted(symbol_dir.glob("tf=*")):
                    timeframe = tf_dir.name.split("=", 1)[1]
                    year_details = []
                    first = last = None
                    partitions = 0
                    row_total = 0
                    price_types = set()
                    for year_dir in sorted(tf_dir.glob("year=*")):
                        meta = _read_json(year_dir / "metadata.json", {})
                        if not meta:
                            continue
                        partitions += 1
                        year = int(meta["year"])
                        row_total += int(meta.get("rows", 0))
                        price_types.add(str(meta.get("price_type") or "unknown"))
                        year_details.append({
                            "month": str(year),
                            "rows": int(meta.get("rows", 0)),
                            "first_timestamp": str(meta.get("first_timestamp")),
                            "last_timestamp": str(meta.get("last_timestamp")),
                            "completeness": "unknown",
                            "source_timezone": "UTC",
                        })
                        if first is None or str(meta["first_timestamp"]) < first:
                            first = str(meta["first_timestamp"])
                        if last is None or str(meta["last_timestamp"]) > last:
                            last = str(meta["last_timestamp"])
                    if not year_details:
                        continue
                    dataset_id = f"{symbol.lower()}_{provider.lower()}_candles_{timeframe}"
                    datasets[dataset_id] = {
                        "dataset_id": dataset_id, "symbol": symbol,
                        "provider": provider, "data_type": "candles",
                        "base_timeframe": timeframe,
                        "price_type": (price_types.pop() if len(price_types) == 1 else "mixed"),
                        "timezone": "UTC", "source_timezone": "UTC",
                        "coverage_start": first, "coverage_end": last,
                        "partitions": partitions, "row_total": row_total,
                        "months": [d["month"] for d in year_details],
                        "month_details": year_details,
                        "years": [int(d["month"]) for d in year_details],
                        "partial_months": [],
                        "aggregate_references_included": False,
                    }

    doc = {"schema_version": DATA_CACHE_SCHEMA_VERSION,
           "generated_at": datetime.now(timezone.utc).isoformat(),
           "datasets": datasets}
    if root.is_dir():
        _atomic_write_text(catalog_path(root), json.dumps(doc, indent=2, ensure_ascii=False))
    return doc


def load_catalog(root: str | Path = DEFAULT_CACHE_ROOT) -> dict:
    return _read_json(catalog_path(root), {"datasets": {}})


def logical_summary(root: str | Path = DEFAULT_CACHE_ROOT,
                    data_dir: str | Path = DEFAULT_DATA_DIR,
                    dataset_id: str | None = None) -> dict:
    """Truthful lineage/total accounting for ONE tick dataset.

    The inventory holds every symbol, so the summary must be tied to a single
    dataset; when ``dataset_id`` is omitted the tick dataset with the most
    monthly partitions is used (deterministic, never a mixture of symbols).
    """
    root = Path(root)
    inv = _read_json(root / "raw_inventory.json", {"files": []}).get("files", [])
    catalog = load_catalog(root).get("datasets", {})
    if dataset_id:
        ds = catalog.get(dataset_id, {})
    else:
        tick_sets = [d for d in catalog.values()
                     if d.get("data_type", "ticks") == "ticks"]
        ds = sorted(
            tick_sets,
            key=lambda d: (-int(d.get("partitions") or 0), str(d.get("dataset_id"))),
        )[0] if tick_sets else {}
    if ds:
        inv = [f for f in inv
               if f.get("symbol") == ds.get("symbol")
               and f.get("provider") == ds.get("provider")]
    monthly = [f for f in inv if f.get("source_role") == "monthly_partition"]
    aggregates = [f for f in inv if f.get("source_role") == "aggregate_reference"]
    # Every year present is accounted for, not just the two that happened to
    # exist when this summary was first written.
    rows_by_year: dict[int, int] = {}
    parts_by_year: dict[int, int] = {}
    for f in monthly:
        year = f.get("year")
        if year is None:
            continue
        rows_by_year[int(year)] = rows_by_year.get(int(year), 0) + int(f.get("rows", 0))
        parts_by_year[int(year)] = parts_by_year.get(int(year), 0) + 1
    agg_rows = sum(int(f.get("rows", 0)) for f in aggregates)
    return {
        "logical_dataset_id": ds.get("dataset_id"),
        "symbol": ds.get("symbol"), "provider": ds.get("provider"),
        "data_type": ds.get("data_type"), "timezone": ds.get("timezone"),
        "raw_inventory_files": len(inv),
        "authoritative_monthly_partitions": len(monthly),
        "monthly_2025_partitions": parts_by_year.get(2025, 0),
        "monthly_2026_partitions": parts_by_year.get(2026, 0),
        "rows_2025": rows_by_year.get(2025, 0),
        "rows_2026": rows_by_year.get(2026, 0),
        "rows_by_year": dict(sorted(rows_by_year.items())),
        "partitions_by_year": dict(sorted(parts_by_year.items())),
        "total_logical_rows": sum(rows_by_year.values()),
        "aggregate_reference_files": len(aggregates),
        "aggregate_rows": agg_rows,
        "aggregate_included_in_logical_total": False,
        # Only meaningful when an aggregate file is actually present.
        "aggregate_missing_ticks_vs_monthly_2025": (
            rows_by_year.get(2025, 0) - agg_rows if aggregates else None
        ),
        "derived_partitions": ds.get("partitions"),
        "coverage_start": ds.get("coverage_start"),
        "coverage_end": ds.get("coverage_end"),
        "partial_months": ds.get("partial_months"),
    }


# --------------------------------------------------------------------------
# Reader (partition pruning)
# --------------------------------------------------------------------------


def _resolve_dataset(source: str, symbol: str | None = None,
                     root: str | Path = DEFAULT_CACHE_ROOT) -> dict:
    root = Path(root)
    ref = source[len("cache:"):] if source.startswith("cache:") else ""
    catalog = load_catalog(root).get("datasets", {})
    if not catalog:
        # derive from directory if catalog missing
        catalog = build_catalog(root).get("datasets", {})
    if not catalog:
        raise PartitionMissingError("no derived datasets in cache; run build first")
    if "/" in ref:
        provider, sym = ref.split("/", 1)
        for ds in catalog.values():
            if ds["symbol"] == sym.upper() and ds["provider"].upper() == provider.upper():
                if symbol and ds["symbol"].upper() != symbol.upper():
                    raise PartitionMissingError(
                        f"dataset {source!r} is {ds['symbol']}, not {symbol.upper()}"
                    )
                return ds
        raise PartitionMissingError(f"no cached dataset for {source!r}")
    explicit_ref = bool(ref and ref not in ("", "market_data"))
    if explicit_ref:
        for ds in catalog.values():
            if ds["dataset_id"] == ref.lower():
                if symbol and ds["symbol"].upper() != symbol.upper():
                    raise PartitionMissingError(
                        f"dataset {ref!r} is {ds['symbol']}, not {symbol.upper()}"
                    )
                return ds
    if symbol:
        matches = [ds for ds in catalog.values() if ds["symbol"] == symbol.upper()]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise PartitionMissingError(f"ambiguous provider for symbol {symbol}")
        # An explicit symbol/ref must never silently resolve to a different
        # symbol (especially the only cached dataset, e.g. XAUUSD).
        raise PartitionMissingError(
            f"no cached dataset for symbol {symbol!r}"
            + (f" or dataset {ref!r}" if explicit_ref else "")
        )
    if not explicit_ref and len(catalog) == 1:
        return next(iter(catalog.values()))
    raise PartitionMissingError(f"cannot resolve dataset for {source!r}")


def resolve_range(start: str | None, end: str | None) -> tuple[pd.Timestamp | None, pd.Timestamp | None]:
    """Translate user-facing inclusive calendar dates to a half-open internal range.

    Internal contract: ``[start, end_exclusive)`` where a date-only end
    (``YYYY-MM-DD``) becomes the next calendar day at 00:00:00 UTC exclusive
    (no 23:59:59.999999 hacks). Datetime inputs with a time component are used
    as-is (``end`` is then already exclusive).
    """
    start_ts = None
    if start:
        start_ts = pd.Timestamp(start, tz="UTC")
        if len(str(start)) <= 10:
            start_ts = start_ts.normalize()
    end_exclusive = None
    if end:
        end_exclusive = pd.Timestamp(end, tz="UTC")
        if len(str(end)) <= 10:
            end_exclusive = end_exclusive.normalize() + pd.Timedelta(days=1)
    return start_ts, end_exclusive


def _months_between(start_ts: pd.Timestamp | None, end_exclusive: pd.Timestamp | None,
                    available: list[str]) -> list[str]:
    """Partitions whose ``[month_start, next_month_start)`` overlaps the range."""
    wanted = []
    for label in available:
        year, month = (int(x) for x in label.split("-"))
        m_start = pd.Timestamp(year=year, month=month, day=1, tz="UTC")
        m_end = m_start + pd.offsets.MonthBegin(1)
        if start_ts is not None and m_end <= start_ts:
            continue
        if end_exclusive is not None and m_start >= end_exclusive:
            continue
        wanted.append(label)
    return wanted


def cache_fingerprint(source: str, *, symbol: str | None = None, start: str | None = None,
                      end: str | None = None, root: str | Path = DEFAULT_CACHE_ROOT) -> str:
    """Deterministic provenance fingerprint of the derived partitions used."""
    ds = _resolve_dataset(source, symbol=symbol, root=root)
    start_ts, end_exclusive = resolve_range(start, end)
    months = _months_between(start_ts, end_exclusive, ds.get("months", [])) or ds.get("months", [])
    parts = [f"v{DATA_CACHE_SCHEMA_VERSION}", f"{ds['provider']}/{ds['symbol']}"]
    for label in months:
        year, month = (int(x) for x in label.split("-"))
        meta = _read_json(partition_metadata(root, PartitionId(ds["provider"], ds["symbol"], year, month)), {})
        parts.append(f"{label}@{str(meta.get('source_sha256'))[:16]}")
    return "sha256:" + hashlib.sha256("|".join(parts).encode()).hexdigest()


def data_provenance(source: str, *, symbol: str | None = None, timeframe: str | None = None,
                    start: str | None = None, end: str | None = None,
                    root: str | Path = DEFAULT_CACHE_ROOT) -> dict:
    """Reproducibility record for the derived data used by a run (archive only)."""
    ds = _resolve_dataset(source, symbol=symbol, root=root)
    start_ts, end_exclusive = resolve_range(start, end)
    months = _months_between(start_ts, end_exclusive, ds.get("months", []))
    partitions = []
    for label in months:
        year, month = (int(x) for x in label.split("-"))
        pid = PartitionId(ds["provider"], ds["symbol"], year, month)
        tm = _read_json(partition_metadata(root, pid), {})
        entry = {
            "month": label,
            "source_relative_path": tm.get("source_relative_path"),
            "source_sha256": tm.get("source_sha256"),
            "tick_partition_fingerprint": _tick_partition_fingerprint(tm) if tm else None,
        }
        if timeframe:
            cm = _read_json(candle_partition_metadata(root, pid, timeframe), {})
            entry["candle_partition_fingerprint"] = cm.get("candle_fingerprint")
        partitions.append(entry)
    return {
        "dataset_id": ds.get("dataset_id"), "provider": ds.get("provider"),
        "symbol": ds.get("symbol"), "timeframe": timeframe,
        "requested_start": start, "requested_end": end,
        "coverage_start": ds.get("coverage_start"), "coverage_end": ds.get("coverage_end"),
        "source_timezone": ds.get("source_timezone", "UTC"),
        "tick_cache_schema": DATA_CACHE_SCHEMA_VERSION,
        "candle_cache_schema": CANDLE_CACHE_SCHEMA_VERSION,
        "candle_builder_version": CANDLE_BUILDER_VERSION,
        "partitions": partitions,
    }


def select_partitions(symbol: str, start: str | None, end: str | None,
                      root: str | Path = DEFAULT_CACHE_ROOT) -> list[str]:
    """Months whose partition files are needed for the inclusive date range."""
    ds = _resolve_dataset("cache:", symbol=symbol, root=root)
    start_ts, end_exclusive = resolve_range(start, end)
    return _months_between(start_ts, end_exclusive, ds.get("months", []))


def load_ticks_frame(
    source: str,
    *,
    symbol: str | None = None,
    start: str | None = None,
    end: str | None = None,
    root: str | Path = DEFAULT_CACHE_ROOT,
    columns: list[str] | None = None,
    limit: int | None = None,
) -> pd.DataFrame:
    """Load the canonical tick frame for an inclusive calendar-date ``[start, end]``.

    Internally the range is half-open ``[start 00:00, next-day(end) 00:00)``.
    Opens only overlapping monthly partitions and returns canonical columns
    (default: timestamp,bid,ask,bid_volume,ask_volume) — identical to the
    verified CSV parse. ``columns`` projects a smaller execution frame when the
    caller does not need every column (the Parquet store stays complete).
    ``limit`` is for bounded read-only previews; it stops reading partitions as
    soon as enough rows are collected and never changes the cached data.
    """
    out_cols = list(columns) if columns else ["timestamp", "bid", "ask", "bid_volume", "ask_volume"]
    read_cols = list(dict.fromkeys(out_cols + ["timestamp", "source_row"]))
    ds = _resolve_dataset(source, symbol=symbol, root=root)
    start_ts, end_exclusive = resolve_range(start, end)
    months = _months_between(start_ts, end_exclusive, ds.get("months", []))
    if not months:
        raise CoverageError(
            f"requested range {start}..{end} is outside cached coverage "
            f"{ds.get('coverage_start')}..{ds.get('coverage_end')}"
        )
    if limit is not None:
        return _load_ticks_limited(
            ds, start_ts, end_exclusive, months, out_cols, read_cols,
            int(limit), root, start, end,
        )
    # Preallocate one contiguous frame (bounded peak) instead of N frames + concat
    # or Arrow-table concatenation, both of which hold multiple full-year copies.
    parts = []
    total = 0
    for label in months:
        year, month = (int(x) for x in label.split("-"))
        pid = PartitionId(ds["provider"], ds["symbol"], year, month)
        parquet = partition_parquet(root, pid)
        if not parquet.is_file():
            raise PartitionMissingError(f"required partition missing: {pid.label}")
        n = int(pq.ParquetFile(parquet).metadata.num_rows)
        parts.append((parquet, n))
        total += n
    if total == 0:
        raise CoverageError(f"no cached ticks in {source} for {start}..{end}")
    arrays = {c: np.empty(total, dtype="int64" if c in ("timestamp", "source_row") else "float64")
              for c in read_cols}
    pos = 0
    for parquet, _n in parts:
        pf = pq.ParquetFile(parquet)
        for batch in pf.iter_batches(batch_size=1_000_000, columns=read_cols):
            bn = batch.num_rows
            for c in read_cols:
                col = batch.column(c)
                arrays[c][pos:pos + bn] = (col.cast(pa.int64()).to_numpy()
                                           if c in ("timestamp", "source_row") else col.to_numpy())
            pos += bn
    frame = pd.DataFrame({c: arrays[c] for c in read_cols if c != "timestamp"}, copy=False)
    frame.insert(0, "timestamp", pd.to_datetime(arrays["timestamp"], utc=True))
    # Skip filter copies when the requested range already covers the loaded data.
    if len(frame):
        need_start = start_ts is not None and start_ts > frame["timestamp"].iloc[0]
        need_end = end_exclusive is not None and end_exclusive <= frame["timestamp"].iloc[-1]
        if need_start:
            frame = frame[frame["timestamp"] >= start_ts]
        if need_end:
            frame = frame[frame["timestamp"] < end_exclusive]
    if frame.empty:
        raise CoverageError(f"no cached ticks in {source} for {start}..{end}")
    # Partitions are concatenated in chronological month order and each partition
    # is already ordered, so sorting is only needed if timestamps are not monotone.
    if not frame["timestamp"].is_monotonic_increasing:
        frame = frame.sort_values(["timestamp", "source_row"], kind="stable")
    if "source_row" in frame.columns:
        frame.drop(columns=["source_row"], inplace=True)
    if not frame.index.equals(pd.RangeIndex(len(frame))):
        frame.reset_index(drop=True, inplace=True)
    return frame


def validate_range(source: str, *, symbol: str | None = None,
                     start: str | None = None, end: str | None = None,
                     root: str | Path = DEFAULT_CACHE_ROOT,
                     frame: "pd.DataFrame | None" = None):
    """Validate a cached range using per-partition cached reports.

    Each overlapping monthly partition is validated at most once: the report
    is cached in ``validation.json`` keyed by the partition fingerprint, so
    repeat runs reuse it. Partition reports combine via
    :func:`src.tick_validation.combine_partition_reports`; only the seams
    (last row of one partition vs first row of the next, plus the requested
    range edges) are checked on the live frame. Uncached/changed partitions
    are validated from Parquet on demand (bounded to that partition only).

    ``frame`` is the already-loaded tick frame for the range (avoids a second
    Parquet read); when omitted it is loaded here.
    """
    from src import tick_validation as _tv

    ds = _resolve_dataset(source, symbol=symbol, root=root)
    start_ts, end_exclusive = resolve_range(start, end)
    months = _months_between(start_ts, end_exclusive, ds.get("months", []))
    if not months:
        raise CoverageError(
            f"requested range {start}..{end} is outside cached coverage "
            f"{ds.get('coverage_start')}..{ds.get('coverage_end')}"
        )
    if frame is None:
        frame = load_ticks_frame(source, symbol=symbol, start=start, end=end, root=root,
                                 columns=["timestamp", "bid", "ask"])
    reports = []
    # Run-frame row offset per month: cached reports carry partition-local
    # rows, so shift them to run-frame positions for honest samples. Slice
    # lengths come from the live frame (exact), never from metadata.
    ts_all = frame["timestamp"]
    pos = 0
    for label in months:
        year, month = (int(x) for x in label.split("-"))
        pid = PartitionId(ds["provider"], ds["symbol"], year, month)
        m_start = pd.Timestamp(year=year, month=month, day=1, tz="UTC")
        m_end = m_start + pd.offsets.MonthBegin(1)
        full = ((start_ts is None or start_ts <= m_start)
                and (end_exclusive is None or end_exclusive >= m_end))
        slen = int(((ts_all >= m_start) & (ts_all < m_end)).sum())
        if full and slen == int((_read_json(partition_metadata(root, pid), {})
                                 .get("rows", -1))):
            meta = _read_json(partition_metadata(root, pid), {})
            fp = _tick_partition_fingerprint(meta) if meta else None
            cached = read_partition_validation(root, pid, fp)
            if cached is not None:
                rep = _tv.ValidationReport.from_dict(cached["report"])
            else:
                part = load_ticks_frame(source, symbol=symbol,
                                        start=f"{year}-{month:02d}-01",
                                        end=_month_end_exclusive(year, month),
                                        root=root, columns=["timestamp", "bid", "ask"])
                rep = _tv.validate_ticks(part)
                if fp:
                    write_partition_validation(root, pid, fp, rep)
            _shift_report_rows(rep, pos)
            reports.append(rep)
        else:
            # Edge (or filtered) month: validate the live slice directly.
            # Small; keeps run-frame positions so samples stay exact. Never
            # cached — the cache only holds whole-partition reports.
            sl = frame[(ts_all >= m_start) & (ts_all < m_end)]
            reports.append(_tv.validate_ticks(sl))
        pos += slen
    combined = _tv.combine_partition_reports(reports)
    _check_seams(frame, combined)
    _check_range_edges(frame, start_ts, end_exclusive, combined)
    return combined


def _shift_report_rows(rep, offset: int) -> None:
    """Shift integer row references in a cached report to run-frame positions."""
    if not offset:
        return
    for rows in rep.samples.values():
        for s in rows:
            for key in ("row", "from_row", "to_row"):
                if key in s and isinstance(s[key], int):
                    s[key] = s[key] + offset


def _month_end_exclusive(year: int, month: int) -> str:
    # Full datetime (not date-only): resolve_range() extends date-only ends
    # by a day, which would overlap the next month's partition and
    # double-count its first day. The T00:00:00 form is used as-is (exclusive).
    if month == 12:
        return f"{year + 1}-01-01T00:00:00"
    return f"{year}-{month + 1:02d}-01T00:00:00"


def _check_seams(frame: "pd.DataFrame", report) -> None:
    """Seam intervals between consecutively stored partitions (O(months)).

    Inside-partition stats come from the cached per-partition reports, but
    each partition's own ``diff()`` drops its first row — so the joint
    intervals (last row of one partition -> first row of the next) exist in
    neither report. They are counted here exactly like any other interval:
    positive joints join gap_count/max/samples, negative joints are unsorted,
    equal joints are cross-partition duplicates.
    """
    import numpy as _np

    ts = frame["timestamp"]
    if len(ts) < 2:
        return
    # Integer month codes (vectorized, no strftime): joints are where the
    # month flips between consecutively stored partitions.
    ym = ts.dt.year.to_numpy() * 12 + ts.dt.month.to_numpy()
    joints = _np.flatnonzero(ym[1:] != ym[:-1]) + 1
    from src import tick_validation as _tv

    bad_order = 0
    dup_groups = 0
    dup_rows = 0
    seam_gaps: list[dict] = []
    for j in joints.tolist():
        a = ts.iloc[j - 1]
        b = ts.iloc[j]
        if pd.isna(a) or pd.isna(b):
            continue
        if b < a:
            bad_order += 1
        elif b == a:
            dup_groups += 1
            dup_rows += 2
        else:
            secs = (b - a).total_seconds()
            seam_gaps.append({
                "from_row": _tv__index_value(frame.index, j - 1),
                "to_row": _tv__index_value(frame.index, j),
                "from_timestamp": str(a),
                "to_timestamp": str(b),
                "seconds": float(secs),
            })
    if seam_gaps:
        report.validation["gap_count"] = int(report.validation["gap_count"]) + len(seam_gaps)
        seam_max = max(g["seconds"] for g in seam_gaps)
        report.validation["gap_max_seconds"] = max(
            float(report.validation["gap_max_seconds"]), seam_max)
        report.validation["interval_max_seconds"] = max(
            float(report.validation["interval_max_seconds"]), seam_max)
        merged = list(report.samples.get("gap", [])) + seam_gaps
        merged.sort(key=lambda s: float(s.get("seconds", 0.0)), reverse=True)
        report.samples["gap"] = merged[: _tv.MAX_SAMPLES]
        # Refresh the interval warning so its count/max match the run frame.
        top = report.samples["gap"][0]
        report.warnings = [w for w in report.warnings
                           if "inter-tick intervals reported" not in w]
        report.warnings.append(
            f"inter-tick intervals reported: {report.validation['gap_count']} positive interval(s); "
            f"max {_tv._fmt_seconds(float(report.validation['gap_max_seconds']))} "
            f"({top['from_timestamp']} -> {top['to_timestamp']}, "
            f"rows {top['from_row']}-> {top['to_row']})"
        )
    if bad_order:
        report.validation["unsorted_pairs"] = int(report.validation["unsorted_pairs"]) + bad_order
        report.warnings.append(
            f"unsorted timestamps across partition seams: {bad_order} joint(s)"
        )
    if dup_groups:
        report.validation["duplicate_timestamp_groups"] = (
            int(report.validation["duplicate_timestamp_groups"]) + dup_groups)
        report.validation["duplicate_timestamp_rows"] = (
            int(report.validation["duplicate_timestamp_rows"]) + dup_rows)


def _tv__index_value(index, pos: int):
    value = index[pos]
    try:
        return int(value)
    except (TypeError, ValueError):
        return str(value)


def _check_range_edges(frame: "pd.DataFrame", start_ts, end_exclusive, report) -> None:
    """Range-edge gaps (requested start/end vs actual coverage) as warnings."""
    if len(frame) == 0:
        return
    first = frame["timestamp"].iloc[0]
    last = frame["timestamp"].iloc[-1]
    if start_ts is not None and first > start_ts:
        gap = (first - start_ts).total_seconds()
        report.warnings.append(
            f"requested start {start_ts} has no ticks until {first} ({gap:.1f}s gap)"
        )
    if end_exclusive is not None and last < end_exclusive - pd.Timedelta(microseconds=1):
        gap = (end_exclusive - last).total_seconds()
        report.warnings.append(
            f"requested end {end_exclusive} has no ticks after {last} ({gap:.1f}s gap)"
        )


def _load_ticks_limited(
    ds: dict,
    start_ts,
    end_exclusive,
    months: list[str],
    out_cols: list[str],
    read_cols: list[str],
    limit: int,
    root: str | Path,
    start: str | None,
    end: str | None,
) -> pd.DataFrame:
    """Bounded tick read for previews: stop after ``limit`` matching rows."""
    if limit <= 0:
        raise ValueError("limit must be > 0")
    remaining = limit
    frames = []
    for label in months:
        year, month = (int(x) for x in label.split("-"))
        pid = PartitionId(ds["provider"], ds["symbol"], year, month)
        parquet = partition_parquet(root, pid)
        if not parquet.is_file():
            raise PartitionMissingError(f"required partition missing: {pid.label}")
        pf = pq.ParquetFile(parquet)
        for batch in pf.iter_batches(
            batch_size=max(1, min(remaining, 4096)), columns=read_cols
        ):
            frame = batch.to_pandas()
            frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
            if start_ts is not None:
                frame = frame[frame["timestamp"] >= start_ts]
            if end_exclusive is not None:
                frame = frame[frame["timestamp"] < end_exclusive]
            if frame.empty:
                continue
            take = min(len(frame), remaining)
            frames.append(frame.iloc[:take])
            remaining -= take
            if remaining <= 0:
                break
        if remaining <= 0:
            break
    if not frames:
        raise CoverageError(f"no cached ticks in {ds.get('dataset_id')} for {start}..{end}")
    frame = pd.concat(frames, ignore_index=True)
    if "source_row" in frame.columns:
        frame.drop(columns=["source_row"], inplace=True)
    return frame.reset_index(drop=True)


# Back-compat alias used by the run boundary.
load_ticks = load_ticks_frame


# --------------------------------------------------------------------------
# Candle cache (derived from canonical tick partitions; never from raw CSV)
# --------------------------------------------------------------------------


def candle_partition_dir(root: str | Path, pid: PartitionId, timeframe: str) -> Path:
    return (Path(root) / "candles" / f"provider={pid.provider}" / f"symbol={pid.symbol}"
            / f"timeframe={timeframe}" / f"year={pid.year}" / f"month={pid.month:02d}")


def candle_partition_parquet(root: str | Path, pid: PartitionId, timeframe: str) -> Path:
    return candle_partition_dir(root, pid, timeframe) / "candles.parquet"


def candle_partition_metadata(root: str | Path, pid: PartitionId, timeframe: str) -> Path:
    return candle_partition_dir(root, pid, timeframe) / "metadata.json"


def _tick_partition_fingerprint(tick_meta: dict) -> str:
    return f"v{tick_meta.get('schema_version')}:{tick_meta.get('source_sha256')}"


def candle_fingerprint(pid: PartitionId, timeframe: str, tick_meta: dict) -> str:
    payload = f"v{CANDLE_CACHE_SCHEMA_VERSION}:b{CANDLE_BUILDER_VERSION}:{timeframe}:{_tick_partition_fingerprint(tick_meta)}"
    return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()


def build_candles_from_ticks(tick_frame: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """The exact verified candle frame (BID OHLC + derived ASK OHLC).

    Reproduces ``ticks.set_index('timestamp')['bid'].resample(tf).ohlc().dropna()``
    (left-labeled, left-closed, no gap fill) and adds ASK OHLC derived from the
    authoritative tick ASK (never MID).
    """
    s = tick_frame.set_index("timestamp")
    bid = s["bid"].resample(timeframe).ohlc().dropna()
    ask = s["ask"].resample(timeframe).ohlc().dropna().reindex(bid.index)
    out = pd.DataFrame({"timestamp": bid.index})
    for c in ("open", "high", "low", "close"):
        out[c] = bid[c].to_numpy(dtype="float64")
        out[f"ask_{c}"] = ask[c].to_numpy(dtype="float64")
    return out[list(CANDLE_COLUMNS)]


def _read_tick_partitions(root: str | Path, pid: PartitionId) -> pd.DataFrame:
    parquet = partition_parquet(root, pid)
    if not parquet.is_file():
        raise PartitionMissingError(
            f"canonical tick partition missing for {pid.label}; run `build` first")
    return pq.read_table(parquet, columns=["timestamp", "bid", "ask"]).to_pandas()


def build_candle_partition(
    pid: PartitionId,
    timeframe: str,
    *,
    root: str | Path = DEFAULT_CACHE_ROOT,
    force: bool = False,
) -> dict:
    if timeframe not in SUPPORTED_CANDLE_TIMEFRAMES:
        raise DataEngineError(f"unsupported candle timeframe: {timeframe}")
    tick_meta = _read_json(partition_metadata(root, pid), {})
    if not tick_meta:
        raise PartitionMissingError(f"canonical tick partition metadata missing for {pid.label}")
    fp = candle_fingerprint(pid, timeframe, tick_meta)
    parquet = candle_partition_parquet(root, pid, timeframe)
    meta = candle_partition_metadata(root, pid, timeframe)
    if (not force and parquet.is_file() and meta.is_file()):
        m = _read_json(meta, {})
        if m.get("candle_fingerprint") == fp and m.get("schema_version") == CANDLE_CACHE_SCHEMA_VERSION \
                and _partition_last_row(parquet) == m.get("rows"):
            return {"status": "CACHE HIT", "partition": f"{pid.label}/{timeframe}", "rows": m.get("rows")}

    _ensure_writable(candle_partition_dir(root, pid, timeframe))
    ticks = _read_tick_partitions(root, pid)
    candles = build_candles_from_ticks(ticks, timeframe)
    rows = len(candles)
    tmp = parquet.with_name(parquet.name + f".tmp-{os.getpid()}")
    writer = None
    try:
        writer = pq.ParquetWriter(tmp, CANDLE_ARROW_SCHEMA, compression="zstd")
        writer.write_table(pa.Table.from_pandas(candles, schema=CANDLE_ARROW_SCHEMA, preserve_index=False))
        writer.close()
        writer = None
        os.replace(tmp, parquet)
    except Exception:
        if writer is not None:
            try:
                writer.close()
            except Exception:  # noqa: BLE001
                pass
        raise
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
    first = str(candles["timestamp"].iloc[0]) if rows else None
    last = str(candles["timestamp"].iloc[-1]) if rows else None
    meta_doc = {
        "schema_version": CANDLE_CACHE_SCHEMA_VERSION,
        "candle_builder_version": CANDLE_BUILDER_VERSION,
        "provider": pid.provider, "symbol": pid.symbol, "timeframe": timeframe,
        "year": pid.year, "month": pid.month, "rows": rows,
        "first_timestamp": first, "last_timestamp": last,
        "candle_fingerprint": fp,
        "tick_partition": f"ticks/provider={pid.provider}/symbol={pid.symbol}/year={pid.year}/month={pid.month:02d}/ticks.parquet",
        "tick_partition_fingerprint": _tick_partition_fingerprint(tick_meta),
        "source_relative_path": tick_meta.get("source_relative_path"),
        "source_sha256": tick_meta.get("source_sha256"),
        "columns": list(CANDLE_COLUMNS),
    }
    _atomic_write_text(meta, json.dumps(meta_doc, indent=2, ensure_ascii=False))
    return {"status": "BUILT", "partition": f"{pid.label}/{timeframe}", "rows": rows}


def _digest_candles(frame: pd.DataFrame) -> str:
    digest = hashlib.sha256()
    digest.update(int(len(frame)).to_bytes(8, "little"))
    ts = frame["timestamp"].dt.tz_convert("UTC").dt.tz_localize(None).astype("datetime64[ns]").astype("int64").to_numpy()
    digest.update(np.ascontiguousarray(ts, dtype="int64").tobytes())
    for c in CANONICAL_CANDLE_VALUE_COLUMNS:
        digest.update(np.ascontiguousarray(frame[c].to_numpy(), dtype="float64").tobytes())
    return digest.hexdigest()


CANONICAL_CANDLE_VALUE_COLUMNS = ("open", "high", "low", "close", "ask_open", "ask_high", "ask_low", "ask_close")


def verify_candle_partition(
    pid: PartitionId,
    timeframe: str,
    *,
    root: str | Path = DEFAULT_CACHE_ROOT,
) -> dict:
    parquet = candle_partition_parquet(root, pid, timeframe)
    if not parquet.is_file():
        raise PartitionMissingError(f"candle partition missing: {pid.label}/{timeframe}")
    ticks = _read_tick_partitions(root, pid)
    onthefly = build_candles_from_ticks(ticks, timeframe)
    cached = pq.read_table(parquet, columns=list(CANDLE_COLUMNS)).to_pandas()
    return {
        "partition": f"{pid.label}/{timeframe}",
        "onthefly_rows": len(onthefly), "cached_rows": len(cached),
        "row_count_parity": len(onthefly) == len(cached),
        "value_parity": _digest_candles(onthefly) == _digest_candles(cached),
        "first_timestamp": str(onthefly["timestamp"].iloc[0]) if len(onthefly) else None,
        "last_timestamp": str(onthefly["timestamp"].iloc[-1]) if len(onthefly) else None,
    }


def build_all_candles(data_dir: str | Path = DEFAULT_DATA_DIR, root: str | Path = DEFAULT_CACHE_ROOT,
                      *, timeframe: str | None = None, force: bool = False) -> list[dict]:
    timeframes = (timeframe,) if timeframe else SUPPORTED_CANDLE_TIMEFRAMES
    results = []
    for _, pid in discover_partitions(data_dir):
        for tf in timeframes:
            results.append(build_candle_partition(pid, tf, root=root, force=force))
    return results


def verify_all_candles(data_dir: str | Path = DEFAULT_DATA_DIR, root: str | Path = DEFAULT_CACHE_ROOT,
                       *, timeframe: str | None = None) -> list[dict]:
    timeframes = (timeframe,) if timeframe else SUPPORTED_CANDLE_TIMEFRAMES
    results = []
    for _, pid in discover_partitions(data_dir):
        for tf in timeframes:
            results.append(verify_candle_partition(pid, tf, root=root))
    return results


def load_candles_frame(
    source: str,
    *,
    timeframe: str,
    symbol: str | None = None,
    start: str | None = None,
    end: str | None = None,
    root: str | Path = DEFAULT_CACHE_ROOT,
) -> pd.DataFrame:
    """Load cached candles as the verified strategy frame (bid OHLC, timestamp index).

    Identical in shape/values to ``ticks.set_index('timestamp')['bid'].resample(tf).ohlc().dropna()``
    for the requested inclusive calendar range. Missing candle cache → structured error.
    """
    if timeframe not in SUPPORTED_CANDLE_TIMEFRAMES:
        raise DataEngineError(f"unsupported candle timeframe: {timeframe}")
    ds = _resolve_dataset(source, symbol=symbol, root=root)
    start_ts, end_exclusive = resolve_range(start, end)
    months = _months_between(start_ts, end_exclusive, ds.get("months", []))
    if not months:
        raise CoverageError(f"requested range {start}..{end} is outside cached coverage")
    frames = []
    for label in months:
        year, month = (int(x) for x in label.split("-"))
        pid = PartitionId(ds["provider"], ds["symbol"], year, month)
        parquet = candle_partition_parquet(root, pid, timeframe)
        if not parquet.is_file():
            raise PartitionMissingError(
                f"candle cache not prepared for {ds['symbol']} {timeframe} {label}; "
                f"run `build candles` first")
        frames.append(pq.read_table(parquet, columns=list(CANDLE_COLUMNS)).to_pandas())
    frame = pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0]
    if start_ts is not None:
        frame = frame[frame["timestamp"] >= start_ts]
    if end_exclusive is not None:
        frame = frame[frame["timestamp"] < end_exclusive]
    frame = frame.sort_values("timestamp", kind="stable").reset_index(drop=True)
    if frame.empty:
        raise CoverageError(f"no cached candles for {source} {timeframe} in {start}..{end}")
    return frame.set_index("timestamp")[["open", "high", "low", "close"]]


#: Hard ceilings for product visualization reads (never a full-year dump).
MAX_CANDLE_WINDOW = 1500
MAX_TICK_WINDOW = 5000


def load_candles_window(
    source: str,
    *,
    timeframe: str,
    symbol: str | None = None,
    start: str | None = None,
    end: str | None = None,
    limit: int = 800,
    root: str | Path | None = None,
) -> pd.DataFrame:
    """Bounded real candle window (BID + ASK OHLC) for visualization.

    Returns at most ``limit`` candles (never more than ``MAX_CANDLE_WINDOW``),
    chronological, restricted to ``[start, end)``. Identical values to the
    canonical candle cache — the same source the engine's strategy frame uses.
    This is a read-only projection: no resampling, no gap filling, no synthesis.
    """
    root = DEFAULT_CACHE_ROOT if root is None else root
    if timeframe not in SUPPORTED_CANDLE_TIMEFRAMES:
        raise DataEngineError(f"unsupported candle timeframe: {timeframe}")
    cap = max(1, min(int(limit), MAX_CANDLE_WINDOW))
    ds = _resolve_dataset(source, symbol=symbol, root=root)
    start_ts, end_exclusive = resolve_range(start, end)
    months = _months_between(start_ts, end_exclusive, ds.get("months", []))
    if not months:
        raise CoverageError(f"requested range {start}..{end} is outside cached coverage")
    frames = []
    for label in months:
        year, month = (int(x) for x in label.split("-"))
        pid = PartitionId(ds["provider"], ds["symbol"], year, month)
        parquet = candle_partition_parquet(root, pid, timeframe)
        if not parquet.is_file():
            raise PartitionMissingError(
                f"candle cache not prepared for {ds['symbol']} {timeframe} {label}; "
                f"run `build-candles` first")
        frames.append(pq.read_table(parquet, columns=list(CANDLE_COLUMNS)).to_pandas())
    frame = pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0]
    if start_ts is not None:
        frame = frame[frame["timestamp"] >= start_ts]
    if end_exclusive is not None:
        frame = frame[frame["timestamp"] < end_exclusive]
    frame = frame.sort_values("timestamp", kind="stable")
    # Cap to the most recent `cap` candles inside the window (bounded response).
    if len(frame) > cap:
        frame = frame.iloc[-cap:]
    frame = frame.reset_index(drop=True)
    if frame.empty:
        raise CoverageError(f"no cached candles for {source} {timeframe} in {start}..{end}")
    return frame


def _row_groups_in_range(pf, start_ts: pd.Timestamp | None,
                         end_exclusive: pd.Timestamp | None) -> list[int]:
    """Row-group indices whose timestamp statistics overlap ``[start, end)``."""
    meta = pf.metadata
    ts_index = 0  # timestamp is written first in ARROW_SCHEMA
    selected = []
    for rg in range(meta.num_row_groups):
        stats = meta.row_group(rg).column(ts_index).statistics
        if stats is None or not stats.has_min_max:
            selected.append(rg)
            continue
        rg_min = pd.Timestamp(stats.min).tz_localize("UTC") if pd.Timestamp(stats.min).tz is None else pd.Timestamp(stats.min)
        rg_max = pd.Timestamp(stats.max).tz_localize("UTC") if pd.Timestamp(stats.max).tz is None else pd.Timestamp(stats.max)
        if start_ts is not None and rg_max < start_ts:
            continue
        if end_exclusive is not None and rg_min >= end_exclusive:
            continue
        selected.append(rg)
    return selected


def load_ticks_window(
    source: str,
    *,
    symbol: str | None = None,
    start: str | None = None,
    end: str | None = None,
    limit: int = 500,
    root: str | Path | None = None,
) -> pd.DataFrame:
    """Bounded real BID/ASK tick window for execution visualization.

    Row-group pruned: only partitions/row groups whose timestamp statistics
    overlap ``[start, end)`` are read, and reading stops once ``limit`` rows
    (max ``MAX_TICK_WINDOW``) have been collected. Returns canonical
    ``timestamp, bid, ask`` (+ volumes) ordered chronologically — the exact
    cached ticks, never synthesized.
    """
    root = DEFAULT_CACHE_ROOT if root is None else root
    cap = max(1, min(int(limit), MAX_TICK_WINDOW))
    ds = _resolve_dataset(source, symbol=symbol, root=root)
    start_ts, end_exclusive = resolve_range(start, end)
    months = _months_between(start_ts, end_exclusive, ds.get("months", []))
    if not months:
        raise CoverageError(f"requested range {start}..{end} is outside cached coverage")
    cols = ["timestamp", "bid", "ask", "bid_volume", "ask_volume"]
    collected = []
    total = 0
    for label in months:
        year, month = (int(x) for x in label.split("-"))
        pid = PartitionId(ds["provider"], ds["symbol"], year, month)
        parquet = partition_parquet(root, pid)
        if not parquet.is_file():
            raise PartitionMissingError(f"required partition missing: {pid.label}")
        pf = pq.ParquetFile(parquet)
        for rg in _row_groups_in_range(pf, start_ts, end_exclusive):
            table = pf.read_row_group(rg, columns=cols)
            sub = table.to_pandas()
            if start_ts is not None:
                sub = sub[sub["timestamp"] >= start_ts]
            if end_exclusive is not None:
                sub = sub[sub["timestamp"] < end_exclusive]
            if len(sub):
                collected.append(sub)
                total += len(sub)
            if total >= cap:
                break
        if total >= cap:
            break
    if not collected:
        raise CoverageError(f"no cached ticks in {source} for {start}..{end}")
    frame = pd.concat(collected, ignore_index=True) if len(collected) > 1 else collected[0]
    if not frame["timestamp"].is_monotonic_increasing:
        frame = frame.sort_values("timestamp", kind="stable")
    frame = frame.reset_index(drop=True)
    if len(frame) > cap:
        frame = frame.iloc[:cap].reset_index(drop=True)
    return frame


def prepared_timeframes(source: str, *, symbol: str | None = None,
                        root: str | Path = DEFAULT_CACHE_ROOT) -> list[str]:
    ds = _resolve_dataset(source, symbol=symbol, root=root)
    out = []
    for tf in SUPPORTED_CANDLE_TIMEFRAMES:
        ok = True
        for label in ds.get("months", []):
            year, month = (int(x) for x in label.split("-"))
            if not candle_partition_parquet(root, PartitionId(ds["provider"], ds["symbol"], year, month), tf).is_file():
                ok = False
                break
        if ok and ds.get("months"):
            out.append(tf)
    return out


# --------------------------------------------------------------------------
# Benchmarks (honest, reproducible)
# --------------------------------------------------------------------------


def benchmark(data_dir: str | Path = DEFAULT_DATA_DIR, root: str | Path = DEFAULT_CACHE_ROOT,
              *, symbol: str = "XAUUSD", provider: str = "DUKAS", month: str = "2025-01",
              range_start: str = "2025-03-17", range_end: str = "2025-05-08",
              repeats: int = 2) -> dict:
    base = Path(data_dir)
    month_path = base / f"{symbol}_{provider}_{month.replace('-', '_')}_ticks.csv"
    out: dict = {"month": month, "range": [range_start, range_end], "repeats": repeats}
    if month_path.is_file():
        times = []
        rows = 0
        for _ in range(repeats):
            t0 = time.perf_counter()
            total = 0
            for frame in canonical_chunks(month_path):
                total += len(frame)
            rows = total
            times.append(time.perf_counter() - t0)
        out["csv_month_seconds"] = round(min(times), 3)
        out["csv_month_rows"] = rows
    try:
        ds = _resolve_dataset("cache:", symbol=symbol, root=root)
        pid_month = month.split("-")
        pid = PartitionId(ds["provider"], ds["symbol"], int(pid_month[0]), int(pid_month[1]))
        parquet = partition_parquet(root, pid)
        if parquet.is_file():
            times = []
            for _ in range(repeats):
                t0 = time.perf_counter()
                pq.read_table(parquet, columns=list(READ_COLUMNS))
                times.append(time.perf_counter() - t0)
            out["parquet_month_seconds"] = round(min(times), 3)
        months = _months_between(range_start, range_end, ds.get("months", []))
        out["partitions_opened"] = months
        # CSV range load: parse only the source files for the same months.
        csv_files = []
        for path, pid in discover_partitions(data_dir):
            if f"{pid.year}-{pid.month:02d}" in months:
                csv_files.append(path)
        if csv_files:
            times = []
            rows_csv = 0
            for _ in range(repeats):
                t0 = time.perf_counter()
                total = 0
                for path in csv_files:
                    for frame in canonical_chunks(path):
                        total += len(frame)
                rows_csv = total
                times.append(time.perf_counter() - t0)
            out["csv_range_seconds"] = round(min(times), 3)
            out["csv_range_rows"] = rows_csv
        times = []
        rows_pq = 0
        for _ in range(repeats):
            t0 = time.perf_counter()
            frame = load_ticks_frame("cache:", symbol=symbol, start=range_start, end=range_end, root=root)
            rows_pq = len(frame)
            times.append(time.perf_counter() - t0)
        out["parquet_range_seconds"] = round(min(times), 3)
        out["parquet_range_rows"] = rows_pq
    except DataEngineError as exc:
        out["parquet_error"] = str(exc)
    return out


# --------------------------------------------------------------------------
# Golden January parity (most important regression proof)
# --------------------------------------------------------------------------


def golden_january(data_dir: str | Path = DEFAULT_DATA_DIR, root: str | Path = DEFAULT_CACHE_ROOT) -> dict:
    """CSV vs Parquet parity for the verified January golden reference."""
    from src import run_backtest, run_request  # local import: avoid cycles

    # Resolve through discovery so the raw file may live in any folder layout
    # (flat or per-symbol); only the partition identity matters.
    january = [
        path for path, pid in discover_partitions(data_dir)
        if pid.symbol == "XAUUSD" and pid.year == 2025 and pid.month == 1
    ]
    if not january:
        raise RawDataMissingError(
            f"missing January 2025 XAUUSD source under {Path(data_dir)}"
        )
    csv_source = str(january[0])

    def run(source: str):
        req = run_request.BacktestRunRequest(
            strategy=run_request.StrategyRequest("candle_direction"),
            market=run_request.MarketRequest("XAUUSD", "15min", "2025-01-31", "2025-01-31", source),
            execution=run_request.ExecutionRequest(5, 10),
        )
        import tempfile
        info = run_backtest.execute_run(req, out=tempfile.mkdtemp(), report=False, public=tempfile.mkdtemp())
        r = info["result"]
        return {
            "ticks": r.data_quality.tick_count, "candles": r.data_quality.candle_count,
            "trades": r.metrics.total_trades, "pnl": round(r.metrics.net_pnl, 6),
            "rows": [(t.direction, str(t.signal_available_time), str(t.entry_time), t.entry_price,
                      str(t.exit_time), t.exit_price, t.exit_reason, round(t.pnl_price, 6)) for t in r.trades],
        }

    csv = run(csv_source)
    parquet = run("cache:XAUUSD")
    parity = (
        csv["ticks"] == parquet["ticks"] and csv["candles"] == parquet["candles"]
        and csv["trades"] == parquet["trades"] and csv["pnl"] == parquet["pnl"]
        and csv["rows"] == parquet["rows"]
    )
    return {
        "csv": {k: v for k, v in csv.items() if k != "rows"},
        "parquet": {k: v for k, v in parquet.items() if k != "rows"},
        "reference": {"ticks": 322548, "candles": 88, "trades": 7, "pnl": -15.82},
        "parity": parity,
        "csv_matches_reference": csv["ticks"] == 322548 and csv["trades"] == 7 and abs(csv["pnl"] - (-15.82)) < 1e-6,
    }


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _cmd_inventory(args) -> int:
    entries = inventory(args.data)
    _ensure_writable(cache_root(args.root))
    _atomic_write_text(Path(args.root) / "raw_inventory.json", json.dumps(
        {"schema_version": DATA_CACHE_SCHEMA_VERSION, "files": entries}, indent=2, ensure_ascii=False))
    for e in entries:
        print(f"{e['relative_path']:38} {e['rows']:>10}  {e['sha256'][:12]}  {e['first_point']} -> {e['last_point']}")
    print(f"files: {len(entries)}")
    return 0


def _cmd_build(args) -> int:
    results = build_all(
        args.data, args.root, month=args.month, force=args.force,
        allow_source_change=getattr(args, "accept_source_change", False),
    )
    built = sum(1 for r in results if r["status"] == "BUILT")
    hit = sum(1 for r in results if r["status"] == "CACHE HIT")
    for r in results:
        print(f"{r['status']:9} {r['partition']}  rows={r['rows']}")
    print(f"built={built} cache_hits={hit} total={len(results)}")
    return 0


def _cmd_verify(args) -> int:
    results = verify_all(args.data, args.root, month=args.month)
    ok = True
    for r in results:
        good = r["row_count_parity"] and r["value_parity"] and r["order_parity"]
        ok &= good
        print(f"{'PASS' if good else 'FAIL'} {r['partition']} rows {r['source_rows']}/{r['derived_rows']} "
              f"value={r['value_parity']} order={r['order_parity']}")
    print(f"partitions: {len(results)} all_ok={ok}")
    return 0 if ok else 1


def _cmd_inspect(args) -> int:
    s = logical_summary(args.root, args.data)
    if not s.get("logical_dataset_id"):
        print("no derived datasets in cache")
        return 0
    print(f"Logical dataset: {s['logical_dataset_id']}  ({s['symbol']} / {s['provider']} / {s['data_type']} / {s['timezone']})")
    print(f"  raw inventory files:            {s['raw_inventory_files']}")
    print(f"  authoritative monthly parts:    {s['authoritative_monthly_partitions']}")
    print(f"      2025:                       {s['monthly_2025_partitions']}")
    print(f"      2026:                       {s['monthly_2026_partitions']} (partial month)")
    print(f"  rows 2025 (authoritative):      {s['rows_2025']:,}")
    print(f"  rows 2026 (authoritative):      {s['rows_2026']:,}")
    print(f"  total logical rows:             {s['total_logical_rows']:,}")
    print(f"  aggregate reference files:      {s['aggregate_reference_files']}")
    print(f"  aggregate rows (NOT included):  {s['aggregate_rows']:,}")
    print(f"  ALL missing ticks vs 2025:      {s['aggregate_missing_ticks_vs_monthly_2025']}")
    print(f"  coverage:                       {s['coverage_start']} -> {s['coverage_end']}")
    print(f"  partial months:                 {s['partial_months']}")
    prepared = prepared_timeframes("cache:", symbol=s.get("symbol"), root=args.root)
    print(f"  prepared candle timeframes:     {prepared if prepared else 'none (run build-candles)'}")
    return 0


def _cmd_bench(args) -> int:
    print(json.dumps(benchmark(args.data, args.root, month=args.month,
                               range_start=args.start, range_end=args.end), indent=2))
    return 0


def _cmd_golden(args) -> int:
    print(json.dumps(golden_january(args.data, args.root), indent=2))
    return 0


def _cmd_build_candles(args) -> int:
    results = build_all_candles(args.data, args.root, timeframe=args.timeframe, force=args.force)
    built = sum(1 for r in results if r["status"] == "BUILT")
    hit = sum(1 for r in results if r["status"] == "CACHE HIT")
    print(f"candles built={built} cache_hits={hit} total={len(results)}")
    return 0


def _cmd_verify_candles(args) -> int:
    results = verify_all_candles(args.data, args.root, timeframe=args.timeframe)
    ok = True
    for r in results:
        good = r["row_count_parity"] and r["value_parity"]
        ok &= good
    print(f"candle partitions verified={sum(1 for r in results if r['row_count_parity'] and r['value_parity'])}/{len(results)} all_ok={ok}")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(prog="python -m src.data_engine",
                                     description="Quant Backtest Lab derived tick store (read-only raw CSV)")
    parser.add_argument("--data", default=str(DEFAULT_DATA_DIR), help="raw CSV directory (read-only)")
    parser.add_argument("--root", default=str(DEFAULT_CACHE_ROOT), help="derived cache root (outside data/)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_inv = sub.add_parser("inventory", help="read-only inventory of raw source files")
    p_inv.set_defaults(fn=_cmd_inventory)

    p_build = sub.add_parser("build", help="build/update derived Parquet partitions")
    p_build.add_argument("--month", help="only this YYYY-MM")
    p_build.add_argument("--all", action="store_true", help="build every available partition (default)")
    p_build.add_argument("--force", action="store_true", help="rebuild even if unchanged")
    p_build.add_argument(
        "--accept-source-change", action="store_true", dest="accept_source_change",
        help="allow rebuilding a partition whose raw file changed (records the new fingerprint)",
    )
    p_build.set_defaults(fn=_cmd_build)

    p_verify = sub.add_parser("verify", help="exact CSV<->Parquet partition verification")
    p_verify.add_argument("--month", help="only this YYYY-MM")
    p_verify.set_defaults(fn=_cmd_verify)

    p_inspect = sub.add_parser("inspect", help="read-only logical dataset inspection")
    p_inspect.set_defaults(fn=_cmd_inspect)

    p_bench = sub.add_parser("bench", help="honest CSV vs Parquet load benchmark")
    p_bench.add_argument("--month", default="2025-01")
    p_bench.add_argument("--start", default="2025-03-17")
    p_bench.add_argument("--end", default="2025-05-08")
    p_bench.set_defaults(fn=_cmd_bench)

    p_golden = sub.add_parser("golden", help="January golden CSV vs Parquet parity")
    p_golden.set_defaults(fn=_cmd_golden)

    p_bc = sub.add_parser("build-candles", help="build/update derived candle partitions")
    p_bc.add_argument("--timeframe", help="only this timeframe (default: all supported)")
    p_bc.add_argument("--force", action="store_true")
    p_bc.set_defaults(fn=_cmd_build_candles)

    p_vc = sub.add_parser("verify-candles", help="exact on-the-fly vs cached candle parity")
    p_vc.add_argument("--timeframe", help="only this timeframe (default: all supported)")
    p_vc.set_defaults(fn=_cmd_verify_candles)

    args = parser.parse_args(argv)
    try:
        return args.fn(args)
    except DataEngineError as exc:
        print(f"ERROR [{exc.code}]: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

