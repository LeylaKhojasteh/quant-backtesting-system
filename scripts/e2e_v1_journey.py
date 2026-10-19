"""Final v1.0 user-journey E2E (compact smoke).

Normal product path with a SMALL real range: version, logical dataset, controlled
timeframe, price-term run, dashboard/inspector/timeline, Data Explorer, report,
library, account-enabled run (balance), and the research workspace tabs.
"""

import json
import os
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

APP = os.environ.get("QBL_APP_URL", "http://127.0.0.1:5173/")
BRIDGE = "http://127.0.0.1:8765"
PUBLIC = ROOT / "Quant-Backtest-Lab" / "public"
MANIFEST = PUBLIC / "adapters-run-manifest.json"

_POLL = "const t0=Date.now();const iv=setInterval(()=>{if(ok()||Date.now()-t0>MAX){clearInterval(iv);res(ok());}},250);"
SET_INPUT_JS = """(([sel, value]) => { const el=document.querySelector(sel); if(!el) return 'NO_EL';
  const setter=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set;
  setter.call(el,value); el.dispatchEvent(new Event('input',{bubbles:true})); el.dispatchEvent(new Event('change',{bubbles:true})); return el.value; })"""
CLICK_EL_JS = """((sel) => { const el=document.querySelector(sel); if(!el) return 'NO'; for (const t of ['pointerdown','mousedown','mouseup','click']) el.dispatchEvent(new MouseEvent(t,{bubbles:true,cancelable:true,view:window})); return 'OK'; })"""
CLICK_ARIA_JS = """((label) => { const el=document.querySelector('[aria-label="'+label.replace(/"/g,'\\\\"')+'"]'); if(!el) return 'NO'; for (const t of ['pointerdown','mousedown','mouseup','click']) el.dispatchEvent(new MouseEvent(t,{bubbles:true,cancelable:true,view:window})); return 'OK'; })"""
CLICK_TEXT_JS = """(([root,text]) => { const el=[...document.querySelectorAll(root)].find(b=>b.textContent.trim()===text||b.textContent.includes(text)); if(!el) return 'NO'; for (const t of ['pointerdown','mousedown','mouseup','click']) el.dispatchEvent(new MouseEvent(t,{bubbles:true,cancelable:true,view:window})); return 'OK'; })"""
CLICK_RUN_JS = """(() => { const b=[...document.querySelectorAll('.real-run button.primary')].find(x=>x.textContent.includes('Run Backtest')||x.textContent.includes('Backtest running')); if(!b) return 'NO'; b.click(); return 'CLICKED'; })()"""
WAIT_ENABLED_JS = ("(() => new Promise(res => { const ok=()=>{const b=[...document.querySelectorAll('.real-run button.primary')].find(x=>x.textContent.includes('Run Backtest'));return !!b && !b.disabled;};"
                   " if(ok())return res(true);" + _POLL.replace("MAX", "40000") + "}))()")
WAIT_NEW_RUN_JS = ("(prev => new Promise(res => { const ok=()=>{const t=(document.querySelector('.run-status')||{}).textContent||'';"
                   " const m=t.match(/Archived Run · (\\S+) ·/); return !!m && m[1]!==prev;};"
                   " if(ok())return res(true);" + _POLL.replace("MAX", "180000") + "}))")
WAIT_METRICS_JS = ("(text => new Promise(res => { const ok=()=>{const e=document.querySelector('.metrics-grouped'); return !!e && e.textContent.includes(text);};"
                   " if(ok())return res(true);" + _POLL.replace("MAX", "25000") + "}))")
WAIT_LIB_JS = ("(rid => new Promise(res => { const ok=()=>[...document.querySelectorAll('.lib-row')].some(r=>r.textContent.includes(rid));"
               " if(ok())return res(true);" + _POLL.replace("MAX", "25000") + "}))")


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


def open_library(cdp):
    cdp.evaluate(f"({CLICK_EL_JS})('.library-button')")
    cdp.evaluate(f"({WAIT_SEL_JS})('.workspace-dialog')")


