"""Lazy chart E2E: main page light, charts mount only after Load Chart.

Verifies with the shared harness (lock, dynamic DevTools port, unique profile):
  * the main page makes no candle/tick requests and renders no chart/trade log;
  * the standalone Price Chart view fetches nothing until "Load Chart";
  * after loading it renders and pan/zoom/reset/center/fullscreen work;
  * the Trade Inspector chart is also gated and works after loading.

Usage:
    python scripts/e2e_lazy_charts.py --url http://127.0.0.1:5173/ --bridge http://127.0.0.1:8765
"""
import argparse
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.e2e_run_switch import (  # noqa: E402
    CDP, TEXT_JS, WAIT_SEL_JS, e2e_main, launch_chrome, wait_debugger,
)

os.environ.setdefault("no_proxy", "127.0.0.1,localhost")
os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")
urllib.request.install_opener(urllib.request.build_opener(urllib.request.ProxyHandler({})))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

FAILS: list[str] = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f" — {detail}" if detail else ""))
    if not cond:
        FAILS.append(name)
    return bool(cond)


RECORDER = """
(() => {
  window.__fetches = [];
  const of = window.fetch;
  window.fetch = function (i, init) {
    try { window.__fetches.push({u: typeof i === 'string' ? i : (i && i.url) || '', op: (init && init.method) || 'GET'}); } catch (e) {}
    return of.apply(this, arguments);
  };
})();
"""

CLICK = """(([root, text]) => { const el=[...document.querySelectorAll(root)].find(b=>(b.textContent||'').includes(text));
  if(!el) return 'NO'; for (const t of ['pointerdown','mousedown','mouseup','click']) el.dispatchEvent(new MouseEvent(t,{bubbles:true,cancelable:true,view:window})); return 'OK'; })"""
CLICK_ARIA = """((label) => { const el=document.querySelector('[aria-label="'+label.replace(/"/g,'\\\\"')+'"]');
  if(!el) return 'NO'; for (const t of ['pointerdown','mousedown','mouseup','click']) el.dispatchEvent(new MouseEvent(t,{bubbles:true,cancelable:true,view:window})); return 'OK'; })"""
CLICK_FS = """((mode) => { const b=[...document.querySelectorAll('.fullscreen-toggle')].find(x=>(x.getAttribute('aria-label')||'').startsWith(mode));
  if(!b) return 'NO'; for (const t of ['pointerdown','mousedown','mouseup','click']) b.dispatchEvent(new MouseEvent(t,{bubbles:true,cancelable:true,view:window})); return 'OK'; })"""
DRAG = """(([x1,y1,x2,y2]) => { const el=document.elementFromPoint(x1,y1); if(!el) return 'NO_EL';
  const o=(x,y)=>({bubbles:true,cancelable:true,clientX:x,clientY:y,pointerId:1,pointerType:'mouse',button:0,buttons:1,isPrimary:true});
  el.dispatchEvent(new PointerEvent('pointerdown',o(x1,y1)));
  for(let i=1;i<=8;i++) el.dispatchEvent(new PointerEvent('pointermove',o(x1+((x2-x1)*i)/8,y1)));
  el.dispatchEvent(new PointerEvent('pointerup',o(x2,y1))); return 'DRAGGED'; })"""
WHEEL = """(([x,y,d]) => { const el=document.elementFromPoint(x,y); if(!el) return 'NO_EL';
  el.dispatchEvent(new WheelEvent('wheel',{bubbles:true,cancelable:true,clientX:x,clientY:y,deltaY:d})); return 'WHEEL'; })"""

VIEW = """(() => {
  const w = document.querySelector('.charts-grid .chart-wrap, .chart-stage .chart-wrap');
  if (!w) return null;
  const r = w.getBoundingClientRect();
  return { start: w.getAttribute('data-candle-start') || '', end: w.getAttribute('data-candle-end') || '',
           count: Number(w.getAttribute('data-candle-count') || 0),
           rect: { x: r.x, y: r.y, w: r.width, h: r.height } };
})()"""


def count_calls(cdp, kind):
    urls = cdp.evaluate("(window.__fetches||[]).map(f=>f.u)") or []
    return len([u for u in urls if kind in u])


def settle_requests(cdp, quiet=1.2, timeout=8.0):
    """Wait until no new fetch has been recorded for `quiet` seconds.

    Edge loading from the standalone chart can still be in flight when the
    view is switched; this prevents counting that request as an inspector
    request.
    """
    end = time.time() + timeout
    last = None
    stable_since = time.time()
    n = 0
    while time.time() < end:
        n = int(cdp.evaluate("(window.__fetches||[]).length") or 0)
        if n != last:
            last = n
            stable_since = time.time()
        elif time.time() - stable_since >= quiet:
            return n
        time.sleep(0.2)
    return n


