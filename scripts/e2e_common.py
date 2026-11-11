"""Shared real-browser E2E helpers for the Quant Backtest Lab.

Drives the installed Chrome over the DevTools Protocol (stdlib +
``websocket-client`` only — no playwright/selenium dependency) against
the running dev server. Product E2E scripts import the shared harness
from this module; archived runs are the only data source.
"""

import argparse
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

import websocket  # websocket-client (already in .venv)

# Python's urllib reads the Windows registry proxy settings (unlike WinHTTP),
# which can route 127.0.0.1 through a proxy and break the CDP handshake.
# Force direct connections for localhost.
os.environ.setdefault("no_proxy", "127.0.0.1,localhost")
os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")
urllib.request.install_opener(urllib.request.build_opener(urllib.request.ProxyHandler({})))

# The page renders chart glyphs (e.g. â–²/â–¼); keep the report encoding-safe on
# legacy Windows consoles instead of crashing mid-run.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CHROME_CANDIDATES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
)

# Distinguishing archived fixtures (runs/run_0001/result.json).
ARCH_ENTRY = "2,626.08"      # trade 1 entry_price
ARCH_NET_PNL = "21.11"       # metrics.net_pnl
ARCH_ENGINE = "Python Bridge:"
ARCH_TICKS = "240,867"       # data_quality.tick_count

def _find_chrome() -> str:
    for c in CHROME_CANDIDATES:
        if Path(c).exists():
            return c
    raise RuntimeError("Chrome binary not found")


class CDP:
    """Minimal synchronous CDP client (Runtime/DOM/Page/Log/Network)."""

    def __init__(self, ws_url: str):
        self.ws = websocket.create_connection(ws_url, timeout=30)
        self._id = 0
        self.console_errors: list = []
        self.failed_requests: list = []
        # Raw JSON bodies of POST /runs requests (for e2e contract assertions).
        self.run_payloads: list = []

    def call(self, method: str, params: dict | None = None, timeout: int = 30):
        self._id += 1
        mid = self._id
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        end = time.time() + timeout
        while time.time() < end:
            # Re-arm the read timeout each iteration so a prior pump() with a
            # short window cannot starve a long awaitPromise evaluation.
            self.ws.settimeout(max(0.2, min(2.0, end - time.time())))
            try:
                msg = json.loads(self.ws.recv())
            except websocket.WebSocketTimeoutException:
                continue
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"CDP {method}: {msg['error']}")
                return msg.get("result", {})
            self._drain_event(msg)
        raise TimeoutError(f"CDP {method} timed out")

    def _drain_event(self, msg: dict) -> None:
        m = msg.get("method", "")
        p = msg.get("params", {})
        if m == "Runtime.consoleAPICalled" and p.get("type") == "error":
            self.console_errors.append(" ".join(
                str(a.get("value", a.get("description", "")))
                for a in p.get("args", [])))
        elif m == "Runtime.exceptionThrown":
            self.console_errors.append(str(p.get("exceptionDetails", {}))[:500])
        elif m == "Log.entryAdded" and p.get("entry", {}).get("level") == "error":
            entry = p["entry"]
            url = entry.get("url", "")
            text = entry.get("text", "")[:500]
            self.console_errors.append((f"{url} :: {text}" if url else text))
        elif m == "Network.loadingFailed":
            self.failed_requests.append(p.get("errorText", "?") + " " + p.get("blockedReason", ""))
        elif m == "Network.requestWillBeSent":
            req = p.get("request", {})
            if req.get("method") == "POST" and str(req.get("url", "")).endswith("/runs"):
                self.run_payloads.append(req.get("postData", ""))

    def pump(self, seconds: float = 1.0) -> None:
        self.ws.settimeout(seconds)
        end = time.time() + seconds
        while time.time() < end:
            try:
                msg = json.loads(self.ws.recv())
            except Exception:
                break
            self._drain_event(msg)

    def evaluate(self, expression: str, await_promise: bool = True, timeout: int = 30):
        r = self.call("Runtime.evaluate", {
            "expression": expression, "returnByValue": True,
            "awaitPromise": await_promise, "timeout": timeout * 1000}, timeout=timeout)
        if r.get("exceptionDetails"):
            raise RuntimeError("page eval: " + str(r["exceptionDetails"])[:400])
        return r.get("result", {}).get("value")