def close_library(cdp):
    cdp.call("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Escape", "code": "Escape", "windowsVirtualKeyCode": 27})
    cdp.call("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Escape", "code": "Escape", "windowsVirtualKeyCode": 27})
    time.sleep(0.4)


def main() -> int:
    ok = True
    manifest_before = MANIFEST.read_text(encoding="utf-8") if MANIFEST.is_file() else None
    files_before = set(p.name for p in PUBLIC.glob("runs-*-result.json"))
    tmp = Path(tempfile.mkdtemp(prefix="qbl-e2e-v1-"))
    runs = tmp / "runs"; runs.mkdir()
    bridge = subprocess.Popen(
        [sys.executable, "-m", "src.bridge", "--port", "8765", "--runs", str(runs), "--public", str(PUBLIC),
         "--data-dir", str(ROOT / "data"), "--strategy-dir", str(tmp / "strat"),
         "--studies", str(tmp / "st"), "--walkforwards", str(tmp / "wf"), "--montecarlo", str(tmp / "mc")],
        cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    chrome = None
    try:
        ok &= check("bridge starts", health(), BRIDGE)
        chrome = launch_chrome(9408)
        t = wait_debugger(9408)
        page = next(x for x in t if x.get("type") == "page")
        cdp = CDP(page["webSocketDebuggerUrl"])
        for dom in ("Page.enable", "Runtime.enable", "Log.enable", "DOM.enable"):
            cdp.call(dom)
        cdp.call("Page.navigate", {"url": APP})
        time.sleep(3)
        cdp.call("Emulation.setDeviceMetricsOverride", {"width": 1440, "height": 900, "deviceScaleFactor": 1, "mobile": False})
        time.sleep(0.8)

        ok &= check("version v1.0.3 shown", "v1.0.3" in (cdp.evaluate(f"({TEXT_JS})('.engine')") or ""))
        ok &= check("logical dataset shown", "XAUUSD · Dukascopy" in (cdp.evaluate(f"({TEXT_JS})('[aria-label=\"Real run dataset\"]')") or ""))
        ok &= check("symbol derived XAUUSD", (cdp.evaluate(f"({TEXT_JS})('[aria-label=\"Run symbol\"]')") or "").strip() == "XAUUSD")
        ok &= check("timeframe controlled", cdp.evaluate("!!document.querySelector('button[aria-label=\"Run timeframe\"]')"))
        ok &= check("real-run enabled", cdp.evaluate(WAIT_ENABLED_JS))

        # price-term run (small real range)
        set_input(cdp, '[aria-label="Run start date"]', "2025-01-02")
        set_input(cdp, '[aria-label="Run end date"]', "2025-01-02")
        cur = (re.search(r"Archived Run · (\S+) ·", cdp.evaluate("(document.querySelector('.run-status')||{}).textContent||''") or "") or [None, None])[1]
        cdp.evaluate(CLICK_RUN_JS)
        completed = cdp.evaluate(f"({WAIT_NEW_RUN_JS})({json.dumps(cur or '')})", timeout=60)
        if not completed:
            # Diagnose: ask the bridge what the job did.
            try:
                import urllib.request as _u
                with _u.urlopen("http://127.0.0.1:8765/run-jobs/active", timeout=10) as _r:
                    _job = json.loads(_r.read().decode()).get("job") or {}
                print("INFO active job:", json.dumps(_job)[:400])
            except Exception as _exc:  # noqa: BLE001 - diagnostic only
                print("INFO active job unavailable:", str(_exc)[:120])
        ok &= check("price-term run completes", completed)
        cdp.pump(0.4)
        run_id = (re.search(r"Archived Run · (\S+) ·", cdp.evaluate("(document.querySelector('.run-status')||{}).textContent||''") or "") or [None, None])[1]
        ok &= check("result metrics render", cdp.evaluate(f"({WAIT_METRICS_JS})('Total PnL')"))
        ok &= check("price chart renders", cdp.evaluate(f"({WAIT_SEL_JS})('.charts-grid svg')"))
        ok &= check("trade log renders", cdp.evaluate(f"({WAIT_SEL_JS})('.overview-trade-log tbody tr')"))

        # trade inspector + timeline chronology
        cdp.evaluate(f"({CLICK_TEXT_JS})(['.navigation button','Trade Inspector'])")
        ok &= check("trade inspector works", cdp.evaluate(f"({WAIT_SEL_JS})('.focused-inspector')"))
        cdp.evaluate(f"({CLICK_TEXT_JS})(['.detail-tabs button','Timeline'])")
        ok &= check("timeline renders", cdp.evaluate(f"({WAIT_SEL_JS})('.trade-timeline .tt-time')"))
        times = cdp.evaluate("Array.from(document.querySelectorAll('.trade-timeline .tt-time')).map(e=>e.textContent.replace(' UTC','').trim())") or []
        import datetime as _dt
        nums = []
        for s in times:
            try:
                nums.append(_dt.datetime.strptime(s, "%Y-%m-%d %H:%M:%S").timestamp())
            except Exception:
                pass
        ok &= check("timeline chronology valid", len(nums) >= 4 and nums == sorted(nums), str(times[:3]))
        cdp.evaluate(f"({CLICK_TEXT_JS})(['.navigation button','Backtest'])")
        time.sleep(0.3)

        # Data Explorer real
        cdp.evaluate(f"({CLICK_TEXT_JS})(['.navigation button','Data Explorer'])")
        time.sleep(0.6)
        de = cdp.evaluate(f"({TEXT_JS})('.explorer')") or ""
        ok &= check("data explorer real logical data", "XAUUSD" in de and ".csv" not in de)
        cdp.evaluate(f"({CLICK_TEXT_JS})(['.navigation button','Backtest'])")
        time.sleep(0.3)

        # report + library + open archived
        with urllib.request.urlopen(f"{BRIDGE}/runs/{run_id}/report", timeout=20) as r:
            ok &= check("standalone report exists", "text/html" in r.headers.get("Content-Type", ""))
        open_library(cdp)
        cdp.evaluate(f"({CLICK_TEXT_JS})(['.workspace-tabs button','Runs'])")
        ok &= check("run appears in library", cdp.evaluate(f"({WAIT_LIB_JS})({json.dumps(run_id or '')})"))
        cdp.evaluate(f"({CLICK_ARIA_JS})({json.dumps(f'Open {run_id} in dashboard')})")
        ok &= check("archived run opens", cdp.evaluate(f"({WAIT_BADGE_JS})('ARCHIVED RUN')"))

        # reopen the workspace and confirm each module tab opens
        close_library(cdp)
        open_library(cdp)
        for tab in ("Compare", "Optimize", "Walk-Forward", "Monte Carlo"):
            res = cdp.evaluate(f"({CLICK_TEXT_JS})(['.workspace-tabs button','{tab}'])")
            if res != "OK":
                print("DBG tabs", cdp.evaluate("Array.from(document.querySelectorAll('.workspace-tabs button')).map(b=>b.textContent.trim())"))
            ok &= check(f"{tab} workflow opens", res == "OK", res)
            time.sleep(0.3)
        close_library(cdp)

        errs = [e for e in cdp.console_errors if "favicon" not in e.lower() and "status of 40" not in e.lower()]
        hydra = [e for e in cdp.console_errors if "hydrat" in e.lower()]
        ok &= check("no console errors", len(errs) == 0, "; ".join(errs[:2]) if errs else "clean")
        ok &= check("no hydration errors", len(hydra) == 0)
        cdp.ws.close()
    finally:
        if chrome is not None:
            chrome.terminate()
        bridge.terminate()
        if manifest_before is not None:
            MANIFEST.write_text(manifest_before, encoding="utf-8")
        for p in PUBLIC.glob("runs-*-result.json"):
            if p.name not in files_before:
                p.unlink()
        shutil.rmtree(tmp, ignore_errors=True)
    print("E2E_RESULT=" + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
