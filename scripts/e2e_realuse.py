"""Real-use workflow E2E: the normal (non-demo) product experience.

Phase A: empty served manifest -> neutral empty state (never a DEMO run).
Phase B: an archived real run exists -> it is selected; run a real backtest with
the Account Model disabled (price terms) and enabled (account metrics); Data
Explorer stays truthful (no seeded stats); Run Library works.

The served manifest is a static frontend file, so it is snapshotted and restored.
"""

import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.e2e_run_switch import (  # noqa: E402
    CDP, TEXT_JS, WAIT_BADGE_JS, WAIT_SEL_JS, WAIT_TEXT_JS, launch_chrome, wait_debugger,
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

APP_URL = "http://127.0.0.1:5173/"
BRIDGE = "http://127.0.0.1:8765"
PUBLIC = ROOT / "Quant-Backtest-Lab" / "public"
MANIFEST = PUBLIC / "adapters-run-manifest.json"
FIXTURE = ROOT / "tests" / "fixtures" / "run_0001"
THRESHOLD = ROOT / "tests" / "fixtures" / "strategies" / "threshold_direction.py"

_POLL = "const t0=Date.now();const iv=setInterval(()=>{if(ok()||Date.now()-t0>MAX){clearInterval(iv);res(ok());}},250);"

SET_INPUT_JS = """(([sel, value]) => {
  const el = document.querySelector(sel); if (!el) return 'NO_EL';
  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
  setter.call(el, value); el.dispatchEvent(new Event('input', { bubbles: true })); el.dispatchEvent(new Event('change', { bubbles: true }));
  return el.value;
})"""
CLICK_EL_JS = """((sel) => { const el=document.querySelector(sel); if(!el) return 'NO'; for (const t of ['pointerdown','mousedown','mouseup','click']) el.dispatchEvent(new MouseEvent(t,{bubbles:true,cancelable:true,view:window})); return 'OK'; })"""
CLICK_ARIA_JS = """((label) => { const el=document.querySelector('[aria-label="'+label.replace(/"/g,'\\\\"')+'"]'); if(!el) return 'NO'; for (const t of ['pointerdown','mousedown','mouseup','click']) el.dispatchEvent(new MouseEvent(t,{bubbles:true,cancelable:true,view:window})); return 'OK'; })"""
CLICK_TEXT_JS = """(([root,text]) => { const el=[...document.querySelectorAll(root)].find(b=>b.textContent.trim()===text||b.textContent.includes(text)); if(!el) return 'NO'; for (const t of ['pointerdown','mousedown','mouseup','click']) el.dispatchEvent(new MouseEvent(t,{bubbles:true,cancelable:true,view:window})); return 'OK'; })"""
CLICK_RUN_JS = """(() => { const b=[...document.querySelectorAll('.real-run button')].find(x=>x.textContent.includes('Run Backtest')||x.textContent.includes('Running')); if(!b) return 'NO'; b.click(); return 'CLICKED'; })()"""

WAIT_ENABLED_JS = ("(() => new Promise(res => {"
                   " const ok=()=>{const b=[...document.querySelectorAll('.real-run button')].find(x=>x.textContent.includes('Run Backtest'));return !!b && !b.disabled;};"
                   " if(ok())return res(true);" + _POLL.replace("MAX", "30000") + "}))()")
WAIT_NEW_RUN_JS = ("(prev => new Promise(res => {"
                   " const ok=()=>{const t=(document.querySelector('.run-status')||{}).textContent||'';"
                   "  const m=t.match(/Archived Run · (\\S+) ·/); return !!m && m[1]!==prev;};"
                   " if(ok())return res(true);" + _POLL.replace("MAX", "180000") + "}))")
WAIT_EMPTY_JS = ("(() => new Promise(res => { const ok=()=>!!document.querySelector('.empty-state');"
                 " if(ok())return res(true);" + _POLL.replace("MAX", "20000") + "}))()")
WAIT_METRICS_JS = ("(text => new Promise(res => {"
                   " const ok=()=>{const e=document.querySelector('.metrics-grouped'); return !!e && e.textContent.includes(text);};"
                   " if(ok())return res(true);" + _POLL.replace("MAX", "20000") + "}))")

STATUS_TEXT_JS = "(() => { const e=document.querySelector('.run-status'); return e ? e.textContent : ''; })()"


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f" — {detail}" if detail else ""))
    return bool(cond)