# --- test harness: serialization, ownership, deadlines ---------------------
#
# Safety rules implemented here (see docs/TEST_HARNESS.md):
#   * one E2E run at a time per repository (exclusive lock);
#   * the bridge port is the one the running frontend is pinned to, so it is
#     validated as free instead of being silently shared with a foreign process;
#   * Chrome gets a dynamically selected DevTools port and a unique temp profile;
#   * only processes owned by THIS run are terminated (PID/handle scoped);
#   * the temp profile created by this run is deleted, on success and failure;
#   * every run has an overall deadline after which owned processes are stopped.

#: The running frontend is pinned to this bridge port (``NEXT_PUBLIC_BRIDGE_URL``
#: or the built-in default), so a dynamic bridge port is not practical for UI
#: E2E. A busy port is a clear failure, never a silent attachment.
BRIDGE_DEFAULT_PORT = 8765

#: Whole-run deadline for one E2E script (seconds).
DEFAULT_E2E_DEADLINE_S = 900

#: Processes created by this run (ChromeSession instances and bridge Popen).
_OWNED: list = []
_LOCK_FH = None


class TestHarnessError(RuntimeError):
    """The harness refused to start, or had to stop the run."""


def _repo_tag() -> str:
    root = Path(__file__).resolve().parent.parent
    return hashlib.sha1(str(root).encode("utf-8")).hexdigest()[:12]


def test_lock_path() -> Path:
    """Repository-scoped lock file (kept out of the working tree)."""
    return Path(tempfile.gettempdir()) / f"qbl-e2e-{_repo_tag()}.lock"


def acquire_test_lock() -> None:
    """Take the exclusive E2E lock; fail fast if another run holds it."""
    global _LOCK_FH
    if _LOCK_FH is not None:
        return
    path = test_lock_path()
    fh = open(path, "a+b")
    try:
        fh.seek(0, os.SEEK_END)
        if fh.tell() == 0:
            fh.write(b"0")
            fh.flush()
        fh.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        fh.close()
        raise TestHarnessError(
            f"another E2E run is active (lock {path}); tests are serial â€” "
            f"wait for it to finish"
        ) from exc
    fh.seek(0)
    fh.truncate()
    fh.write(f"pid={os.getpid()}\n".encode("utf-8"))
    fh.flush()
    _LOCK_FH = fh
    print(f"[harness] lock acquired: {path} holding_pid={os.getpid()}")


