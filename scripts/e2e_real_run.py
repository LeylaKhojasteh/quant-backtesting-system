"""Real browser E2E: configure and execute a real Python backtest from the UI.

Starts the local bridge (repo public dir so the frontend serves the published
run), drives Chrome over CDP, and verifies the full flow:

  bridge Connected -> catalogs load -> invalid date range shows a clean bridge
  error (fresh page, no fallback) -> configure candle_direction on real data ->
  Running state (duplicate clicks ignored) -> completes -> archived +
  report.html + published -> new run appears in the run source -> UI
  auto-switches -> metrics/trades match the archived result.json -> Inspector
  and Analytics work, unsupported metrics stay unavailable -> run_0001 still
  loads -> Demo still works -> switch back to the new run (no stale data) ->
  bridge offline shows Unavailable while Demo keeps working.

The repo's frontend public artifacts are snapshotted and restored afterwards.
"""

import json
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.e2e_run_switch import (  # noqa: E402
    CDP,
    CLICK_NAV_JS,
    CLICK_RESULT_TAB_JS,
    TEXT_JS,
    WAIT_BADGE_JS,
    WAIT_SEL_JS,
    WAIT_TEXT_JS,
    launch_chrome,
    wait_debugger,
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

APP_URL = "http://127.0.0.1:5173/"
BRIDGE_PORT = 8765
BRIDGE_URL = f"http://127.0.0.1:{BRIDGE_PORT}"
PUBLIC = ROOT / "Quant-Backtest-Lab" / "public"
MANIFEST = PUBLIC / "adapters-run-manifest.json"
DEMO_ENTRY = "2,799.84"
RUN0001_ENTRY = "2,626.08"

_POLL = "const t0=Date.now();const iv=setInterval(()=>{if(ok()||Date.now()-t0>MAX){clearInterval(iv);res(ok());}},250);"
WAIT_SOURCE_JS = ("(needle => new Promise(res => {"
                  " const ok=()=>[...document.querySelectorAll('.run-source button')].some(b=>b.textContent.includes(needle));"
                  " if(ok())return res(true);" + _POLL.replace("MAX", "25000") + "}))")
WAIT_ROW_JS = ("(([text,notText]) => new Promise(res => {"
               " const ok=()=>{const r=document.querySelector('.overview-trade-log tbody tr');"
               "  return !!r && r.textContent.includes(text) && (!notText || !r.textContent.includes(notText));};"
               " if(ok())return res(true);" + _POLL.replace("MAX", "25000") + "}))")
WAIT_DONE_JS = ("(() => new Promise(res => {"
                " const ok=()=>{const n=document.querySelector('.rr-notice');const b=document.querySelector('.demo-badge');"
                "  return !!n && !!b && b.textContent.trim()==='ARCHIVED RUN';};"
                " if(ok())return res(true);" + _POLL.replace("MAX", "150000") + "}))()")
WAIT_ENABLED_JS = ("(() => new Promise(res => {"
                   " const ok=()=>{const b=[...document.querySelectorAll('.real-run button')].find(x=>x.textContent.includes('Run Backtest'));return !!b && !b.disabled;};"
                   " if(ok())return res(true);" + _POLL.replace("MAX", "30000") + "}))()")
WAIT_INPUT_JS = ("(([sel,value]) => new Promise(res => {"
                 " const ok=()=>{const e=document.querySelector(sel);return !!e && e.value===value;};"
                 " if(ok())return res(true);" + _POLL.replace("MAX", "10000") + "}))")

# Page-side observer so a transient "Running…" state is never missed between polls.
INSTALL_OBSERVER_JS = """(() => {
  window.__sawRunning = false;
  const scan = () => {
    if ([...document.querySelectorAll('.real-run button')].some(b => b.textContent.includes('Running'))) window.__sawRunning = true;
  };
  scan();
  if (window.__runMo) window.__runMo.disconnect();
  const mo = new MutationObserver(scan);
  mo.observe(document.documentElement, { subtree: true, childList: true, characterData: true, attributes: true });
  window.__runMo = mo;
  return 'installed';
})()"""
SAW_RUNNING_JS = "!!window.__sawRunning"
HAS_ERROR_JS = "!!document.querySelector('.rr-error')"

SET_INPUT_JS = """(([sel, value]) => {
  const el = document.querySelector(sel);
  if (!el) return 'NO_EL:' + sel;
  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
  setter.call(el, value);
  el.dispatchEvent(new Event('input', { bubbles: true }));
  el.dispatchEvent(new Event('change', { bubbles: true }));
  return el.value;
})"""

CLICK_RUN_JS = """(() => {
  const b = [...document.querySelectorAll('.real-run button')]
    .find(x => x.textContent.includes('Run Backtest') || x.textContent.includes('Running'));
  if (!b) return 'NO_BTN';
  b.click();
  return 'CLICKED';
})()"""

CLICK_SOURCE_JS = """((needle) => {
  const b = [...document.querySelectorAll('.run-source button')].find(x => x.textContent.includes(needle));
  if (!b) return 'NOT_FOUND';
  b.click();
  return 'CLICKED';
})"""


def check(name: str, cond, detail: str = "") -> bool:
    print(("PASS " if cond else "FAIL ") + name + (f" — {detail}" if detail else ""))
    return bool(cond)


def fmt(n: float) -> str:
    return f"{n:,.2f}"


def get_bridge_health(timeout: int = 30) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        try:
            with urllib.request.urlopen(f"{BRIDGE_URL}/health", timeout=3) as r:
                return json.loads(r.read())["status"] == "ok"
        except Exception:
            time.sleep(0.4)
    return False


def dev_up() -> bool:
    try:
        with urllib.request.urlopen(APP_URL, timeout=5) as r:
            return r.status == 200
    except Exception:
        return False


def set_dates(cdp: CDP, start: str, end: str) -> None:
    cdp.evaluate(f"({SET_INPUT_JS})(['[aria-label=\"Run start date\"]','{start}'])")
    cdp.evaluate(f"({SET_INPUT_JS})(['[aria-label=\"Run end date\"]','{end}'])")


def main() -> int:
    ok = True
    if not dev_up():
        print("E2E needs the frontend dev server on 5173. Aborting.")
        return 1

    manifest_before = MANIFEST.read_text(encoding="utf-8") if MANIFEST.is_file() else None
    files_before = set(p.name for p in PUBLIC.glob("runs-*-result.json"))

    runs_dir = Path(tempfile.mkdtemp(prefix="qbl-e2e-runs-"))
    bridge = subprocess.Popen(
        [sys.executable, "-m", "src.bridge", "--port", str(BRIDGE_PORT),
         "--runs", str(runs_dir), "--public", str(PUBLIC),
         "--strategy-dir", "tests/fixtures/strategies", "--data-dir", "data"],
        cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    chrome = None
    try:
        ok &= check("bridge starts locally", get_bridge_health(), BRIDGE_URL)

        chrome = launch_chrome(9345)
        targets = wait_debugger(9345)
        page = next(t for t in targets if t.get("type") == "page")
        cdp = CDP(page["webSocketDebuggerUrl"])
        for dom in ("Page.enable", "Runtime.enable", "Log.enable", "Network.enable"):
            cdp.call(dom)
        cdp.call("Page.navigate", {"url": APP_URL})

        # ---- app + bridge status ---------------------------------------------
        ok &= check("app loads Demo", cdp.evaluate(f"({WAIT_BADGE_JS})('DEMO')"), "DEMO")
        ok &= check("run_0001 present", cdp.evaluate(f"({WAIT_SOURCE_JS})('run_0001')"))
        ok &= check("bridge shows Connected",
                    cdp.evaluate(f"({WAIT_TEXT_JS})(['.engine','Connected'])"),
                    cdp.evaluate(f"({TEXT_JS})('.engine')"))
        ok &= check("strategy catalog loads",
                    cdp.evaluate(f"({WAIT_TEXT_JS})(['[aria-label=\"Real run strategy\"]','Candle Direction'])"),
                    cdp.evaluate(f"({TEXT_JS})('[aria-label=\"Real run strategy\"]')"))
        ok &= check("dataset catalog loads",
                    cdp.evaluate(f"({WAIT_TEXT_JS})(['[aria-label=\"Real run dataset\"]','XAUUSD_'])"),
                    cdp.evaluate(f"({TEXT_JS})('[aria-label=\"Real run dataset\"]')"))

        # ---- invalid date range -> clean bridge error (fresh page) -----------
        runs_before_err = cdp.evaluate("document.querySelectorAll('.run-source button').length")
        set_dates(cdp, "2025-03-05", "2025-03-05")
        cdp.evaluate(f"({WAIT_ENABLED_JS})")
        cdp.evaluate(CLICK_RUN_JS)
        err_shown = False
        for _ in range(40):  # the failing run still reads the full file first
            if cdp.evaluate(HAS_ERROR_JS):
                err_shown = True
                break
            time.sleep(1.5)
        ok &= check("bridge error displayed for empty range", err_shown,
                    cdp.evaluate(f"({TEXT_JS})('.rr-error')"))
        runs_after_err = cdp.evaluate("document.querySelectorAll('.run-source button').length")
        ok &= check("failed run adds no selector entry", runs_after_err == runs_before_err, f"{runs_before_err} -> {runs_after_err}")
        ok &= check("no fallback: still Demo", cdp.evaluate(
            "document.querySelector('.run-source button.source-active')?.textContent.includes('Demo')"))

        # ---- configure + run a known real-data candle_direction backtest -----
        set_dates(cdp, "2025-01-02", "2025-01-02")
        ok &= check("run dates applied",
                    cdp.evaluate(f"({WAIT_INPUT_JS})(['[aria-label=\"Run start date\"]','2025-01-02'])"),
                    cdp.evaluate("document.querySelector('[aria-label=\"Run start date\"]').value"))
        ok &= check("Run Backtest enabled", cdp.evaluate(WAIT_ENABLED_JS))
        runs_before = cdp.evaluate("document.querySelectorAll('.run-source button').length")
        cdp.evaluate(INSTALL_OBSERVER_JS)
        cdp.evaluate(CLICK_RUN_JS)
        # duplicate clicks in the same tick must not start a second run
        cdp.evaluate("(() => { const b=[...document.querySelectorAll('.real-run button')].find(x=>x.textContent.includes('Running')||x.textContent.includes('Run Backtest')); if(b){b.click();b.click();} return 'ok'; })()")

        ok &= check("backtest completes + auto-loads", cdp.evaluate(WAIT_DONE_JS, timeout=180),
                    cdp.evaluate(f"({TEXT_JS})('.rr-notice')"))
        ok &= check("Running state appears", cdp.evaluate(SAW_RUNNING_JS))
        runs_after = cdp.evaluate("document.querySelectorAll('.run-source button').length")
        ok &= check("exactly one new run (duplicate clicks ignored)", runs_after == runs_before + 1,
                    f"{runs_before} -> {runs_after}")

        status_text = cdp.evaluate(f"({TEXT_JS})('.run-status')") or ""
        m = re.search(r"Archived reference · (\S+) ·", status_text)
        new_run = m.group(1) if m else None
        ok &= check("UI switched to new run", bool(new_run), status_text[:140])

        # ---- archive / report / publish + data match -------------------------
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        entry = next((r for r in manifest["runs"] if r["runId"] == new_run), None)
        published = PUBLIC / f"runs-{new_run}-result.json" if new_run else None
        ok &= check("manifest lists new run", entry is not None, json.dumps(entry, ensure_ascii=False))
        ok &= check("report.html created", bool(new_run) and (runs_dir / new_run / "report.html").is_file())
        ok &= check("published result present", bool(published) and published.is_file(), str(published))

        payload = json.loads(published.read_text(encoding="utf-8")) if published and published.is_file() else {}
        trades = payload.get("trades", [])
        metrics = payload.get("metrics", {})
        first_entry = fmt(trades[0]["entry_price"]) if trades else ""
        ok &= check("auto-loaded run rendered",
                    cdp.evaluate(f"({WAIT_ROW_JS})(['{first_entry}','{DEMO_ENTRY}'])") if first_entry else False,
                    first_entry)
        trade_rows = cdp.evaluate("document.querySelectorAll('.overview-trade-log tbody tr').length")
        first_row = cdp.evaluate("(() => { const r=document.querySelector('.overview-trade-log tbody tr'); return r?r.textContent.replace(/\\s+/g,' '):''; })()")
        metrics_text = cdp.evaluate(f"({TEXT_JS})('.metrics-grouped')") or ""
        ok &= check("trade count matches archive", trade_rows == len(trades), f"dom={trade_rows} archive={len(trades)}")
        ok &= check("first trade matches archive", bool(first_entry) and first_entry in first_row,
                    f"{first_entry} | {first_row[:80]}")
        ok &= check("net PnL matches archive", fmt(metrics.get("net_pnl", 0)) in metrics_text,
                    fmt(metrics.get("net_pnl", 0)))
        ok &= check("no stale demo values", DEMO_ENTRY not in first_row, first_row[:80])

        # ---- Inspector / Analytics / unavailable metrics ---------------------
        cdp.evaluate(f"({CLICK_NAV_JS})('Trade Inspector')")
        ok &= check("Trade Inspector renders", cdp.evaluate(f"({WAIT_SEL_JS})('.inspector')"))
        insp = cdp.evaluate(f"({TEXT_JS})('.inspector')") or ""
        ok &= check("Inspector shows archived trade", first_entry in insp, insp[:100])
        ok &= check("Inspector MAE/MFE unavailable (N/A)", "MAE N/A" in insp and "MFE N/A" in insp)
        cdp.evaluate(f"({CLICK_NAV_JS})('Backtest')")
        cdp.evaluate(f"({WAIT_SEL_JS})('.overview-trade-log')")
        cdp.evaluate(f"({CLICK_RESULT_TAB_JS})('Analytics')")
        ok &= check("Analytics run identity renders", cdp.evaluate(f"({WAIT_SEL_JS})('.run-identity')"))
        ident = cdp.evaluate(f"({TEXT_JS})('.run-identity')") or ""
        ok &= check("Analytics shows run strategy", payload.get("strategy", {}).get("name", "") in ident, ident[:120])
        pending = cdp.evaluate("Array.from(document.querySelectorAll('.analytics-view .pm[role=\"status\"]')).map(e=>e.getAttribute('aria-label'))") or []
        ok &= check("unsupported MAE/MFE metrics stay unavailable", any("Average MAE" in x for x in pending),
                    "; ".join(pending)[:140])
        cdp.evaluate(f"({CLICK_RESULT_TAB_JS})('Overview')")
        cdp.evaluate(f"({WAIT_SEL_JS})('.overview-trade-log')")

        # ---- existing run_0001 + Demo + back ---------------------------------
        ok &= check("run_0001 still loads", cdp.evaluate(f"({CLICK_SOURCE_JS})('run_0001')") == "CLICKED")
        ok &= check("run_0001 data restored", cdp.evaluate(f"({WAIT_ROW_JS})(['{RUN0001_ENTRY}',''])") )
        ok &= check("switch to Demo", cdp.evaluate(f"({CLICK_SOURCE_JS})('Demo')") == "CLICKED")
        ok &= check("Demo data restored", cdp.evaluate(f"({WAIT_ROW_JS})(['{DEMO_ENTRY}',''])") )
        ok &= check("switch back to new run", cdp.evaluate(f"({CLICK_SOURCE_JS})({json.dumps(new_run or '')})") == "CLICKED")
        ok &= check("new run data restored (no stale)",
                    cdp.evaluate(f"({WAIT_ROW_JS})(['{first_entry}','{DEMO_ENTRY}'])") )
        ok &= check("selected trade resets to #1", "#1" in (cdp.evaluate(f"({TEXT_JS})('.selected-trade-strip')") or ""))

        errs = [e for e in cdp.console_errors
                if "favicon" not in e.lower()
                and "status of 400" not in e.lower()]  # the intentional invalid-range run
        hydra = [e for e in cdp.console_errors if "hydrat" in e.lower()]
        ok &= check("no console errors", len(errs) == 0, "; ".join(errs[:2]) if errs else "clean")
        ok &= check("no hydration errors", len(hydra) == 0)

        # ---- bridge offline ---------------------------------------------------
        bridge.terminate()
        bridge.wait(timeout=10)
        cdp.call("Page.navigate", {"url": APP_URL})
        ok &= check("offline: app still loads Demo", cdp.evaluate(f"({WAIT_BADGE_JS})('DEMO')"))
        ok &= check("offline: engine shows Unavailable", cdp.evaluate(f"({WAIT_TEXT_JS})(['.engine','Unavailable'])"))
        ok &= check("offline: Demo workflow works", cdp.evaluate(f"({WAIT_ROW_JS})(['{DEMO_ENTRY}',''])") )
        ok &= check("offline: real-run control disabled", cdp.evaluate(
            "(() => { const b=[...document.querySelectorAll('.real-run button')].find(x=>x.textContent.includes('Run Backtest')); return !!b && b.disabled; })()"))
        cdp.ws.close()
    finally:
        if chrome is not None:
            chrome.terminate()
        if bridge.poll() is None:
            bridge.terminate()
        if manifest_before is not None:
            MANIFEST.write_text(manifest_before, encoding="utf-8")
        for p in PUBLIC.glob("runs-*-result.json"):
            if p.name not in files_before:
                p.unlink()

    print("E2E_RESULT=" + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
