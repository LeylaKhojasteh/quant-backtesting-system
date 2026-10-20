"""Timeframe mapping QA: drives the real UI for ALL six options, captures the
actual POST /runs payload and the resulting archived config.json.

Proves M1->1min, M5->5min, M15->15min, M30->30min, H1->1h, H4->4h with no alias
collision. Uses isolated temp runs/public so the repo baseline is untouched.
"""

import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from scripts.e2e_run_switch import e2e_main, launch_bridge, require_bridge_port_free

from scripts.e2e_run_switch import CDP, WAIT_SEL_JS, launch_chrome, wait_debugger  # noqa: E402

APP = "http://127.0.0.1:5173/"
BRIDGE = "http://127.0.0.1:8765"
DATA = ROOT / "data" / "XAUUSD_DUKAS_2025_01_ticks.csv"

MAPPINGS = [
    ("1 minute (M1)", "1min"),
    ("5 minutes (M5)", "5min"),
    ("15 minutes (M15)", "15min"),
    ("30 minutes (M30)", "30min"),
    ("1 hour (H1)", "1h"),
    ("4 hours (H4)", "4h"),
]

_POLL = "const t0=Date.now();const iv=setInterval(()=>{if(ok()||Date.now()-t0>MAX){clearInterval(iv);res(ok());}},250);"
SET_INPUT_JS = """(([sel, value]) => { const el=document.querySelector(sel); if(!el) return 'NO_EL';
  const setter=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set;
  setter.call(el,value); el.dispatchEvent(new Event('input',{bubbles:true})); el.dispatchEvent(new Event('change',{bubbles:true})); return el.value; })"""
CLICK_ARIA_JS = """((label) => { const el=document.querySelector('[aria-label="'+label.replace(/"/g,'\\\\"')+'"]'); if(!el) return 'NO'; for (const t of ['pointerdown','mousedown','mouseup','click']) el.dispatchEvent(new MouseEvent(t,{bubbles:true,cancelable:true,view:window})); return 'OK'; })"""
CLICK_RUN_JS = """(() => { const b=[...document.querySelectorAll('.real-run button')].find(x=>x.textContent.includes('Run Backtest')||x.textContent.includes('Running')); if(!b) return 'NO'; b.click(); return 'CLICKED'; })()"""
WAIT_NEW_RUN_JS = ("(prev => new Promise(res => { const ok=()=>{const t=(document.querySelector('.run-status')||{}).textContent||'';"
                   " const m=t.match(/Archived Run · (\\S+) ·/); return !!m && m[1]!==prev;};"
                   " if(ok())return res(true);" + _POLL.replace("MAX", "180000") + "}))")
WAIT_ENABLED_JS = ("(() => new Promise(res => { const ok=()=>{const b=[...document.querySelectorAll('.real-run button')].find(x=>x.textContent.includes('Run Backtest'));return !!b && !b.disabled;};"
                   " if(ok())return res(true);" + _POLL.replace("MAX", "40000") + "}))()")


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f" — {detail}" if detail else ""))
    return bool(cond)


def set_input(cdp, sel, value):
    cdp.evaluate(f"({SET_INPUT_JS})([{json.dumps(sel)},{json.dumps(value)}])")


def select_timeframe(cdp, label):
    for _ in range(20):
        cdp.evaluate(f"({CLICK_ARIA_JS})('Run timeframe')")
        time.sleep(0.3)
        found = cdp.evaluate(f"(text => [...document.querySelectorAll('[role=option]')].some(o=>o.textContent.trim()===text))({json.dumps(label)})")
        if found:
            cdp.evaluate("(text => { const o=[...document.querySelectorAll('[role=option]')].find(x=>x.textContent.trim()===text);"
                         " if(!o) return 'NO'; for (const t of ['pointerdown','mousedown','mouseup','click']) o.dispatchEvent(new MouseEvent(t,{bubbles:true,cancelable:true,view:window})); return 'OK'; })"
                         f"({json.dumps(label)})")
            time.sleep(0.3)
            return True
    return False