def release_test_lock() -> None:
    global _LOCK_FH
    if _LOCK_FH is None:
        return
    path = _LOCK_FH.name
    try:
        if os.name == "nt":
            import msvcrt

            _LOCK_FH.seek(0)
            msvcrt.locking(_LOCK_FH.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(_LOCK_FH.fileno(), fcntl.LOCK_UN)
    except OSError as exc:
        print(f"[harness] lock release warning: {exc}")
    finally:
        _LOCK_FH.close()
        _LOCK_FH = None
        print(f"[harness] lock released: {path}")


def free_tcp_port() -> int:
    """Ask the OS for a currently free loopback port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def require_bridge_port_free(port: int = BRIDGE_DEFAULT_PORT) -> int:
    """Refuse to run when the bridge port is already taken.

    The frontend talks to a fixed bridge URL, so attaching this run to a
    foreign bridge (or to another test's bridge) would silently mix data.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError as exc:
            raise TestHarnessError(
                f"bridge port {port} is already in use ({exc}); stop the other "
                f"bridge/E2E run first â€” refusing to attach to it"
            ) from exc
    print(f"[harness] bridge port {port} is free")
    return port


class ChromeSession(subprocess.Popen):
    """A test-owned Chrome process with a unique profile.

    ``terminate()`` only touches this process (the PID/handle we created) and
    removes the temp profile afterwards. It never targets processes by name.
    """

    def __init__(self, argv, *, profile: Path, port: int):
        super().__init__(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.profile_dir = profile
        self.debug_port = port
        self._cleaned = False
        _OWNED.append(self)
        print(
            f"[harness] chrome pid={self.pid} devtools_port={port} profile={profile}"
        )

    def terminate(self) -> None:  # noqa: D401 - Popen API
        if self.poll() is None:
            try:
                super().terminate()
            except Exception as exc:  # noqa: BLE001 - cleanup must not throw
                print(f"[harness] chrome terminate warning (pid={self.pid}): {exc}")
            try:
                self.wait(timeout=10)
            except subprocess.TimeoutExpired:
                print(f"[harness] chrome pid={self.pid} ignored SIGTERM for 10s; "
                      f"force-terminating this PID only")
                try:
                    super().kill()
                    self.wait(timeout=5)
                except Exception as exc:  # noqa: BLE001
                    print(f"[harness] chrome force-terminate warning (pid={self.pid}): {exc}")
        print(f"[harness] chrome pid={self.pid} exit_code={self.poll()}")
        self.cleanup_profile()
        if self in _OWNED:
            _OWNED.remove(self)

    def cleanup_profile(self) -> None:
        if self._cleaned:
            return
        self._cleaned = True
        path = self.profile_dir
        try:
            if path.exists():
                shutil.rmtree(path)
                print(f"[harness] profile removed: {path}")
            else:
                print(f"[harness] profile already gone: {path}")
        except OSError as exc:
            # Report the exact path; never touch anything else.
            print(f"[harness] PROFILE CLEANUP FAILED: {path} ({exc}) â€” left in place")


def launch_chrome(preferred_port: int | None = None) -> ChromeSession:
    """Launch test-owned headless Chrome on a dynamically selected port."""
    port = preferred_port if (preferred_port and _port_is_free(preferred_port)) else free_tcp_port()
    profile = Path(tempfile.mkdtemp(prefix=f"qbl-e2e-{os.getpid()}-"))
    args = ["--headless=new", "--disable-gpu", "--no-sandbox",
            f"--remote-debugging-port={port}", f"--user-data-dir={profile}",
            # Chrome >= 111 rejects CDP websockets from unknown origins.
            "--remote-allow-origins=*",
            "--no-first-run", "--no-default-browser-check", "--disable-extensions",
            "--hide-scrollbars", "--window-size=1440,900", "about:blank"]
    return ChromeSession([_find_chrome()] + args, profile=profile, port=port)


def _port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def launch_bridge(argv, **kwargs) -> subprocess.Popen:
    """Start a test-owned bridge and register it for deadline cleanup."""
    proc = subprocess.Popen(argv, **kwargs)
    _OWNED.append(proc)
    print(f"[harness] bridge pid={proc.pid}")
    return proc


def stop_owned() -> None:
    """Terminate only the processes this run created (never by name)."""
    for obj in list(reversed(_OWNED)):
        if isinstance(obj, ChromeSession):
            # Handles profile cleanup and its own exit-code report.
            try:
                obj.terminate()
            except Exception as exc:  # noqa: BLE001 - cleanup is best effort
                print(f"[harness] cleanup warning: {exc}")
            continue
        try:
            if obj.poll() is None:
                obj.terminate()
                try:
                    obj.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    print(f"[harness] bridge pid={obj.pid} ignored terminate for 10s")
            print(f"[harness] bridge pid={obj.pid} exit_code={obj.poll()}")
        except Exception as exc:  # noqa: BLE001
            print(f"[harness] bridge cleanup warning (pid={obj.pid}): {exc}")
    _OWNED.clear()


def _stop_own_process_tree(exit_code: int) -> None:
    """Last resort: stop this run's own process tree (PID scoped)."""
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(os.getpid())],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    os._exit(exit_code)


def start_deadline(max_seconds: float) -> None:
    """Stop the run (and its owned processes) when the deadline expires."""
    def _watchdog() -> None:
        time.sleep(max_seconds)
        print(f"[harness] DEADLINE {int(max_seconds)}s exceeded â€” stopping owned processes")
        stop_owned()
        print("[harness] deadline cleanup finished; exit_code=124")
        _stop_own_process_tree(124)

    threading.Thread(target=_watchdog, name="qbl-e2e-deadline", daemon=True).start()
    print(f"[harness] deadline: {int(max_seconds)}s")


def e2e_main(main, max_seconds: float = DEFAULT_E2E_DEADLINE_S) -> int:
    """Run one E2E script under the lock and the overall deadline.

    Returns the script's exit code, or 2 when another run holds the lock.
    """
    try:
        acquire_test_lock()
    except TestHarnessError as exc:
        print(f"[harness] REFUSED: {exc}")
        return 2
    try:
        start_deadline(max_seconds)
        code = main()
        if not isinstance(code, int):
            code = 0
        print(f"[harness] main exit_code={code}")
        return code
    finally:
        stop_owned()
        release_test_lock()



def wait_debugger(port: int, timeout: int = 30) -> list:
    end = time.time() + timeout
    while time.time() < end:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=5) as r:
                return json.loads(r.read().decode())
        except Exception:
            time.sleep(0.5)
    raise RuntimeError("Chrome debugger not reachable")


# --- page probes -----------------------------------------------------------

OPEN_RUN_JS = """((needle) => new Promise(res => {
  const current = ((document.querySelector('.run-context-bar') || {}).dataset || {}).runId || '';
  if (current.includes(needle)) return res('CLICKED');
  const findOpen = () => {
    const rows = [...document.querySelectorAll('.lib-row')];
    const row = rows.find(r => (r.textContent || '').includes(needle));
    return row ? [...row.querySelectorAll('button')].find(b => (b.getAttribute('aria-label') || '').startsWith('Open ')) : null;
  };
  if (!document.querySelector('[role="dialog"]')) {
    const trigger = document.querySelector('.library-button');
    if (!trigger) return res('NO');
    trigger.click();
  }
  const runsTab = [...document.querySelectorAll('[role="tab"]')].find(t => (t.textContent || '').trim() === 'Runs');
  if (runsTab && runsTab.getAttribute('aria-selected') !== 'true') runsTab.click();
  const t0 = Date.now();
  const iv = setInterval(() => {
    const btn = findOpen();
    if (btn) { clearInterval(iv); btn.click(); res('CLICKED'); }
    else if (Date.now() - t0 > 25000) { clearInterval(iv); res('NO'); }
  }, 200);
}))"""

CLICK_ARCHIVED_JS = OPEN_RUN_JS

WAIT_TEXT_JS = """(([sel, needle]) => new Promise(res => {
  const ok = () => {
    const e = document.querySelector(sel);
    return !!e && (e.textContent || '').includes(needle);
  };
  if (ok()) return res(true);
  const t0 = Date.now();
  const iv = setInterval(() => {
    if (ok() || Date.now() - t0 > 25000) { clearInterval(iv); res(ok()); }
  }, 200);
}))"""
WAIT_BADGE_JS = """(want => new Promise(res => {
  const ok = () => (document.querySelector('.run-badge')?.textContent?.trim() ?? null) === want;
  if (ok()) return res(true);
  const t0 = Date.now();
  const iv = setInterval(() => {
    if (ok() || Date.now() - t0 > 25000) { clearInterval(iv); res(ok()); }
  }, 200);
}))"""

WAIT_SEL_JS = """((sel) => new Promise(res => {
  const ok = () => !!document.querySelector(sel);
  if (ok()) return res(true);
  const t0 = Date.now();
  const iv = setInterval(() => {
    if (ok() || Date.now() - t0 > 25000) { clearInterval(iv); res(ok()); }
  }, 200);
}))"""

TEXT_JS = """((sel) => {
  const e = document.querySelector(sel);
  return e ? e.textContent.replace(/\\s+/g, ' ').trim() : null;
})"""

CLICK_NAV_JS = """((label) => {
  const b = [...document.querySelectorAll('.navigation button')]
    .find(x => x.textContent.includes(label));
  if (!b) return 'NOT_FOUND';
  for (const t of ['pointerdown', 'mousedown', 'mouseup', 'click']) {
    b.dispatchEvent(new MouseEvent(t, { bubbles: true, cancelable: true, view: window }));
  }
  return 'CLICKED';
})"""

CLICK_RESULT_TAB_JS = """((label) => {
  const btns = [...document.querySelectorAll('.result-tabs button')];
  const b = btns.find(x => x.textContent.includes(label));
  if (!b) return 'NOT_FOUND:' + btns.map(x => x.textContent).join('|');
  for (const t of ['pointerdown', 'mousedown', 'mouseup', 'click']) {
    b.dispatchEvent(new MouseEvent(t, { bubbles: true, cancelable: true, view: window }));
  }
  return 'CLICKED';
})"""


def check(name: str, cond: bool, detail: str = "") -> bool:
    print(("PASS " if cond else "FAIL ") + name + (f" â€” {detail}" if detail else ""))
    return bool(cond)


def read_data_quality(cdp) -> str:
    """Data-quality diagnostics live in Data Explorer now; read them there."""
    cdp.evaluate(f"({CLICK_NAV_JS})('Data Explorer')")
    cdp.pump(0.4)
    txt = cdp.evaluate(f"({TEXT_JS})('.data-quality-grid')") or ""
    cdp.evaluate(f"({CLICK_NAV_JS})('Backtest')")
    cdp.pump(0.3)
    return txt


def has(texts, needle: str) -> bool:
    return any(needle.lower() in t.lower() for t in texts)