def wait_chart(cdp, timeout=45):
    return cdp.evaluate(
        "(()=>new Promise(res=>{const ok=()=>{const w=document.querySelector('.chart-stage .chart-wrap');"
        "return !!w && Number(w.getAttribute('data-candle-count')||0)>0};"
        "if(ok())return res(true);const t0=Date.now();"
        "const iv=setInterval(()=>{if(ok()||Date.now()-t0>20000){clearInterval(iv);res(ok())}},250)}))()",
        timeout=timeout,
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=os.environ.get("QBL_APP_URL", "http://127.0.0.1:5173/"))
    ap.add_argument("--bridge", default=os.environ.get("QBL_BRIDGE", "http://127.0.0.1:8765"))
    ap.add_argument("--chrome-port", type=int, default=0)
    args = ap.parse_args()

    chrome = launch_chrome()
    try:
        targets = wait_debugger(chrome.debug_port)
        page = next(t for t in targets if t.get("type") == "page")
        cdp = CDP(page["webSocketDebuggerUrl"])
        for dom in ("Page.enable", "Runtime.enable", "Log.enable", "Network.enable"):
            cdp.call(dom)
        cdp.call("Page.addScriptToEvaluateOnNewDocument", {"source": RECORDER})
        cdp.call("Page.navigate", {"url": args.url})

        check("app hydrates", cdp.evaluate(f"({WAIT_SEL_JS})('.run-source button')"))
        check("run result rendered", cdp.evaluate(f"({WAIT_SEL_JS})('.metrics-grouped')"))
        time.sleep(1.0)

        # --- main page is light ------------------------------------------------
        check("main page has no price chart", cdp.evaluate("!document.querySelector('.charts-grid .chart-wrap')"))
        check("main page has no trade log table", cdp.evaluate("!document.querySelector('.overview-trade-log')"))
        check("main page requests no candles", count_calls(cdp, "/candles") == 0, str(count_calls(cdp, "/candles")))
        check("main page requests no ticks", count_calls(cdp, "/ticks") == 0, str(count_calls(cdp, "/ticks")))
        check("Cumulative PnL panel is full width",
              cdp.evaluate("!!document.querySelector('.charts-grid.charts-single .equity')"))
        eq_w = cdp.evaluate("(() => { const e=document.querySelector('.charts-grid.charts-single'); return e?Math.round(e.getBoundingClientRect().width):0 })()")
        check("Cumulative PnL spans the content area", eq_w > 700, f"{eq_w}px")

        # --- standalone price chart: no fetch before Load ----------------------
        check("Price Chart tab opens", cdp.evaluate(f"({CLICK})(['.navigation button','Price Chart'])" ) == "OK")
        check("lazy load prompt shown", cdp.evaluate(f"({WAIT_SEL_JS})('.chart-load')"))
        time.sleep(0.6)
        check("no candle request before Load Chart", count_calls(cdp, "/candles") == 0,
              str(count_calls(cdp, "/candles")))

        check("Load Chart clicked", cdp.evaluate(f"({CLICK})(['.chart-load button','Load Chart'])" ) == "OK")
        check("standalone chart renders after Load", wait_chart(cdp))
        after_load = count_calls(cdp, "/candles")
        check("candles requested only after Load", after_load > 0, f"{after_load} calls")

        # --- interactions ------------------------------------------------------
        cdp.evaluate("(()=>{const g=document.querySelector('.chart-stage');if(g)g.scrollIntoView({block:'center'});return 'ok'})()")
        time.sleep(0.5)
        v0 = cdp.evaluate(VIEW)
        check("chart exposes a real window", bool(v0) and v0["count"] > 0, json.dumps(v0))
        if not v0 or not v0["count"]:
            raise RuntimeError("standalone chart has no candles")

        # 1) the initial count is frozen: automatic edge loading must not change
        #    the visible range (no re-computation from a growing buffer).
        settle_requests(cdp)
        v_stable = cdp.evaluate(VIEW)
        check("initial count does not change with automatic edge loading",
              v_stable["count"] == v0["count"], f"{v0['count']} -> {v_stable['count']}")
        check("automatic edge loading does not jump the viewport",
              v_stable["start"] == v0["start"] and v_stable["end"] == v0["end"],
              f"{v0['start']}..{v0['end']} -> {v_stable['start']}..{v_stable['end']}")

        rect = v_stable["rect"]
        cx, cy = rect["x"] + rect["w"] / 2, rect["y"] + rect["h"] / 2

        # 2) a drag really moves the viewport timestamp (not just the buffer).
        cdp.evaluate(f"({DRAG})({json.dumps([cx - 150, cy, cx + 150, cy])})")
        time.sleep(0.5)
        v1 = cdp.evaluate(VIEW)
        check("drag moves the viewport timestamp", v1["start"] != v_stable["start"],
              f"{v_stable['start']} -> {v1['start']}")
        check("drag keeps the candle count", v1["count"] == v_stable["count"],
              f"{v_stable['count']} -> {v1['count']}")

        cdp.evaluate(f"({WHEEL})({json.dumps([cx, cy, -120])})")
        time.sleep(0.5)
        v2 = cdp.evaluate(VIEW)
        check("wheel zoom changes the candle count", v2["count"] != v1["count"], f"{v1['count']} -> {v2['count']}")

        cdp.evaluate(f"({CLICK_ARIA})('Zoom in')")
        time.sleep(0.4)
        v3 = cdp.evaluate(VIEW)
        check("zoom-in button works", v3["count"] != v2["count"], f"{v2['count']} -> {v3['count']}")
        cdp.evaluate(f"({CLICK_ARIA})('Zoom out')")
        time.sleep(0.4)
        v4 = cdp.evaluate(VIEW)
        check("zoom-out button works", v4["count"] != v3["count"], f"{v3['count']} -> {v4['count']}")

        # 3) reset returns exactly to the initial viewport.
        cdp.evaluate(f"({CLICK_ARIA})('Reset view')")
        time.sleep(0.5)
        v5 = cdp.evaluate(VIEW)
        check("reset restores the initial viewport",
              v5["start"] == v0["start"] and v5["count"] == v0["count"],
              f"{v5['start']} count={v5['count']} (expected {v0['start']} count={v0['count']})")

        # 4) panning to the head triggers a real edge load without a jump: the
        #    visible range right after the drag must survive the prepend.
        calls_before_drag = count_calls(cdp, "/candles")
        drags = 0
        prev = v5["start"]
        for _ in range(6):
            cdp.evaluate(f"({DRAG})({json.dumps([cx - 420, cy, cx + 420, cy])})")
            time.sleep(0.35)
            drags += 1
            now = cdp.evaluate(VIEW)
            if now["start"] == prev:
                break
            prev = now["start"]
        v_head = cdp.evaluate(VIEW)
        settle_requests(cdp, quiet=1.2, timeout=10.0)
        v_head_after = cdp.evaluate(VIEW)
        calls_after = count_calls(cdp, "/candles")
        check("panning to the head requests more real candles", calls_after > calls_before_drag,
              f"{calls_before_drag} -> {calls_after} ({drags} drags)")
        check("edge loading does not jump the viewport",
              v_head_after["start"] == v_head["start"]
              and v_head_after["end"] == v_head["end"]
              and v_head_after["count"] == v_head["count"],
              f"{v_head['start']}..{v_head['end']} -> {v_head_after['start']}..{v_head_after['end']}")

        # 5) centering a trade moves the viewport.
        before_center = cdp.evaluate(VIEW)["start"]
        check("next trade centers the chart", cdp.evaluate(f"({CLICK_ARIA})('Next trade')") == "OK")
        time.sleep(0.7)
        after_center = cdp.evaluate(VIEW)["start"]
        check("centering moves the viewport", after_center != before_center, f"{before_center} -> {after_center}")

        check("fullscreen opens", cdp.evaluate(f"({CLICK_FS})('Enter fullscreen')") == "OK")
        time.sleep(0.6)
        check("fullscreen layout active", cdp.evaluate("!!document.querySelector('.panel.is-fullscreen')"))
        cdp.evaluate(f"({CLICK_FS})('Exit fullscreen')")
        time.sleep(0.5)
        check("fullscreen closes", cdp.evaluate("!document.querySelector('.panel.is-fullscreen')"))

        # --- Trade Inspector: also lazy ---------------------------------------
        # Let the standalone chart's edge loading settle before switching, so a
        # still-in-flight chart request is not mistaken for an inspector fetch.
        settle_requests(cdp)
        before_inspector = count_calls(cdp, "/candles")
        check("Trade Inspector opens", cdp.evaluate(f"({CLICK})(['.navigation button','Trade Inspector'])" ) == "OK")
        check("inspector shows lazy prompt", cdp.evaluate(f"({WAIT_SEL_JS})('.large-debugger .chart-load')"))
        time.sleep(1.0)
        settle_requests(cdp, quiet=1.0, timeout=6.0)
        check("no candle request before inspector Load",
              count_calls(cdp, "/candles") == before_inspector,
              f"{before_inspector} -> {count_calls(cdp, '/candles')}")

        check("inspector Load Chart clicked",
              cdp.evaluate(f"({CLICK})(['.large-debugger .chart-load button','Load Chart'])" ) == "OK")
        check("inspector chart renders after Load", wait_chart(cdp))
        check("inspector requested candle data after Load",
              count_calls(cdp, "/candles") > before_inspector,
              f"{before_inspector} -> {count_calls(cdp, '/candles')}")
        check("inspector chart is bounded (chart-stage present)",
              cdp.evaluate("!!document.querySelector('.large-debugger .chart-stage')"))

        check("inspector fullscreen opens",
              cdp.evaluate(f"({CLICK_ARIA})('Enter fullscreen Trade Debugger')") == "OK")
        time.sleep(0.5)
        check("inspector fullscreen layout active",
              cdp.evaluate("!!document.querySelector('.large-debugger.is-fullscreen')"))
        cdp.evaluate(f"({CLICK_ARIA})('Exit fullscreen Trade Debugger')")
        time.sleep(0.4)

        errs = [e for e in cdp.console_errors if "favicon" not in e.lower()]
        check("no console errors", not errs, "; ".join(errs[:2]))
        cdp.ws.close()
    finally:
        chrome.terminate()

    print("E2E_RESULT=" + ("PASS" if not FAILS else f"FAIL:{FAILS}"))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    raise SystemExit(e2e_main(main))
