"""One-command local launcher for Quant Backtest Lab.

Starts the Python bridge and the frontend dev server, waits until both are
ready, then opens the browser. Handles missing environment, busy ports, startup
failure and Ctrl+C cleanup (no orphan processes).

Usage:
    python scripts/start_quant_lab.py
or via START_QUANT_LAB.bat at the repo root.
"""

from __future__ import annotations

import atexit
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "Quant-Backtest-Lab"
BRIDGE_PORT = 8765
FRONTEND_PORT = 5173
APP_URL = f"http://127.0.0.1:{FRONTEND_PORT}/"
HEALTH_URL = f"http://127.0.0.1:{BRIDGE_PORT}/health"

_bridge: subprocess.Popen | None = None
_frontend: subprocess.Popen | None = None


def log(msg: str) -> None:
    print(f"[quant-lab] {msg}", flush=True)


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def http_ok(url: str, timeout: float = 1.5) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return 200 <= r.status < 500
    except Exception:
        return False


def wait_http(url: str, seconds: float) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        if http_ok(url):
            return True
        # abort early if the child died
        for proc in (_bridge, _frontend):
            if proc is not None and proc.poll() is not None and proc is not _frontend:
                return False
        time.sleep(0.5)
    return False


def wait_port(port: int, seconds: float) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        if port_in_use(port):
            return True
        if _frontend is not None and _frontend.poll() is not None:
            return False
        time.sleep(0.5)
    return False


def cleanup() -> None:
    for proc, name in ((_frontend, "frontend"), (_bridge, "bridge")):
        if proc is None or proc.poll() is not None:
            continue
        log(f"stopping {name}…")
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                proc.terminate()
        except Exception:
            pass
    time.sleep(0.3)


def main() -> int:
    log(f"repo: {ROOT}")

    # --- environment checks -------------------------------------------------
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    try:
        import pandas  # noqa: F401
        import src.bridge  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        log("Python environment is missing required packages (pandas / bridge).")
        log(f"  detail: {exc}")
        log("Install them, e.g.  python -m pip install -r requirements.txt")
        return 1
    log(f"python: {sys.executable}")

    node = shutil.which("node")
    if node is None:
        log("Node.js was not found on PATH. Install Node 18+ to run the frontend.")
        return 1
    if not (FRONTEND / "node_modules").is_dir():
        log("Frontend dependencies are missing (Quant-Backtest-Lab/node_modules).")
        log("Run  npm install  (or pnpm install) inside Quant-Backtest-Lab first.")
        return 1

    # --- port checks --------------------------------------------------------
    if port_in_use(BRIDGE_PORT):
        log(f"Port {BRIDGE_PORT} is already in use — is another bridge running?")
        log("Stop it, or open the app directly if it is already up.")
        if http_ok(HEALTH_URL):
            log("A bridge is already responding; opening the app only.")
            webbrowser.open(APP_URL)
            return 0
        return 1
    if port_in_use(FRONTEND_PORT):
        log(f"Port {FRONTEND_PORT} is already in use — is the dev server already running?")
        log(f"Opening {APP_URL}.")
        webbrowser.open(APP_URL)
        return 0

    atexit.register(cleanup)

    # --- start bridge -------------------------------------------------------
    bridge_log = open(ROOT / ".qbl-bridge.log", "w", encoding="utf-8")
    log("starting Python bridge…")
    _bridge = subprocess.Popen(
        [sys.executable, "-m", "src.bridge", "--port", str(BRIDGE_PORT)],
        cwd=str(ROOT), stdout=bridge_log, stderr=subprocess.STDOUT,
    )
    if not wait_http(HEALTH_URL, 40):
        log("Python bridge failed to become ready. See .qbl-bridge.log")
        cleanup()
        return 1
    log("Python bridge: Connected")

    # --- start frontend -----------------------------------------------------
    dev_log = open(ROOT / ".qbl-dev.log", "w", encoding="utf-8")
    log("starting frontend (this can take a few seconds)…")
    _frontend = subprocess.Popen(
        [node, "scripts/run-framework.mjs", "dev"],
        cwd=str(FRONTEND), stdout=dev_log, stderr=subprocess.STDOUT,
    )
    if not wait_port(FRONTEND_PORT, 150):
        log("Frontend failed to become ready. See .qbl-dev.log")
        cleanup()
        return 1
    log("frontend: ready")

    log(f"opening {APP_URL}")
    webbrowser.open(APP_URL)

    log("Quant Backtest Lab is running. Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
            if _bridge.poll() is not None:
                log("Python bridge exited unexpectedly.")
                break
            if _frontend.poll() is not None:
                log("Frontend exited unexpectedly.")
                break
    except KeyboardInterrupt:
        log("shutting down…")
    finally:
        cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
