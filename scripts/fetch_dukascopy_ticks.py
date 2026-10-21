#!/usr/bin/env python3
"""Fetch real tick data from Dukascopy's public historical feed.

Dukascopy publishes one LZMA-compressed ``.bi5`` file per instrument, hour
and day. Each decompressed file is a sequence of 20-byte tick records:

    offset  0  int32 BE   milliseconds since the start of the hour (UTC)
    offset  4  int32 BE   ask price, integer scaled by the instrument point
    offset  8  int32 BE   bid price, integer scaled by the instrument point
    offset 12  float32 BE ask volume (millions of the base unit)
    offset 16  float32 BE bid volume (millions of the base unit)

The repo's own reader (``src/data_engine.py``) expects a *flat* Dukascopy CSV:

    YYYY.MM.DD,HH:MM:SS.mmm,bid,ask,bid_volume,ask_volume

with the wall-clock treated as UTC and no shifting. This script downloads the
hours, decodes them and writes exactly that shape, preserving source order.

The public feed is heavily rate limited (it answers 503 under load), so every
hour is retried with backoff and only a payload that decodes cleanly is kept.

Examples:
    # one trading day of XAUUSD into data/
    python scripts/fetch_dukascopy_ticks.py XAUUSD 2024 12 04 --out data

    # a specific hour range only
    python scripts/fetch_dukascopy_ticks.py EURUSD 2025 01 06 --hours 8-16 --out data

    # decode .bi5 files you already have on disk
    python scripts/fetch_dukascopy_ticks.py --decode-only /tmp/duka --year 2024 --month 12 --day 04
"""
from __future__ import annotations

import argparse
import lzma
import struct
import time
import urllib.error
import urllib.request
from pathlib import Path

URL = ("https://datafeed.dukascopy.com/datafeed/{symbol}/{year:04d}/00/{day:02d}/{hour:02d}h_ticks.bi5")
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

#: Dukascopy encodes prices as integers scaled by an instrument-specific
#: "point" factor:
#:   * FX majors (EURUSD, GBPUSD, ...) -> 5 decimals -> point 1e-5
#:   * Metals (XAUUSD, XAGUSD, ...)   -> 3 decimals -> point 1e-3
#:   * JPY crosses (USDJPY, ...)      -> 3 decimals -> point 1e-3
#: Getting this wrong scales the whole price series (e.g. gold at 26.40
#: instead of 2640.58), so the point is always resolved explicitly and the
#: result is sanity-checked against a plausible price band.
POINTS = {
    "EURUSD": 1e-5, "GBPUSD": 1e-5, "AUDUSD": 1e-5, "NZDUSD": 1e-5,
    "USDCAD": 1e-5, "USDCHF": 1e-5, "EURGBP": 1e-5, "EURJPY": 1e-3,
    "GBPJPY": 1e-3, "USDJPY": 1e-3, "XAUUSD": 1e-3, "XAGUSD": 1e-3,
}

#: Rough sanity band per symbol (bid price must land inside it).
PRICE_BANDS = {
    "XAUUSD": (100.0, 5000.0), "XAGUSD": (5.0, 200.0),
    "EURUSD": (0.9, 1.4), "GBPUSD": (1.0, 1.6), "USDJPY": (100.0, 200.0),
}


def _valid_bi5(path: Path) -> bool:
    """A usable .bi5 is LZMA-compressed and decodes to a multiple of 20 bytes."""
    try:
        raw = lzma.decompress(path.read_bytes())
    except (lzma.LZMAError, OSError):
        return False
    return bool(raw) and len(raw) % 20 == 0


def download_hour(symbol: str, year: int, day: int, hour: int,
                  out_dir: Path, attempts: int = 12) -> Path | None:
    target = out_dir / f"{year:04d}_{day:02d}_{hour:02d}h.bi5"
    if target.is_file() and _valid_bi5(target):
        return target
    url = URL.format(symbol=symbol, year=year, day=day, hour=hour)
    delay = 2.0
    for attempt in range(1, attempts + 1):
        tmp = out_dir / f".tmp_{hour:02d}h_{attempt}.bi5"
        try:
            request = urllib.request.Request(
                url, headers={"User-Agent": USER_AGENT, "Referer": "https://www.dukascopy.com/"})
            with urllib.request.urlopen(request, timeout=30) as response:
                blob = response.read()
            tmp.write_bytes(blob)
            if _valid_bi5(tmp):
                tmp.replace(target)
                return target
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            print(f"  [{hour:02d}h] {type(exc).__name__}: {exc} (attempt {attempt})", flush=True)
        finally:
            if tmp.exists():
                tmp.unlink()
        time.sleep(delay)
        delay = min(delay * 1.6, 12.0)
    return None


