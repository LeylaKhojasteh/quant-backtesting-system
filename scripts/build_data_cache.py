"""Convenience wrapper for the Data Engine CLI.

Examples:
    python scripts/build_data_cache.py inventory
    python scripts/build_data_cache.py build --all
    python scripts/build_data_cache.py build --month 2025-01
    python scripts/build_data_cache.py verify
    python scripts/build_data_cache.py inspect
    python scripts/build_data_cache.py golden
    python scripts/build_data_cache.py bench
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import data_engine  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(data_engine.main(sys.argv[1:]))
