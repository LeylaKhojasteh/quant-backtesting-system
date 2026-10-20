"""Phase 3 full-year UI QA: publish a real full-year M15 run and load it in the
product UI, checking metrics/chart/trade log/inspector/analytics and errors.

Uses repo public (snapshotted/restored) and temp runs; one full-year run.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from scripts.e2e_run_switch import e2e_main, launch_bridge, require_bridge_port_free

os.environ.setdefault("no_proxy", "127.0.0.1,localhost")
os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

from scripts.e2e_run_switch import (  # noqa: E402
    CDP, TEXT_JS, WAIT_BADGE_JS, WAIT_SEL_JS, launch_chrome, wait_debugger,
)

APP = "http://127.0.0.1:5173/"
BRIDGE = "http://127.0.0.1:8765"
PUBLIC = ROOT / "Quant-Backtest-Lab" / "public"
MANIFEST = PUBLIC / "adapters-run-manifest.json"

_POLL = "const t0=Date.now();const iv=setInterval(()=>{if(ok()||Date.now()-t0>MAX){clearInterval(iv);res(ok());}},250);"
CLICK_TEXT_JS = """(([root,text]) => { const el=[...document.querySelectorAll(root)].find(b=>b.textContent.trim()===text||b.textContent.includes(text)); if(!el) return 'NO'; for (const t of ['pointerdown','mousedown','mouseup','click']) el.dispatchEvent(new MouseEvent(t,{bubbles:true,cancelable:true,view:window})); return 'OK'; })"""
CLICK_ARIA_JS = """((label) => { const el=document.querySelector('[aria-label="'+label.replace(/"/g,'\\\\"')+'"]'); if(!el) return 'NO'; for (const t of ['pointerdown','mousedown','mouseup','click']) el.dispatchEvent(new MouseEvent(t,{bubbles:true,cancelable:true,view:window})); return 'OK'; })"""
WAIT_METRICS_JS = ("(text => new Promise(res => { const ok=()=>{const e=document.querySelector('.metrics-grouped'); return !!e && e.textContent.includes(text);};"
                   " if(ok())return res(true);" + _POLL.replace("MAX", "30000") + "}))")
WAIT_LIB_JS = ("(rid => new Promise(res => { const ok=()=>[...document.querySelectorAll('.lib-row')].some(r=>r.textContent.includes(rid));"
               " if(ok())return res(true);" + _POLL.replace("MAX", "30000") + "}))")
WAIT_SEL = "(sel => new Promise(res => { const ok=()=>!!document.querySelector(sel); if(ok())return res(true);" + _POLL.replace("MAX", "30000") + "}))"


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


def main() -> int:
    ok = True
    manifest_before = MANIFEST.read_text(encoding="utf-8") if MANIFEST.is_file() else None
    files_before = set(p.name for p in PUBLIC.glob("runs-*-result.json"))
    tmp = Path(tempfile.mkdtemp(prefix="qbl-fy-ui-"))
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
        # publish a real full-year M15 run through the bridge
        body = json.dumps({
            "strategy": {"selector": "candle_direction"},
            "market": {"dataset_id": "xauusd_dukas_ticks", "symbol": "XAUUSD", "timeframe": "15min",
                       "start": "2025-01-01", "end": "2025-12-31"},
            "execution": {"stop_distance": 5, "take_profit_distance": 10},
            "publish": True,
        }).encode()
        t0 = time.perf_counter()
        req = urllib.request.Request(f"{BRIDGE}/runs", data=body, headers={"Content-Type": "application/json"}, method="POST")
        resp = json.loads(_opener.open(req, timeout=600).read())
        run_id = resp["run_id"]
        print(f"full-year run {run_id} in {time.perf_counter()-t0:.1f}s")
        ok &= check("full-year run archived+published", (PUBLIC / f"runs-{run_id}-result.json").is_file())
        try:
            with _opener.open(f"{BRIDGE}/runs/{run_id}/report", timeout=20) as r:
                rpt = r.read().decode("utf-8", "replace")
                ctype = r.headers.get("Content-Type", "")
            ok &= check("standalone report renders", "text/html" in ctype and "Trades (5749)" in rpt)
        except Exception as exc:  # noqa: BLE001
            ok &= check("standalone report renders", False, str(exc))

        chrome = launch_chrome()
        t = wait_debugger(chrome.debug_port)
        page = next(x for x in t if x.get("type") == "page")
        cdp = CDP(page["webSocketDebuggerUrl"])
        for dom in ("Page.enable", "Runtime.enable", "Log.enable", "DOM.enable"):
            cdp.call(dom)
        cdp.call("Page.navigate", {"url": APP})
        time.sleep(3)
        cdp.call("Emulation.setDeviceMetricsOverride", {"width": 1440, "height": 900, "deviceScaleFactor": 1, "mobile": False})
        time.sleep(0.6)

        # open the full-year run from the library
        cdp.evaluate("(()=>{const b=document.querySelector('.library-button'); for (const t of ['pointerdown','mousedown','mouseup','click']) b.dispatchEvent(new MouseEvent(t,{bubbles:true,cancelable:true,view:window}));})()")
        cdp.evaluate(f"({WAIT_SEL})('.workspace-dialog')")
        cdp.evaluate(f"({CLICK_TEXT_JS})(['.workspace-tabs button','Runs'])")
        ok &= check("full-year run listed in library", cdp.evaluate(f"({WAIT_LIB_JS})({json.dumps(run_id)})"))
        t0 = time.perf_counter()
        cdp.evaluate(f"({CLICK_ARIA_JS})({json.dumps(f'Open {run_id} in dashboard')})")
        ok &= check("full-year run opens in dashboard", cdp.evaluate(f"({WAIT_BADGE_JS})('ARCHIVED RUN')", timeout=60))
        load_s = time.perf_counter() - t0
        ok &= check("metrics render (5749 trades)", cdp.evaluate(f"({WAIT_METRICS_JS})('5749')", timeout=60))
        ok &= check("net PnL renders", cdp.evaluate(f"({WAIT_METRICS_JS})('-3,711.54')", timeout=30))
        ok &= check("price chart renders", cdp.evaluate(f"({WAIT_SEL})('.charts-grid svg')", timeout=30))
        ok &= check("trade log renders", cdp.evaluate(f"({WAIT_SEL})('.overview-trade-log tbody tr')", timeout=30))
        print(f"UI load-to-metrics {load_s:.2f}s")
        cdp.call("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Escape", "code": "Escape", "windowsVirtualKeyCode": 27})
        cdp.call("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Escape", "code": "Escape", "windowsVirtualKeyCode": 27})
        time.sleep(0.4)
        # inspector + analytics
        cdp.evaluate(f"({CLICK_TEXT_JS})(['.navigation button','Trade Inspector'])")
        ok &= check("trade inspector works", cdp.evaluate(f"({WAIT_SEL})('.focused-inspector')", timeout=30))
        cdp.evaluate(f"({CLICK_TEXT_JS})(['.navigation button','Backtest'])")
        time.sleep(0.3)
        cdp.evaluate(f"({CLICK_TEXT_JS})(['.result-tabs button','Analytics'])")
        ok &= check("analytics renders", cdp.evaluate(f"({WAIT_SEL})('.analytics-view')", timeout=30))

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
    raise SystemExit(e2e_main(main))