def decode_hour(path: Path, point: float, hour: int) -> list[str]:
    """Decode one hourly .bi5 file into CSV rows.

    Dukascopy's ``ms`` field is the offset *within* the hour (0 .. 3_599_982),
    so the absolute time-of-day is ``hour + ms``. Getting this wrong collapses
    every hour of the day onto 00:00:00.
    """
    raw = lzma.decompress(path.read_bytes())
    rows: list[str] = []
    for i in range(len(raw) // 20):
        chunk = raw[i * 20:(i + 1) * 20]
        ms, ask, bid = struct.unpack(">iii", chunk[0:12])
        bid_volume, ask_volume = struct.unpack(">ff", chunk[12:20])
        total_ms = hour * 3_600_000 + ms
        minutes, rem = divmod(total_ms, 60_000)
        seconds, millis = divmod(rem, 1000)
        rows.append(
            f"{minutes // 60:02d}:{minutes % 60:02d}:{seconds:02d}.{millis:03d},"
            f"{bid * point:.5f},{ask * point:.5f},"
            f"{bid_volume * 1e6:.3f},{ask_volume * 1e6:.3f}"
        )
    return rows


def resolve_point(symbol: str, explicit: float) -> float:
    if explicit:
        return explicit
    point = POINTS.get(symbol.upper())
    if not point:
        raise SystemExit(
            f"unknown point factor for {symbol!r}; pass --point explicitly "
            f"(FX majors 1e-5, metals and JPY crosses 1e-3)")
    return point


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("symbol", nargs="?", help="e.g. XAUUSD, EURUSD")
    ap.add_argument("year", nargs="?", type=int)
    ap.add_argument("month", nargs="?", type=int)
    ap.add_argument("day", nargs="?", type=int)
    ap.add_argument("--hours", default="0-23", help="hour range, e.g. 8-16 or 0,1,2")
    ap.add_argument("--out", default="data", help="output directory for the CSV")
    ap.add_argument("--provider", default="DUKAS", help="provider label in the file name")
    ap.add_argument("--point", type=float, default=0.0, help="price point factor override")
    ap.add_argument("--decode-only", default="",
                    help="decode .bi5 files from this directory instead of downloading")
    args = ap.parse_args()

    if args.decode_only:
        source = Path(args.decode_only)
        symbol = (args.symbol or "XAUUSD").upper()
        if not (args.year and args.month and args.day):
            ap.error("year, month and day are required with --decode-only")
        year, month, day = args.year, args.month, args.day
    else:
        if not (args.symbol and args.year and args.month and args.day):
            ap.error("symbol, year, month and day are required (or use --decode-only)")
        source = Path(args.out) / ".bi5-cache"
        symbol = args.symbol.upper()
        year, month, day = args.year, args.month, args.day

    point = resolve_point(symbol, args.point)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    if not args.decode_only:
        source.mkdir(parents=True, exist_ok=True)
        if "-" in args.hours:
            first, _, last = args.hours.partition("-")
            hours = range(int(first), int(last) + 1)
        else:
            hours = [int(h) for h in args.hours.split(",") if h.strip()]
        for hour in hours:
            path = download_hour(symbol, year, day, hour, source)
            if path is None:
                print(f"  [{hour:02d}h] gave up after retries", flush=True)
            else:
                print(f"  [{hour:02d}h] ok", flush=True)

    files = sorted(source.glob("*.bi5"))
    if not files:
        print(f"no .bi5 files found in {source}")
        return 1

    stamp = f"{year:04d}.{month:02d}.{day:02d}"
    lines: list[str] = []
    hours_seen: list[int] = []
    for path in files:
        hour = int(path.stem.split("_")[-1].rstrip("h"))
        try:
            rows = decode_hour(path, point, hour)
        except lzma.LZMAError:
            print(f"  skip (not a valid LZMA stream): {path.name}")
            continue
        if not rows:
            continue
        hours_seen.append(hour)
        lines.extend(f"{stamp},{row}" for row in rows)

    if not lines:
        print("no decodable ticks")
        return 1

    first_bid = float(lines[0].split(",")[2])
    band = PRICE_BANDS.get(symbol)
    if band and not (band[0] <= first_bid <= band[1]):
        raise SystemExit(
            f"price sanity check failed for {symbol}: bid {first_bid} outside "
            f"expected band {band}. Wrong point factor (used {point:g})?")

    out_file = out_dir / f"{symbol}_{args.provider}_{year:04d}_{month:02d}_ticks.csv"
    out_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    size_mb = out_file.stat().st_size / 1024 / 1024
    print(f"hours: {sorted(hours_seen)}")
    print(f"ticks: {len(lines)}")
    print(f"price band ok: {symbol} bid {first_bid} (point {point:g})")
    print(f"wrote {out_file} ({size_mb:.1f} MB)")
    print()
    print("Next:")
    print("  python -m src.data_engine build --all")
    print("  python -m src.data_engine build-candles")
    print("  python -m src.data_engine verify")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
