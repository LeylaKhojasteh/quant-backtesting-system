#!/usr/bin/env python3
"""Install the bundled sample dataset into ``data/`` so a newcomer can run a
real backtest without supplying their own market data.

The sample is one real trading day of XAUUSD tick data taken from Dukascopy
(2024-12-04, UTC). It is committed under ``data/sample/`` and copied into
``data/`` on demand, because ``data/`` itself is git-ignored (raw market data
is machine-specific) and the engine only reads ``data/*.csv`` — it does not
recurse into subdirectories.

Usage:
    python scripts/install_sample_data.py            # copy the sample in
    python scripts/install_sample_data.py --list     # show what is available
    python scripts/install_sample_data.py --remove   # remove it again
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SAMPLE_DIR = ROOT / "data" / "sample"
DATA_DIR = ROOT / "data"


def samples() -> list[Path]:
    if not SAMPLE_DIR.is_dir():
        return []
    return sorted(SAMPLE_DIR.glob("*.csv"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--list", action="store_true", help="list bundled samples and exit")
    ap.add_argument("--remove", action="store_true", help="remove previously installed samples")
    args = ap.parse_args(argv)

    found = samples()
    if args.list:
        if not found:
            print("no bundled sample data found")
            return 0
        for path in found:
            size_kb = path.stat().st_size / 1024
            print(f"{path.name}  ({size_kb:.0f} KB)")
        return 0

    if args.remove:
        removed = 0
        for path in found:
            target = DATA_DIR / path.name
            if target.exists():
                target.unlink()
                removed += 1
                print(f"removed {target.name}")
        print(f"removed {removed} file(s)")
        return 0

    if not found:
        print(
            "no bundled sample data found in data/sample/.\n"
            "The sample is committed to the repository; if you are reading this "
            "from a checkout, run `git pull` to fetch it.",
            file=sys.stderr,
        )
        return 1

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for path in found:
        target = DATA_DIR / path.name
        if target.exists():
            print(f"already present: {target.name} (left untouched — raw data is immutable)")
            continue
        shutil.copy2(path, target)
        size_kb = target.stat().st_size / 1024
        print(f"installed {target.name}  ({size_kb:.0f} KB)")

    print()
    print("Next:")
    print("  1) build the derived cache:")
    print("     .venv\\Scripts\\python.exe -m src.data_engine build --all")
    print("  2) start the lab:")
    print("     START_QUANT_LAB.bat   (or: python scripts/start_quant_lab.py)")
    print("  3) pick the sample dataset, set the date range to 2024-12-04, run.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
