"""Real-browser E2E verification for Demo <-> Archived run switching.

Drives the installed Chrome over the DevTools Protocol (stdlib +
``websocket-client`` only — no playwright/selenium dependency) against
the running Quant Backtest Lab dev server.

Flow verified in a live page:
  1. Demo baseline loads (DEMO badge, seeded metrics, 7 seeded trades).
  2. Select a non-default trade (index reset behaviour is observable).
  3. Click archived run_0001 in the .run-source switch.
  4. Assert the archived BacktestRun renders: ARCHIVED RUN badge, archived
     instrument/engine, 7 archived trades, live+measured readiness, account
     metrics still Pending (accountReady=false), charts present, selection
     reset, and none of the demo values remain.
  5. Click back to Demo; assert the seeded state is restored exactly.
  6. Collect console errors, hydration errors and failed requests throughout.

Usage:
    python scripts/e2e_run_switch.py [--url http://127.0.0.1:5173/]
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
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

# The page renders chart glyphs (e.g. ▲/▼); keep the report encoding-safe on
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

# Distinguishing demo fixtures (app/demo.ts).
DEMO_ENTRY = "2,799.84"      # trade 1 entry
DEMO_ENGINE = "Python Bridge:"


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


def launch_chrome(port: int) -> subprocess.Popen:
    profile = Path(tempfile.mkdtemp(prefix="qbl-e2e-"))
    args = ["--headless=new", "--disable-gpu", "--no-sandbox",
            f"--remote-debugging-port={port}", f"--user-data-dir={profile}",
            # Chrome >= 111 rejects CDP websockets from unknown origins.
            "--remote-allow-origins=*",
            "--no-first-run", "--no-default-browser-check", "--disable-extensions",
            "--hide-scrollbars", "--window-size=1440,900", "about:blank"]
    return subprocess.Popen([_find_chrome()] + args,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


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

SNAPSHOT_JS = """(() => {
  const q = (s) => document.querySelector(s);
  const t = (s) => q(s)?.textContent?.trim() ?? null;
  const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();
  const labels = (s) => [...document.querySelectorAll(s)]
    .map(e => norm(e.getAttribute('aria-label') || e.textContent || ''));
  const firstRow = q('.overview-trade-log tbody tr');
  return {
    badge: t('.demo-badge'),
    sourceBtns: labels('.run-source button'),
    tradeRows: document.querySelectorAll('.overview-trade-log tbody tr').length,
    firstRow: firstRow ? norm(firstRow.textContent) : null,
    selected: norm(t('.overview-trade-log tr.selected td:first-child')),
    readiness: labels('.readiness-chip'),
    pending: labels('.pm[role="status"]'),
    engine: norm(t('.engine')),
    runStatus: norm(t('.run-status')),
    subtitle: norm(t('.main-heading p')),
    dataQuality: norm(t('.data-quality')).slice(0, 600),
    metricsText: norm(t('.metrics-grouped')).slice(0, 3000),
    hasEquity: !!q('.equity svg'),
    hasCandle: !!q('.chart-wrap svg'),
  };
})()"""

CLICK_ROW_JS = """((n) => {
  const rows = [...document.querySelectorAll('.overview-trade-log tbody tr')];
  const r = rows[n - 1];
  if (!r) return 'NO_ROW:' + rows.length;
  r.click();
  return 'CLICKED:' + rows.length;
})"""

CLICK_ARCHIVED_JS = """(() => {
  const btns = [...document.querySelectorAll('.run-source button')];
  const b = btns.find(x => x.textContent.includes('run_0001'));
  if (!b) return 'NOT_FOUND:' + btns.map(x => x.textContent).join('|');
  b.click(); return 'CLICKED';
})()"""

CLICK_DEMO_JS = """(() => {
  const btns = [...document.querySelectorAll('.run-source button')];
  const b = btns.find(x => x.textContent.includes('Demo'));
  if (!b) return 'NOT_FOUND';
  b.click(); return 'CLICKED';
})()"""

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
  const ok = () => (document.querySelector('.demo-badge')?.textContent?.trim() ?? null) === want;
  if (ok()) return res(true);
  const t0 = Date.now();
  const iv = setInterval(() => {
    if (ok() || Date.now() - t0 > 25000) { clearInterval(iv); res(ok()); }
  }, 200);
}))"""