def main() -> int:
    ok = True
    tmp = Path(tempfile.mkdtemp(prefix="qbl-tfmap-"))
    runs = tmp / "runs"; runs.mkdir()
    pub = tmp / "public"; pub.mkdir()
    require_bridge_port_free()
    bridge = launch_bridge(
        [sys.executable, "-m", "src.bridge", "--port", "8765", "--runs", str(runs), "--public", str(pub),
         "--data-dir", str(ROOT / "data"), "--strategy-dir", str(tmp / "strat"),
         "--studies", str(tmp / "st"), "--walkforwards", str(tmp / "wf"), "--montecarlo", str(tmp / "mc")],
        cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    chrome = None
    try:
        end = time.time() + 30
        while time.time() < end:
            try:
                import urllib.request
                urllib.request.build_opener(urllib.request.ProxyHandler({})).open(f"{BRIDGE}/health", timeout=2)
                break
            except Exception:
                time.sleep(0.3)
        chrome = launch_chrome()
        t = wait_debugger(chrome.debug_port)
        page = next(x for x in t if x.get("type") == "page")
        cdp = CDP(page["webSocketDebuggerUrl"])
        for dom in ("Page.enable", "Runtime.enable", "Log.enable", "Network.enable", "DOM.enable"):
            cdp.call(dom)
        cdp.call("Page.navigate", {"url": APP})
        time.sleep(3)
        cdp.call("Emulation.setDeviceMetricsOverride", {"width": 1440, "height": 900, "deviceScaleFactor": 1, "mobile": False})
        time.sleep(0.6)
        ok &= check("real-run enabled", cdp.evaluate(WAIT_ENABLED_JS))
        set_input(cdp, '[aria-label="Run start date"]', "2025-01-02")
        set_input(cdp, '[aria-label="Run end date"]', "2025-01-02")

        prev = ""
        import re as _re
        for label, expected in MAPPINGS:
            ok &= check(f"timeframe option selectable: {label}", select_timeframe(cdp, label))
            # current run id BEFORE clicking (the default archived run shows one too)
            cur_status = cdp.evaluate("(document.querySelector('.run-status')||{}).textContent||''") or ""
            cur = (_re.search(r"Archived Run · (\S+) ·", cur_status) or [None, None])[1]
            cdp.run_payloads.clear()
            cdp.evaluate(CLICK_RUN_JS)
            ok &= check(f"run completes for {label}", cdp.evaluate(f"({WAIT_NEW_RUN_JS})({json.dumps(cur or '')})", timeout=200))
            cdp.pump(0.6)
            status = cdp.evaluate("(document.querySelector('.run-status')||{}).textContent||''") or ""
            m = _re.search(r"Archived Run · (\S+) ·", status)
            run_id = m.group(1) if m else None
            prev = run_id or prev
            payload_tf = None
            if cdp.run_payloads:
                try:
                    payload_tf = json.loads(cdp.run_payloads[-1]).get("market", {}).get("timeframe")
                except Exception:
                    payload_tf = None
            cfg_tf = None
            if run_id and (runs / run_id / "config.json").is_file():
                cfg_tf = json.loads((runs / run_id / "config.json").read_text(encoding="utf-8")).get("market", {}).get("timeframe")
            print(f"   ACTUAL {label}: POST payload timeframe={payload_tf!r} archived config timeframe={cfg_tf!r}")
            ok &= check(f"{label} -> {expected}", payload_tf == expected and cfg_tf == expected, f"payload={payload_tf} config={cfg_tf}")

        cdp.ws.close()
    finally:
        if chrome is not None:
            chrome.terminate()
        bridge.terminate()
        shutil.rmtree(tmp, ignore_errors=True)
    print("TFMAPPING_QA_RESULT=" + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(e2e_main(main))
