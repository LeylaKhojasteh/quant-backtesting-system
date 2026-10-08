"""Central runtime configuration for Quant Backtest Lab.

One authoritative source for the release version and the default local paths,
each overridable via environment variables (QBL_*). Not an enterprise config
framework — just enough to avoid scattered hard-coded paths.
"""

import os
from pathlib import Path

VERSION = "1.0.0"


def _path(env: str, default: str) -> Path:
    return Path(os.environ.get(env, default))


BRIDGE_HOST = os.environ.get("QBL_BRIDGE_HOST", "127.0.0.1")
BRIDGE_PORT = int(os.environ.get("QBL_BRIDGE_PORT", "8765"))
FRONTEND_HOST = os.environ.get("QBL_FRONTEND_HOST", "127.0.0.1")
FRONTEND_PORT = int(os.environ.get("QBL_FRONTEND_PORT", "5173"))

DATA_DIR = _path("QBL_DATA_DIR", "data")
CACHE_ROOT = _path("QBL_CACHE_ROOT", "cache/market_data")
RUNS_DIR = _path("QBL_RUNS_DIR", "runs")
STRATEGY_DIR = _path("QBL_STRATEGY_DIR", "strategies")
STUDIES_DIR = _path("QBL_STUDIES_DIR", "studies")
WALKFORWARDS_DIR = _path("QBL_WALKFORWARDS_DIR", "walkforwards")
MONTECARLO_DIR = _path("QBL_MONTECARLO_DIR", "montecarlo")
PUBLIC_DIR = _path("QBL_PUBLIC_DIR", "Quant-Backtest-Lab/public")