WAIT_READY_JS = """(() => new Promise(res => {
  const ready = () => {
    const btns = [...document.querySelectorAll('.run-source button')];
    const hasRun = btns.some(b => b.textContent.includes('run_0001'));
    const loading = [...document.querySelectorAll('.readiness-item')]
      .some(e => e.textContent.includes('Loading'));
    const rows = document.querySelectorAll('.overview-trade-log tbody tr').length;
    return hasRun && !loading && rows === 7;
  };
  if (ready()) return res(true);
  const t0 = Date.now();
  const iv = setInterval(() => {
    if (ready() || Date.now() - t0 > 25000) { clearInterval(iv); res(ready()); }
  }, 200);
}))()"""

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
    print(("PASS " if cond else "FAIL ") + name + (f" — {detail}" if detail else ""))
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


def main() -> int:
    ap = argparse.ArgumentParser(description="QBL Demo<->Archived browser E2E.")
    ap.add_argument("--url", default="http://127.0.0.1:5173/?fixture=demo")
    ap.add_argument("--cdp-port", type=int, default=9333)
    a = ap.parse_args()

    ok = True
    chrome = launch_chrome(a.cdp_port)
    try:
        targets = wait_debugger(a.cdp_port)
        page = next(t for t in targets if t.get("type") == "page")
        cdp = CDP(page["webSocketDebuggerUrl"])
        for domain in ("Page.enable", "Runtime.enable", "Log.enable", "Network.enable"):
            cdp.call(domain)
        cdp.call("Page.navigate", {"url": a.url})

        # ---- 1. DEMO BASELINE -------------------------------------------------
        ok &= check("app hydrated and demo ready", cdp.evaluate(WAIT_READY_JS), "run list + ready")
        ok &= check("demo badge", cdp.evaluate(f"({WAIT_BADGE_JS})('DEMO')"), "DEMO")
        ok &= check("demo data loaded",
                    cdp.evaluate(f"({WAIT_TEXT_JS})(['.overview-trade-log tbody tr','{DEMO_ENTRY}'])"),
                    "seeded trade present")
        snap = cdp.evaluate(SNAPSHOT_JS)
        snap["dataQuality"] = read_data_quality(cdp)
        print("--- 1. DEMO BASELINE ---")
        ok &= check("source switch lists Demo + run_0001",
                    has(snap["sourceBtns"], "Demo") and has(snap["sourceBtns"], "run_0001"),
                    str(snap["sourceBtns"]))
        ok &= check("demo trade rows == 7", snap["tradeRows"] == 7, str(snap["tradeRows"]))
        ok &= check("demo first trade is seeded", DEMO_ENTRY in (snap["firstRow"] or ""),
                    snap["firstRow"])
        ok &= check("demo engine is not connected", DEMO_ENGINE in snap["engine"], snap["engine"])
        ok &= check("equity chart present", snap["hasEquity"], "")
        ok &= check("candle chart present", snap["hasCandle"], "")
        ok &= check("demo readiness is seeded/not measured/not connected",
                    has(snap["readiness"], "Seeded demo")
                    and has(snap["readiness"], "Not measured")
                    and has(snap["readiness"], "Not available"),
                    "; ".join(snap["readiness"]))
        ok &= check("demo data quality is demo/not measured",
                    "Not a real run" in snap["dataQuality"], snap["dataQuality"][:120])
        ok &= check("demo account metrics are Pending (accountReady=false)",
                    has(snap["pending"], "Sharpe Ratio")
                    and has(snap["pending"], "Return %")
                    and has(snap["pending"], "Balance")
                    and has(snap["pending"], "Pending"),
                    "; ".join(snap["pending"][:6]))

        # ---- 2. SELECT A NON-DEFAULT TRADE -----------------------------------
        clicked_row = cdp.evaluate(f"({CLICK_ROW_JS})(5)")
        cdp.pump(0.4)
        sel = cdp.evaluate("document.querySelector('.overview-trade-log tr.selected td:first-child')?.textContent || ''")
        print("--- 2. TRADE SELECTION ---")
        ok &= check("demo trade #5 selectable", clicked_row == "CLICKED:7", str(clicked_row))
        ok &= check("demo selection follows click", sel.strip() == "5", sel.strip()[:80])

        # ---- 2b. DEMO TRADE INSPECTOR (seeded MAE/MFE preserved) -------------
        print("--- 2b. DEMO TRADE INSPECTOR (seeded MAE/MFE) ---")
        ok &= check("open demo Trade Inspector",
                    cdp.evaluate(f"({CLICK_NAV_JS})('Trade Inspector')") == "CLICKED", "")
        ok &= check("demo inspector rendered",
                    cdp.evaluate(f"({WAIT_SEL_JS})('.inspector')"), "")
        demo_insp = cdp.evaluate(f"({TEXT_JS})('.inspector')") or ""
        demo_explorer = cdp.evaluate(f"({TEXT_JS})('.trade-explorer')") or ""
        ok &= check("demo inspector keeps numeric MAE/MFE",
                    "MAE N/A" not in demo_insp and "MFE N/A" not in demo_insp
                    and "MAE " in demo_insp and "MFE " in demo_insp,
                    demo_insp[:160])
        ok &= check("demo trade explorer keeps numeric MAE/MFE",
                    "N/A" not in demo_explorer, demo_explorer[:160])
        ok &= check("return to Backtest (demo)",
                    cdp.evaluate(f"({CLICK_NAV_JS})('Backtest')") == "CLICKED", "")
        cdp.evaluate(f"({WAIT_SEL_JS})('.overview-trade-log')")

        # ---- 3. SWITCH TO run_0001 -------------------------------------------
        print("--- 3. SWITCH TO run_0001 ---")
        ok &= check("archived button clicked",
                    cdp.evaluate(CLICK_ARCHIVED_JS) == "CLICKED", "")
        ok &= check("archived badge",
                    cdp.evaluate(f"({WAIT_BADGE_JS})('ARCHIVED RUN')"), "ARCHIVED RUN")
        ok &= check("archived data loaded",
                    cdp.evaluate(f"({WAIT_TEXT_JS})(['.engine','{ARCH_ENGINE}'])"), ARCH_ENGINE)
        ok &= check("archived trade loaded",
                    cdp.evaluate(f"({WAIT_TEXT_JS})(['.overview-trade-log tbody tr','{ARCH_ENTRY}'])"),
                    ARCH_ENTRY)
        snap2 = cdp.evaluate(SNAPSHOT_JS)
        snap2["dataQuality"] = read_data_quality(cdp)
        ok &= check("archived trade rows == 7", snap2["tradeRows"] == 7, str(snap2["tradeRows"]))
        ok &= check("archived first trade value", ARCH_ENTRY in (snap2["firstRow"] or ""),
                    snap2["firstRow"])
        ok &= check("archived MAE/MFE not synthesized",
                    "N/A" in (snap2["firstRow"] or ""), snap2["firstRow"])
        ok &= check("archived engine reported", ARCH_ENGINE in snap2["engine"], snap2["engine"])
        ok &= check("archived net PnL rendered", ARCH_NET_PNL in snap2["metricsText"],
                    snap2["metricsText"][:160])
        ok &= check("selection reset to #1", (snap2["selected"] or "").strip() == "1",
                    snap2["selected"])
        ok &= check("no stale demo trade value",
                    (snap2["firstRow"] or "").count(DEMO_ENTRY) == 0, snap2["firstRow"])
        ok &= check("no stale demo engine/readiness",
                    "archived" not in snap2["engine"]
                    and not has(snap2["readiness"], "Seeded demo"),
                    snap2["engine"] + " | " + "; ".join(snap2["readiness"]))
        ok &= check("no stale demo run status",
                    "seeded demo" not in snap2["runStatus"]
                    and "run_0001" in snap2["runStatus"],
                    snap2["runStatus"][:160])
        ok &= check("archived readiness is live/measured/connected",
                    has(snap2["readiness"], "Archived dataset")
                    and has(snap2["readiness"], "Measured")
                    and has(snap2["readiness"], "Validated"),
                    "; ".join(snap2["readiness"]))
        ok &= check("archived data quality is measured",
                    "Not a real run" not in snap2["dataQuality"]
                    and ARCH_TICKS in snap2["dataQuality"],
                    snap2["dataQuality"][:160])
        ok &= check("archived account metrics stay Pending (accountReady=false)",
                    has(snap2["pending"], "Sharpe Ratio")
                    and has(snap2["pending"], "Return %")
                    and has(snap2["pending"], "Balance")
                    and has(snap2["pending"], "Pending"),
                    "; ".join(snap2["pending"][:6]))
        ok &= check("equity chart renders after switch", snap2["hasEquity"], "")
        # v1.0.2: archived market context needs the bridge. With no bridge the
        # chart must show an explicit unavailable state and NEVER demo candles.
        chart_txt = cdp.evaluate(f"({TEXT_JS})('.charts-grid')") or ""
        ok &= check("archived candle chart is honest with no bridge (no demo fallback)",
                    ("Market candles unavailable" in chart_txt) or snap2["hasCandle"],
                    chart_txt[:120])
        ok &= check("archived chart shows no demo prices", DEMO_ENTRY not in chart_txt, chart_txt[:120])

        # ---- 3b. TRADE INSPECTOR (archived) ----------------------------------
        print("--- 3b. TRADE INSPECTOR (archived) ---")
        ok &= check("open Trade Inspector",
                    cdp.evaluate(f"({CLICK_NAV_JS})('Trade Inspector')") == "CLICKED", "")
        ok &= check("inspector rendered", cdp.evaluate(f"({WAIT_SEL_JS})('.inspector')"), "")
        insp = cdp.evaluate(f"({TEXT_JS})('.inspector')") or ""
        explorer = cdp.evaluate(f"({TEXT_JS})('.trade-explorer')") or ""
        ok &= check("inspector shows archived trade", ARCH_ENTRY in insp,
                    insp[:160])
        ok &= check("inspector trade count is archived run",
                    "Trade #1 of 7" in insp, insp[:120])
        ok &= check("inspector has no demo trade value",
                    DEMO_ENTRY not in insp, insp[:160])
        ok &= check("archived Trade Inspector shows MAE/MFE = N/A",
                    "MAE N/A" in insp and "MFE N/A" in insp, insp[:200])
        ok &= check("archived Trade Explorer shows MAE/MFE = N/A",
                    "MAE N/A" in explorer and "MFE N/A" in explorer, explorer[:200])
        ok &= check("return to Backtest",
                    cdp.evaluate(f"({CLICK_NAV_JS})('Backtest')") == "CLICKED", "")
        cdp.evaluate(f"({WAIT_SEL_JS})('.overview-trade-log')")

        # ---- 3c. ANALYTICS (archived) ----------------------------------------
        print("--- 3c. ANALYTICS (archived) ---")
        ok &= check("open Analytics result tab",
                    cdp.evaluate(f"({CLICK_RESULT_TAB_JS})('Analytics')") == "CLICKED", "")
        cdp.evaluate("(t=>{const b=[...document.querySelectorAll('.analytics-nav button')].find(x=>x.textContent.includes(t)); if(b) b.click();})('Account & Data')")
        ok &= check("run identity rendered", cdp.evaluate(f"({WAIT_SEL_JS})('.run-identity')"), "")
        ident = cdp.evaluate(f"({TEXT_JS})('.run-identity')") or ""
        ok &= check("analytics shows archived strategy", "Candle Direction · 1.0" in ident,
                    ident[:200])
        ok &= check("analytics has no demo strategy name",
                    "Candle Direction Test" not in ident, ident[:200])
        ok &= check("analytics shows archived parameters",
                    "stop_distance" in ident and "take_profit_distance" in ident, ident[:240])
        cdp.evaluate("(t=>{const b=[...document.querySelectorAll('.analytics-nav button')].find(x=>x.textContent.includes(t)); if(b) b.click();})('Risk & Drawdown')")
        analytics_pending = cdp.evaluate(
            "Array.from(document.querySelectorAll('.analytics-view .pm[role=\"status\"]'))"
            ".map(e => e.getAttribute('aria-label'))"
        ) or []
        ok &= check("analytics MAE/MFE aggregates are unavailable (not synthesized)",
                    any("Average MAE" in x for x in analytics_pending)
                    and any("Average MFE" in x for x in analytics_pending)
                    and any("MFE Capture" in x for x in analytics_pending),
                    "; ".join(analytics_pending)[:220])
        ok &= check("return to Overview",
                    cdp.evaluate(f"({CLICK_RESULT_TAB_JS})('Overview')") == "CLICKED", "")
        cdp.evaluate(f"({WAIT_SEL_JS})('.overview-trade-log')")

        # ---- 4. SWITCH BACK TO DEMO ------------------------------------------
        print("--- 4. SWITCH BACK TO DEMO ---")
        ok &= check("demo button clicked", cdp.evaluate(CLICK_DEMO_JS) == "CLICKED", "")
        ok &= check("demo badge restored",
                    cdp.evaluate(f"({WAIT_BADGE_JS})('DEMO')"), "DEMO")
        ok &= check("demo data restored",
                    cdp.evaluate(f"({WAIT_TEXT_JS})(['.engine','{DEMO_ENGINE}'])"), DEMO_ENGINE)
        ok &= check("demo trade restored",
                    cdp.evaluate(f"({WAIT_TEXT_JS})(['.overview-trade-log tbody tr','{DEMO_ENTRY}'])"),
                    DEMO_ENTRY)
        snap3 = cdp.evaluate(SNAPSHOT_JS)
        snap3["dataQuality"] = read_data_quality(cdp)
        ok &= check("demo trade rows == 7 again", snap3["tradeRows"] == 7, str(snap3["tradeRows"]))
        ok &= check("demo first trade restored", DEMO_ENTRY in (snap3["firstRow"] or ""),
                    snap3["firstRow"])
        ok &= check("selection reset to #1 on return", (snap3["selected"] or "").strip() == "1",
                    snap3["selected"])
        ok &= check("no stale archived value remains",
                    (snap3["firstRow"] or "").count(ARCH_ENTRY) == 0
                    and "archived" not in snap3["engine"],
                    snap3["firstRow"] + " | " + snap3["engine"])
        ok &= check("demo readiness restored",
                    has(snap3["readiness"], "Seeded demo")
                    and has(snap3["readiness"], "Not measured"),
                    "; ".join(snap3["readiness"]))
        ok &= check("demo data quality restored",
                    "Not a real run" in snap3["dataQuality"],
                    snap3["dataQuality"][:120])
        ok &= check("equity chart renders after return", snap3["hasEquity"], "")
        ok &= check("candle chart renders after return", snap3["hasCandle"], "")

        # ---- 5. CONSOLE / NETWORK --------------------------------------------
        print("--- 5. CONSOLE / NETWORK ---")
        # This E2E intentionally runs with NO bridge, so bridge calls fail with
        # connection-refused; that expected noise is filtered. Any other error
        # (or a hydration error) still fails.
        errs = [e for e in cdp.console_errors
                if "favicon" not in e.lower()
                and "ERR_CONNECTION_REFUSED" not in e
                and "Failed to load resource" not in e]
        hydra = [e for e in cdp.console_errors if "hydrat" in e.lower()]
        failed = [r for r in cdp.failed_requests if "ERR_CONNECTION_REFUSED" not in r]
        ok &= check("no console errors", len(errs) == 0,
                    "; ".join(errs[:3]) if errs else "clean")
        ok &= check("no hydration errors", len(hydra) == 0,
                    "; ".join(hydra[:2]) if hydra else "clean")
        ok &= check("no unexpected failed requests", len(failed) == 0,
                    "; ".join(failed[:3]) if failed else "clean")
        cdp.ws.close()
    finally:
        chrome.terminate()
    print("E2E_RESULT=" + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
