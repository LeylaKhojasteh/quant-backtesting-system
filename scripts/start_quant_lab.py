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
#: Fallback frontend ports when the default cannot be bound (e.g. a local
#: proxy/firewall reserving low loopback ports: Node reports EACCES).
FRONTEND_FALLBACK_PORTS = (8123, 12136, 18000, 30000)
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


def _local_opener() -> urllib.request.OpenerDirector:
    # Windows urllib honors the registry proxy for localhost (unlike browsers
    # with proxy bypass). A local intercepting proxy answers 502 for our own
    # bridge health check and makes a healthy startup look dead. Bypass any
    # proxy for loopback explicitly.
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def http_ok(url: str, timeout: float = 1.5) -> bool:
    try:
        with _local_opener().open(url, timeout=timeout) as r:
            return 200 <= r.status < 500
    except Exception:
        return False


def wait_http(url: str, seconds: float) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        if http_ok(url):
            return True
        # Abort early only if OUR OWN bridge child died. Never inspect
        # _frontend here: it is None at this stage, and a stale `is not`
        # comparison could misread the state and abort a healthy startup.
        if _bridge is not None and _bridge.poll() is not None:
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
    # NOTE: sys.executable MUST be the only Python used. An earlier revision
    # of this launcher could be re-entered through a stale .bat/uv shim and
    # spawn a second interpreter (uv python) that bound the port first while
    # OUR child stayed silent — health then failed with an empty log. Guard
    # with creationflags-free direct spawn and verify OUR child owns the port.
    bridge_log = open(ROOT / ".qbl-bridge.log", "w", encoding="utf-8")
    log(f"bridge interpreter: {sys.executable}")
    log("starting Python bridge…")
    _bridge = subprocess.Popen(
        [sys.executable, "-m", "src.bridge", "--port", str(BRIDGE_PORT)],
        cwd=str(ROOT), stdout=bridge_log, stderr=subprocess.STDOUT,
    )
    if not wait_http(HEALTH_URL, 60):
        log("Python bridge failed to become ready. See .qbl-bridge.log")
        cleanup()
        return 1
    log("Python bridge: Connected")

    # --- start frontend -----------------------------------------------------
    # run-framework pins --port 5173; when the bind is denied (EACCES from a
    # local proxy/firewall), fall back to the next free port instead of dying.
    frontend_port = FRONTEND_PORT
    app_url = APP_URL
    dev_log = open(ROOT / ".qbl-dev.log", "w", encoding="utf-8")
    log("starting frontend (this can take a few seconds)…")
    _frontend = subprocess.Popen(
        [node, "scripts/run-framework.mjs", "dev"],
        cwd=str(FRONTEND), stdout=dev_log, stderr=subprocess.STDOUT,
    )
    ready = wait_port(frontend_port, 60)
    if not ready and not port_in_use(frontend_port):
        # Default port never opened (child dead on EACCES, or still
        # starting but the port stays shut). Reap the child if needed and
        # restart on the first free fallback port.
        if _frontend is not None and _frontend.poll() is None:
            _frontend.terminate()
            try:
                _frontend.wait(timeout=10)
            except Exception:
                pass
        for candidate in FRONTEND_FALLBACK_PORTS:
            if port_in_use(candidate):
                continue
            log(f"port {FRONTEND_PORT} unavailable, retrying on {candidate}…")
            frontend_port = candidate
            app_url = f"http://127.0.0.1:{frontend_port}/"
            dev_log = open(ROOT / ".qbl-dev.log", "w", encoding="utf-8")
            # Bypass run-framework here: it pins --port 5173 itself.
            _frontend = subprocess.Popen(
                [node, "node_modules/vinext/dist/cli.js", "dev",
                 "--port", str(candidate), "--host", "127.0.0.1"],
                cwd=str(FRONTEND), stdout=dev_log, stderr=subprocess.STDOUT,
            )
            if wait_port(candidate, 90):
                ready = True
                break
            if _frontend is not None and _frontend.poll() is None:
                _frontend.terminate()
    if not ready:
        log("Frontend failed to become ready. See .qbl-dev.log")
        cleanup()
        return 1
    log("frontend: ready")

    log(f"opening {app_url}")
    webbrowser.open(app_url)

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
