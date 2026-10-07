"""Symbol/Timeframe contract QA (real browser + API).

Verifies: Symbol is read-only and dataset-derived, Timeframe is a controlled
select with the canonical engine values, arbitrary '44' is impossible from the
UI, and the backend rejects an unsupported timeframe with a structured error
(no raw parser text).
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("no_proxy", "127.0.0.1,localhost")
os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

from scripts.e2e_run_switch import CDP, TEXT_JS, WAIT_SEL_JS, launch_chrome, wait_debugger  # noqa: E402

APP = "http://127.0.0.1:5173/"
BRIDGE = "http://127.0.0.1:8765"
CLICK_ARIA_JS = """((label) => { const el=document.querySelector('[aria-label="'+label.replace(/"/g,'\\\\"')+'"]'); if(!el) return 'NO'; for (const t of ['pointerdown','mousedown','mouseup','click']) el.dispatchEvent(new MouseEvent(t,{bubbles:true,cancelable:true,view:window})); return 'OK'; })"""


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f" — {detail}" if detail else ""))
    return bool(cond)


def main() -> int:
    ok = True
    chrome = launch_chrome(9392)
    try:
        t = wait_debugger(9392)
        page = next(x for x in t if x.get("type") == "page")
        cdp = CDP(page["webSocketDebuggerUrl"])
        for dom in ("Page.enable", "Runtime.enable", "Log.enable", "DOM.enable"):
            cdp.call(dom)
        cdp.call("Page.navigate", {"url": APP})
        time.sleep(3)
        cdp.call("Emulation.setDeviceMetricsOverride", {"width": 1440, "height": 900, "deviceScaleFactor": 1, "mobile": False})
        time.sleep(0.6)

        # Symbol: not a free-text input; derived + dataset-matching
        ok &= check("symbol is not a text input", not cdp.evaluate("!!document.querySelector('input[aria-label=\"Run symbol\"]')"))
        ok &= check("symbol is derived/read-only", cdp.evaluate(f"({WAIT_SEL_JS})('[aria-label=\"Run symbol\"]')"))
        sym = (cdp.evaluate(f"({TEXT_JS})('[aria-label=\"Run symbol\"]')") or "").strip()
        ok &= check("symbol follows dataset (XAUUSD)", sym == "XAUUSD", sym)

        # Timeframe: select, not input
        ok &= check("timeframe is not a text input", not cdp.evaluate("!!document.querySelector('input[aria-label=\"Run timeframe\"]')"))
        ok &= check("timeframe trigger is a select", cdp.evaluate("!!document.querySelector('button[aria-label=\"Run timeframe\"], [role=combobox][aria-label=\"Run timeframe\"]')"))
        cdp.evaluate(f"({CLICK_ARIA_JS})('Run timeframe')")
        vals = []
        for _ in range(24):
            time.sleep(0.25)
            vals = cdp.evaluate("Array.from(document.querySelectorAll('[role=option]')).map(o=>o.getAttribute('data-value')||o.textContent.trim())") or []
            if vals:
                break
        expected_labels = ["1 minute (M1)", "5 minutes (M5)", "15 minutes (M15)", "30 minutes (M30)", "1 hour (H1)", "4 hours (H4)"]
        ok &= check("only supported timeframes appear (friendly labels)", vals == expected_labels, str(vals))
        # pick M15
        cdp.evaluate("(() => { const o=[...document.querySelectorAll('[role=option]')].find(x=>(x.getAttribute('data-value')||'')==='15min'); if(o) for (const t of ['pointerdown','mousedown','mouseup','click']) o.dispatchEvent(new MouseEvent(t,{bubbles:true,cancelable:true,view:window})); })()")
        time.sleep(0.4)
        ok &= check("M15 selected shows friendly label", "M15" in (cdp.evaluate(f"({TEXT_JS})('[aria-label=\"Run timeframe\"]')") or ""))

        # Arbitrary '44' impossible: no timeframe text entry exists
        ok &= check("no timeframe text entry to type 44", not cdp.evaluate("!!document.querySelector('input[aria-label=\"Run timeframe\"]')"))

        # Backend rejects an unsupported timeframe cleanly
        body = json.dumps({
            "strategy": {"selector": "candle_direction"},
            "market": {"symbol": "XAUUSD", "data_source": "x.csv", "start": "2025-01-02",
                       "end": "2025-01-02", "timeframe": "44"},
        }).encode()
        req = urllib.request.Request(f"{BRIDGE}/runs", data=body,
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            _opener.open(req, timeout=10)
            ok &= check("backend rejects timeframe 44", False, "unexpectedly accepted")
        except urllib.error.HTTPError as exc:
            payload = json.loads(exc.read().decode())
            code = payload.get("error", {}).get("code")
            msg = payload.get("error", {}).get("message", "")
            ok &= check("backend rejects timeframe 44 (unsupported_timeframe)", exc.code == 400 and code == "unsupported_timeframe", f"{exc.code} {code}")
            ok &= check("no raw parser text in backend error", "Invalid frequency" not in msg and "ValueError" not in msg, msg)

        # sidebar no-scroll + run visible + no overflow
        m = cdp.evaluate("(() => { const el=document.querySelector('.config'); const r=document.querySelector('.real-run .primary.run'); const rb=r?r.getBoundingClientRect():null; return {scroll: el.scrollHeight-el.clientHeight, runVisible: rb? (rb.top>=0 && rb.bottom<=window.innerHeight+1):false, overflow: document.documentElement.scrollWidth-window.innerWidth}; })()")
        ok &= check("sidebar no internal scroll", m["scroll"] <= 1, str(m["scroll"]))
        ok &= check("Run Backtest visible", m["runVisible"])
        ok &= check("no horizontal overflow", m["overflow"] <= 1, str(m["overflow"]))

        errs = [e for e in cdp.console_errors if "favicon" not in e.lower() and "status of 40" not in e.lower()]
        ok &= check("no console errors", len(errs) == 0, "; ".join(errs[:2]) if errs else "clean")
        cdp.ws.close()
    finally:
        chrome.terminate()
    print("TF_QA_RESULT=" + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