def health(timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        try:
            with urllib.request.urlopen(f"{BRIDGE}/health", timeout=3) as r:
                return json.loads(r.read())["status"] == "ok"
        except Exception:
            time.sleep(0.4)
    return False


def set_input(cdp, sel, value):
    cdp.evaluate(f"({SET_INPUT_JS})([{json.dumps(sel)},{json.dumps(value)}])")


def escape(cdp):
    cdp.call("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Escape", "code": "Escape", "windowsVirtualKeyCode": 27})
    cdp.call("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Escape", "code": "Escape", "windowsVirtualKeyCode": 27})


def open_library(cdp):
    cdp.evaluate(f"({CLICK_EL_JS})('.library-button')")
    cdp.evaluate(f"({WAIT_SEL_JS})('.workspace-dialog')")


def open_tab(cdp, name):
    cdp.evaluate(f"({CLICK_TEXT_JS})(['.workspace-tabs button','{name}'])")


def select_radix(cdp, trigger_aria, option_text):
    cdp.evaluate(f"({CLICK_ARIA_JS})({json.dumps(trigger_aria)})")
    for _ in range(60):
        if cdp.evaluate(f"(text => [...document.querySelectorAll('[role=option]')].some(o=>o.textContent.includes(text)))({json.dumps(option_text)})"):
            break
        time.sleep(0.25)
    cdp.evaluate(
        "(text => { const o=[...document.querySelectorAll('[role=option]')].find(x=>x.textContent.includes(text));"
        " if(!o) return 'NO'; for (const t of ['pointerdown','mousedown','mouseup','click']) o.dispatchEvent(new MouseEvent(t,{bubbles:true,cancelable:true,view:window})); return 'OK'; })"
        f"({json.dumps(option_text)})")


