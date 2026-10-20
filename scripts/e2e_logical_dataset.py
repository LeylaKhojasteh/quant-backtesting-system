"""Logical-dataset real-run E2E (Data Engine Phase 2).

Proves the normal product path: logical dataset (XAUUSD · Dukascopy) → controlled
timeframe → real date range → run through the Data Engine (Parquet ticks +
cached candles) → archive/result. Uses isolated temp runs/public; repo cache.
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
from scripts.e2e_run_switch import e2e_main, launch_bridge, require_bridge_port_free

from scripts.e2e_run_switch import (  # noqa: E402
    CDP, TEXT_JS, WAIT_BADGE_JS, WAIT_SEL_JS, WAIT_TEXT_JS, launch_chrome, wait_debugger,
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

APP = "http://127.0.0.1:5173/"
BRIDGE = "http://127.0.0.1:8765"
PUBLIC = ROOT / "Quant-Backtest-Lab" / "public"
MANIFEST = PUBLIC / "adapters-run-manifest.json"

_POLL = "const t0=Date.now();const iv=setInterval(()=>{if(ok()||Date.now()-t0>MAX){clearInterval(iv);res(ok());}},250);"
SET_INPUT_JS = """(([sel, value]) => { const el=document.querySelector(sel); if(!el) return 'NO_EL';
  const setter=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set;
  setter.call(el,value); el.dispatchEvent(new Event('input',{bubbles:true})); el.dispatchEvent(new Event('change',{bubbles:true})); return el.value; })"""
CLICK_ARIA_JS = """((label) => { const el=document.querySelector('[aria-label="'+label.replace(/"/g,'\\\\"')+'"]'); if(!el) return 'NO'; for (const t of ['pointerdown','mousedown','mouseup','click']) el.dispatchEvent(new MouseEvent(t,{bubbles:true,cancelable:true,view:window})); return 'OK'; })"""
CLICK_TEXT_JS = """(([root,text]) => { const el=[...document.querySelectorAll(root)].find(b=>b.textContent.trim()===text||b.textContent.includes(text)); if(!el) return 'NO'; for (const t of ['pointerdown','mousedown','mouseup','click']) el.dispatchEvent(new MouseEvent(t,{bubbles:true,cancelable:true,view:window})); return 'OK'; })"""
CLICK_RUN_JS = """(() => { const b=[...document.querySelectorAll('.real-run button')].find(x=>x.textContent.includes('Run Backtest')||x.textContent.includes('Running')); if(!b) return 'NO'; b.click(); return 'CLICKED'; })()"""
WAIT_ENABLED_JS = ("(() => new Promise(res => { const ok=()=>{const b=[...document.querySelectorAll('.real-run button')].find(x=>x.textContent.includes('Run Backtest'));return !!b && !b.disabled;};"
                   " if(ok())return res(true);" + _POLL.replace("MAX", "40000") + "}))()")
WAIT_NEW_RUN_JS = ("(prev => new Promise(res => { const ok=()=>{const t=(document.querySelector('.run-status')||{}).textContent||'';"
                   " const m=t.match(/Archived Run · (\\S+) ·/); return !!m && m[1]!==prev;};"
                   " if(ok())return res(true);" + _POLL.replace("MAX", "180000") + "}))")
WAIT_METRICS_JS = ("(text => new Promise(res => { const ok=()=>{const e=document.querySelector('.metrics-grouped'); return !!e && e.textContent.includes(text);};"
                   " if(ok())return res(true);" + _POLL.replace("MAX", "20000") + "}))")
WAIT_LIB_JS = ("(rid => new Promise(res => { const ok=()=>[...document.querySelectorAll('.lib-row')].some(r=>r.textContent.includes(rid));"
               " if(ok())return res(true);" + _POLL.replace("MAX", "20000") + "}))")


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


def main() -> int:
    ok = True
    manifest_before = MANIFEST.read_text(encoding="utf-8") if MANIFEST.is_file() else None
    files_before = set(p.name for p in PUBLIC.glob("runs-*-result.json"))
    tmp = Path(tempfile.mkdtemp(prefix="qbl-e2e-logical-"))
    runs = tmp / "runs"; runs.mkdir()
    require_bridge_port_free()
    bridge = launch_bridge(
        [sys.executable, "-m", "src.bridge", "--port", "8765", "--runs", str(runs), "--public", str(PUBLIC),
         "--data-dir", str(ROOT / "data"), "--strategy-dir", str(tmp / "strat"),
         "--studies", str(tmp / "st"), "--walkforwards", str(tmp / "wf"), "--montecarlo", str(tmp / "mc")],
        cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    chrome = None
    try:
        ok &= check("bridge starts", health(), BRIDGE)
        chrome = launch_chrome()
        t = wait_debugger(chrome.debug_port)
        page = next(x for x in t if x.get("type") == "page")
        cdp = CDP(page["webSocketDebuggerUrl"])
        for dom in ("Page.enable", "Runtime.enable", "Log.enable", "Network.enable", "DOM.enable"):
            cdp.call(dom)
        cdp.call("Page.navigate", {"url": APP})
        time.sleep(3)
        cdp.call("Emulation.setDeviceMetricsOverride", {"width": 1440, "height": 900, "deviceScaleFactor": 1, "mobile": False})
        time.sleep(0.8)

        ok &= check("real-run enabled", cdp.evaluate(WAIT_ENABLED_JS))
        ds_label = cdp.evaluate(f"({TEXT_JS})('[aria-label=\"Real run dataset\"]')") or ""
        ok &= check("logical dataset label shown", "XAUUSD · Dukascopy" in ds_label, ds_label[:60])
        ok &= check("no raw monthly filename as dataset",
                    "ticks.csv" not in ds_label and ".csv" not in ds_label, ds_label[:60])
        ok &= check("symbol derived read-only XAUUSD", (cdp.evaluate(f"({TEXT_JS})('[aria-label=\"Run symbol\"]')") or "").strip() == "XAUUSD")
        ok &= check("timeframe controlled", cdp.evaluate("!!document.querySelector('button[aria-label=\"Run timeframe\"]')"))
        status = cdp.evaluate(f"({TEXT_JS})('[aria-label=\"Dataset status\"]')") or ""
        ok &= check("dataset prepared state shown", "Data Ready" in status or "Preparation required" in status, status[:70])

        # golden range: 2025-01-31 (M15), through the real product path
        set_input(cdp, '[aria-label="Run start date"]', "2025-01-31")
        set_input(cdp, '[aria-label="Run end date"]', "2025-01-31")
        cur = (re.search(r"Archived Run · (\S+) ·", cdp.evaluate("(document.querySelector('.run-status')||{}).textContent||''") or "") or [None, None])[1]
        cdp.evaluate(CLICK_RUN_JS)
        ok &= check("logical run completes", cdp.evaluate(f"({WAIT_NEW_RUN_JS})({json.dumps(cur or '')})", timeout=200))
        cdp.pump(0.5)
        run_id = (re.search(r"Archived Run · (\S+) ·", cdp.evaluate("(document.querySelector('.run-status')||{}).textContent||''") or "") or [None, None])[1]
        ok &= check("run archived to temp runs", bool(run_id) and (runs / run_id / "result.json").is_file())
        cfg = json.loads((runs / run_id / "config.json").read_text(encoding="utf-8"))
        ok &= check("archive carries dataset_id provenance", cfg.get("market", {}).get("dataset_id") == "xauusd_dukas_ticks")
        ok &= check("archive carries data provenance", bool(cfg.get("data_provenance", {}).get("partitions")))
        ok &= check("golden 7-trade / -15.82 result",
                    cdp.evaluate(f"({WAIT_METRICS_JS})('-15.82')") and cdp.evaluate(f"({WAIT_METRICS_JS})('Total Trades')"),
                    (cdp.evaluate(f"({TEXT_JS})('.metrics-grouped')") or "")[:200])

        # Data Explorer real logical dataset
        cdp.evaluate(f"({CLICK_TEXT_JS})(['.navigation button','Data Explorer'])")
        time.sleep(0.8)
        de = cdp.evaluate(f"({TEXT_JS})('.explorer')") or ""
        ok &= check("Data Explorer shows logical dataset", "XAUUSD" in de and "Data Quality" in de, de[:200])
        ok &= check("Data Explorer shows no raw csv path", ".csv" not in de)
        cdp.evaluate(f"({CLICK_TEXT_JS})(['.navigation button','Backtest'])")
        time.sleep(0.3)

        # Run Library + legacy run_0001
        cdp.evaluate("(()=>{const b=document.querySelector('.library-button'); if(!b) return 'NO'; for (const t of ['pointerdown','mousedown','mouseup','click']) b.dispatchEvent(new MouseEvent(t,{bubbles:true,cancelable:true,view:window})); return 'OK';})()")
        cdp.evaluate(f"({WAIT_SEL_JS})('.workspace-dialog')")
        cdp.evaluate(f"({CLICK_TEXT_JS})(['.workspace-tabs button','Runs'])")
        ok &= check("run library lists new logical run", cdp.evaluate(f"({WAIT_LIB_JS})({json.dumps(run_id or '')})"))
        cdp.call("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Escape", "code": "Escape", "windowsVirtualKeyCode": 27})
        cdp.call("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Escape", "code": "Escape", "windowsVirtualKeyCode": 27})
        time.sleep(0.3)
        cdp.evaluate("(id=>{const b=[...document.querySelectorAll('.run-source button')].find(x=>x.textContent.includes(id)); if(b) b.click(); return !!b;})('run_0001')")
        ok &= check("legacy run_0001 still works", cdp.evaluate(f"({WAIT_BADGE_JS})('ARCHIVED RUN')"))

        errs = [e for e in cdp.console_errors if "favicon" not in e.lower() and "status of 40" not in e.lower()]
        hydra = [e for e in cdp.console_errors if "hydrat" in e.lower()]
        ok &= check("no unexpected console errors", len(errs) == 0, "; ".join(errs[:2]) if errs else "clean")
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
    raise SystemExit(e2e_main(main))