def main() -> int:
    ok = True
    manifest_before = MANIFEST.read_text(encoding="utf-8")
    files_before = set(p.name for p in PUBLIC.glob("runs-*-result.json"))
    tmp = Path(tempfile.mkdtemp(prefix="qbl-e2e-realuse-"))
    runs = tmp / "runs"; runs.mkdir()
    strategy = tmp / "strategies"; strategy.mkdir()
    shutil.copy(THRESHOLD, strategy / "threshold_direction.py")
    log = open(tmp / "bridge.log", "w")

    def start_bridge():
        return subprocess.Popen(
            [sys.executable, "-m", "src.bridge", "--port", "8765", "--runs", str(runs),
             "--public", str(PUBLIC), "--strategy-dir", str(strategy), "--data-dir", str(ROOT / "data"),
             "--studies", str(tmp / "studies"), "--walkforwards", str(tmp / "wf"), "--montecarlo", str(tmp / "mc")],
            cwd=str(ROOT), stdout=log, stderr=subprocess.STDOUT)

    def open_app():
        chrome = launch_chrome(9370)
        t = wait_debugger(9370)
        page = next(x for x in t if x.get("type") == "page")
        cdp = CDP(page["webSocketDebuggerUrl"])
        for dom in ("Page.enable", "Runtime.enable", "Log.enable", "Network.enable", "DOM.enable"):
            cdp.call(dom)
        cdp.call("Page.navigate", {"url": APP_URL})
        return chrome, cdp

    try:
        # ---- Phase A: no archived runs -> neutral empty state -----------------
        MANIFEST.write_text(json.dumps({"runs": []}), encoding="utf-8")
        bridge = start_bridge()
        chrome = None
        try:
            ok &= check("bridge starts (phase A)", health(), BRIDGE)
            chrome, cdp = open_app()
            ok &= check("real-use empty state shown when no runs", cdp.evaluate(WAIT_EMPTY_JS))
            ok &= check("badge is not DEMO without a run",
                        "DEMO" not in (cdp.evaluate(f"({TEXT_JS})('.demo-badge')") or ""))
            ok &= check("no demo source control exists",
                        not cdp.evaluate("([...document.querySelectorAll('.run-source button')].some(b=>b.textContent.includes('Demo')))"))
            cdp.ws.close()
        finally:
            if chrome is not None:
                chrome.terminate()
            bridge.terminate()
            time.sleep(1)

        # ---- Phase B: archived real run -> real workflow ----------------------
        (PUBLIC / "runs-run_0001-result.json").is_file()  # baseline artifact must exist
        MANIFEST.write_text(json.dumps({
            "runs": [{"runId": "run_0001", "file": "runs/run_0001/result.json",
                      "label": "run_0001 · Candle Direction", "description": "Archived reference run"}]
        }), encoding="utf-8")
        shutil.copytree(FIXTURE, runs / "run_0001")
        bridge = start_bridge()
        chrome = None
        try:
            ok &= check("bridge starts (phase B)", health(), BRIDGE)
            chrome, cdp = open_app()
            ok &= check("archived real run auto-selected (no demo)",
                        cdp.evaluate(f"({WAIT_BADGE_JS})('ARCHIVED RUN')"))
            ok &= check("selected run is run_0001", "run_0001" in (cdp.evaluate(STATUS_TEXT_JS) or ""))
            ok &= check("no demo source control exists",
                        not cdp.evaluate("([...document.querySelectorAll('.run-source button')].some(b=>b.textContent.includes('Demo')))"))
            ok &= check("topbar bridge status is explicit + separate",
                        "Python Bridge:" in (cdp.evaluate(f"({TEXT_JS})('.engine')") or ""))

            # real run, account model disabled -> price-term metrics
            ok &= check("real-run enabled (price)", cdp.evaluate(WAIT_ENABLED_JS))
            select_radix(cdp, "Real run strategy", "Candle Direction")
            set_input(cdp, '[aria-label="Run start date"]', "2025-01-02")
            set_input(cdp, '[aria-label="Run end date"]', "2025-01-02")
            cdp.evaluate(CLICK_RUN_JS)
            ok &= check("price-term run completes", cdp.evaluate(f"({WAIT_NEW_RUN_JS})('run_0001')", timeout=200))
            price_run = None
            for _ in range(20):
                mm = re.search(r"Archived Run · (\S+) ·", cdp.evaluate(STATUS_TEXT_JS) or "")
                if mm and mm.group(1) != "run_0001":
                    price_run = mm.group(1)
                    break
                time.sleep(0.3)
            ok &= check("price-term metrics render", cdp.evaluate(f"({WAIT_METRICS_JS})('Total PnL')"))
            ok &= check("account metrics stay Pending without a model",
                        cdp.evaluate(f"({WAIT_SEL_JS})('[aria-label=\"Balance: Pending\"]')"))

            # real run, account model enabled -> account metrics
            cdp.evaluate(f"({CLICK_ARIA_JS})('Strategy & Trade Settings')")
            cdp.evaluate(f"({WAIT_SEL_JS})('[aria-label=\"Stop distance\"]')")
            cdp.evaluate(f"({CLICK_ARIA_JS})('Enable account model')")
            cdp.evaluate(f"({WAIT_SEL_JS})('[aria-label=\"Account currency\"]')")
            set_input(cdp, '[aria-label="Account currency"]', "USD")
            set_input(cdp, '[aria-label="Account starting balance"]', "10000")
            set_input(cdp, '[aria-label="Account position size lots"]', "1")
            set_input(cdp, '[aria-label="Account commission per lot"]', "7")
            set_input(cdp, '[aria-label="Instrument contract size"]', "100")
            set_input(cdp, '[aria-label="Instrument quote currency"]', "USD")
            escape(cdp)
            time.sleep(0.4)
            cdp.evaluate(CLICK_RUN_JS)
            ok &= check("account-enabled run completes", cdp.evaluate(f"({WAIT_NEW_RUN_JS})({json.dumps(price_run)})", timeout=200))
            ok &= check("account metrics appear when enabled", cdp.evaluate(f"({WAIT_METRICS_JS})('Net Account PnL')"))
            ok &= check("chart legend uses clear price-term wording",
                        "Cumulative Trade PnL" in (cdp.evaluate(f"({TEXT_JS})('.equity-legend')") or ""))

            # Data Explorer stays truthful
            cdp.evaluate(f"({CLICK_TEXT_JS})(['.navigation button','Data Explorer'])")
            time.sleep(0.8)
            de = cdp.evaluate(f"({TEXT_JS})('.explorer')") or ""
            ok &= check("data explorer shows no seeded statistics",
                        "SEEDED PREVIEW" not in de and "99.12" not in de and "Illustrative spread" not in de)

            # Run Library works
            open_library(cdp)
            open_tab(cdp, "Runs")
            ok &= check("run library lists real runs", cdp.evaluate(f"({WAIT_SEL_JS})('.lib-row')"))

            errs = [e for e in cdp.console_errors if "favicon" not in e.lower() and "status of 40" not in e.lower()]
            hydra = [e for e in cdp.console_errors if "hydrat" in e.lower()]
            ok &= check("no unexpected console errors", len(errs) == 0, "; ".join(errs[:2]) if errs else "clean")
            ok &= check("no hydration errors", len(hydra) == 0)
            cdp.ws.close()
        finally:
            if chrome is not None:
                chrome.terminate()
            bridge.terminate()
    finally:
        MANIFEST.write_text(manifest_before, encoding="utf-8")
        for p in PUBLIC.glob("runs-*-result.json"):
            if p.name not in files_before:
                p.unlink()
        log.close()
        shutil.rmtree(tmp, ignore_errors=True)

    print("E2E_RESULT=" + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
