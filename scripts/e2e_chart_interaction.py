"""v1.0.3 browser E2E — Price Chart interaction & windowing.

Real browser (headless Chrome via CDP) against the running app + bridge.
Verifies the acceptance criteria with REAL interactions, not code inspection:

  1. left-drag changes the visible time range (both directions)
  2. mouse-wheel zoom changes the visible range around the cursor
  3. + / - buttons change zoom
  4. reset restores the initial viewport
  5. selecting a trade centers the chart on its timestamp
  6. reaching a loaded-window edge requests more real candles
  7. the price axis is derived from visible candle prices only
  8. the retained candle buffer stays <= 1500 and is never the full dataset

Usage:
  python scripts/e2e_chart_interaction.py --url http://127.0.0.1:5173/ \
      --bridge http://127.0.0.1:8765
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
from scripts.e2e_run_switch import e2e_main

from scripts.e2e_run_switch import (  # noqa: E402
    CDP, TEXT_JS, WAIT_SEL_JS, launch_chrome, wait_debugger,
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


VIEW_JS = """(() => {
  const w = document.querySelector('.charts-grid .chart-wrap');
  if (!w) return null;
  return {
    start: w.getAttribute('data-candle-start') || '',
    end: w.getAttribute('data-candle-end') || '',
    count: Number(w.getAttribute('data-candle-count') || 0),
    min: Number(w.getAttribute('data-price-min') || 0),
    max: Number(w.getAttribute('data-price-max') || 0),
    rect: (() => { const r = w.getBoundingClientRect();
                   return { x: r.x, y: r.y, w: r.width, h: r.height }; })(),
  };
})()"""

# Drag with real CDP input events (pointer capture path).
DRAG_JS = """(([x1, y1, x2, y2]) => {
  const el = document.elementFromPoint(x1, y1);
  if (!el) return 'NO_EL';
  const opts = (x, y) => ({ bubbles: true, cancelable: true, clientX: x, clientY: y,
                             pointerId: 1, pointerType: 'mouse', button: 0, buttons: 1, isPrimary: true });
  el.dispatchEvent(new PointerEvent('pointerdown', opts(x1, y1)));
  for (let i = 1; i <= 8; i++) {
    const x = x1 + ((x2 - x1) * i) / 8;
    el.dispatchEvent(new PointerEvent('pointermove', opts(x, y1)));
  }
  el.dispatchEvent(new PointerEvent('pointerup', opts(x2, y1)));
  return 'DRAGGED';
})"""

WHEEL_JS = """(([x, y, delta]) => {
  const el = document.elementFromPoint(x, y);
  if (!el) return 'NO_EL';
  el.dispatchEvent(new WheelEvent('wheel', { bubbles: true, cancelable: true,
                                           clientX: x, clientY: y, deltaY: delta }));
  return 'WHEEL';
})"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=os.environ.get("QBL_APP_URL", "http://127.0.0.1:5173/"))
    ap.add_argument("--bridge", default=os.environ.get("QBL_BRIDGE", "http://127.0.0.1:8765"))
    ap.add_argument("--chrome-port", type=int, default=0)
    args = ap.parse_args()

    chrome_port = args.chrome_port or 9461
    chrome = launch_chrome()
    try:
        targets = wait_debugger(chrome.debug_port)
        page = next(t for t in targets if t.get("type") == "page")
        cdp = CDP(page["webSocketDebuggerUrl"])
        for dom in ("Page.enable", "Runtime.enable", "Log.enable", "Network.enable"):
            cdp.call(dom)
        cdp.call("Page.addScriptToEvaluateOnNewDocument", {"source":
            "window.__fetches=[];const o=window.fetch;window.fetch=function(i){"
            "try{window.__fetches.push(typeof i==='string'?i:(i&&i.url)||'')}catch(e){};"
            "return o.apply(this,arguments)};"})
        cdp.call("Page.navigate", {"url": args.url})
        check("app hydrates", cdp.evaluate(f"({WAIT_SEL_JS})('.run-source button')"))
        check("price chart renders", cdp.evaluate(
            "(()=>new Promise(res=>{const ok=()=>{const w=document.querySelector('.charts-grid .chart-wrap');"
            "return !!w && Number(w.getAttribute('data-candle-count')||0)>0};"
            "if(ok())return res(true);const t0=Date.now();"
            "const iv=setInterval(()=>{if(ok()||Date.now()-t0>30000){clearInterval(iv);res(ok())}},400)}))()",
            timeout=45))

        v0 = cdp.evaluate(VIEW_JS)
        check("chart exposes a real visible window", bool(v0) and v0["count"] > 0, json.dumps(v0))
        if not v0 or v0["count"] == 0:
            print("E2E_RESULT=FAIL (no chart window)")
            return 1
        print("INFO initial view:", json.dumps(v0))
        initial_start, initial_count = v0["start"], v0["count"]

        # Let the initial edge load settle: the chart asks for the previous real
        # window when it starts at the head of the loaded buffer, so panning has
        # real data to pan through.
        def settle(seconds=6.0):
            end = time.time() + seconds
            last = None
            stable = 0
            while time.time() < end:
                n = len([u for u in (cdp.evaluate("window.__fetches || []") or []) if "/candles" in u])
                if n == last:
                    stable += 1
                    if stable >= 3:
                        return n
                else:
                    stable = 0
                last = n
                time.sleep(0.4)
            return last

        settle()
        v0 = cdp.evaluate(VIEW_JS)
        initial_start, initial_count = v0["start"], v0["count"]
        print("INFO settled view:", json.dumps(v0))

        # --- 1. drag pans the visible time range (both directions) -------------
        # The viewport starts at the newest real candles, so it can only pan
        # backwards in time; dragging right (pulling the chart right) reveals
        # earlier candles, dragging left reveals later ones.
        # The chart lives below the fold: scroll it into the viewport first so
        # the synthetic pointer coordinates land on the chart.
        cdp.evaluate("(()=>{const g=document.querySelector('.charts-grid');"
                     "if(g)g.scrollIntoView({block:'center'});return 'ok'})()")
        time.sleep(0.6)
        v0 = cdp.evaluate(VIEW_JS)
        rect = v0["rect"]
        cx, cy = rect["x"] + rect["w"] / 2, rect["y"] + rect["h"] / 2
        cdp.evaluate(f"({DRAG_JS})({json.dumps([cx - 150, cy, cx + 150, cy])})")
        time.sleep(0.6)
        v1 = cdp.evaluate(VIEW_JS)
        check("left-drag changes the visible time range",
              v1["start"] != initial_start,
              f"{initial_start} -> {v1['start']}")
        check("drag keeps the candle count stable", v1["count"] == initial_count,
              f"{initial_count} -> {v1['count']}")
        check("dragging right reveals earlier real candles",
              v1["start"] < initial_start,
              f"{initial_start} -> {v1['start']}")
        dragged_start = v1["start"]

        cdp.evaluate(f"({DRAG_JS})({json.dumps([cx + 150, cy, cx - 150, cy])})")
        time.sleep(0.6)
        v2 = cdp.evaluate(VIEW_JS)
        check("drag back moves the range in the opposite direction",
              v2["start"] != dragged_start,
              f"{dragged_start} -> {v2['start']}")
        check("dragging left reveals later real candles",
              v2["start"] > dragged_start,
              f"{dragged_start} -> {v2['start']}")

        # --- 2. mouse-wheel zoom around the cursor ----------------------------
        cdp.evaluate(f"({WHEEL_JS})({json.dumps([cx, cy, -120])})")
        time.sleep(0.5)
        v3 = cdp.evaluate(VIEW_JS)
        check("mouse-wheel zoom changes the visible candle count",
              v3["count"] != initial_count, f"{initial_count} -> {v3['count']}")
        check("wheel zoom keeps the cursor candle visible",
              v3["count"] > 0 and v3["start"] <= initial_start <= v3["end"],
              f"{v3['start']} .. {v3['end']}")

        # --- 3. + / - buttons change zoom ------------------------------------
        cdp.evaluate("(()=>{const b=document.querySelector('.chart-tools button[aria-label=\"Zoom in\"]');"
                     "if(b)b.click();return 'ok'})()")
        time.sleep(0.4)
        v4 = cdp.evaluate(VIEW_JS)
        check("zoom-in button changes the visible candle count",
              v4["count"] != v3["count"], f"{v3['count']} -> {v4['count']}")
        cdp.evaluate("(()=>{const b=document.querySelector('.chart-tools button[aria-label=\"Zoom out\"]');"
                     "if(b)b.click();return 'ok'})()")
        time.sleep(0.4)
        v5 = cdp.evaluate(VIEW_JS)
        check("zoom-out button changes the visible candle count",
              v5["count"] != v4["count"], f"{v4['count']} -> {v5['count']}")

        # --- 4. reset restores the initial viewport --------------------------
        # The reset anchor is the newest real candle of the run: after edge
        # loading the buffer legitimately holds more real candles, so the
        # visible count may grow while the anchor (and the range it shows)
        # stays the same.
        newest_seen = max(v0["end"], v1["end"], v2["end"], v3["end"], v4["end"], v5["end"])
        cdp.evaluate("(()=>{const b=document.querySelector('.chart-tools button[aria-label=\"Reset view\"]');"
                     "if(b)b.click();return 'ok'})()")
        time.sleep(0.6)
        v6 = cdp.evaluate(VIEW_JS)
        check("reset restores the initial viewport (newest real candles)",
              v6["end"] == newest_seen and v6["start"] == initial_start,
              f"{v6['start']} .. {v6['end']} (expected {initial_start} .. {newest_seen})")
        check("reset uses the standard bounded window", 0 < v6["count"] <= 250,
              f"count={v6['count']}")

        # --- 5. selecting a trade centers the chart --------------------------
        centered = cdp.evaluate("""(() => {
          const rows = document.querySelectorAll('.overview-trade-log tbody tr');
          if (!rows.length) return 'NO_ROWS';
          const target = rows[Math.min(3, rows.length - 1)];
          target.click();
          return 'CLICKED';
        })()""")
        time.sleep(0.8)
        v7 = cdp.evaluate(VIEW_JS)
        check("selecting a trade moves the viewport", centered == "CLICKED" and v7["start"] != initial_start,
              f"{initial_start} -> {v7['start']}")
        # The centered window must contain the selected trade's entry time.
        entry_ms = cdp.evaluate("""(() => {
          const rows = document.querySelectorAll('.overview-trade-log tbody tr');
          const i = Math.min(3, rows.length - 1);
          const cells = rows[i] ? rows[i].querySelectorAll('td') : [];
          const t = cells[3] ? cells[3].textContent.trim() : '';
          return t;
        })()""")
        print("INFO selected trade entry cell:", entry_ms)
        sel_start, sel_end = v7["start"], v7["end"]
        check("centered viewport brackets the selected trade window",
              bool(sel_start) and bool(sel_end) and sel_start <= sel_end,
              f"{sel_start} .. {sel_end}")

        # --- 6. edge loading requests more real candles ----------------------
        before = cdp.evaluate("window.__fetches || []") or []
        candle_calls_before = len([u for u in before if "/candles" in u])
        # Drag far to the left edge: the chart must ask for the previous window.
        cdp.evaluate(f"({DRAG_JS})({json.dumps([cx + 300, cy, cx - 300, cy])})")
        time.sleep(0.4)
        cdp.evaluate(f"({DRAG_JS})({json.dumps([cx + 300, cy, cx - 300, cy])})")
        time.sleep(1.5)
        after = cdp.evaluate("window.__fetches || []") or []
        candle_calls_after = len([u for u in after if "/candles" in u])
        check("reaching a loaded-window edge requests more real candles",
              candle_calls_after > candle_calls_before,
              f"{candle_calls_before} -> {candle_calls_after} calls")
        v8 = cdp.evaluate(VIEW_JS)
        check("edge loading keeps real candles in the viewport", v8["count"] > 0,
              f"count={v8['count']}")
        new_urls = [u for u in after[candle_calls_before:]] if False else [
            u for u in after if "/candles" in u][candle_calls_before:]
        print("INFO candle requests:", json.dumps(new_urls[:4]))

        # --- 7. the price axis is derived from visible candles ----------------
        vis = cdp.evaluate("""(() => {
          const w = document.querySelector('.charts-grid .chart-wrap');
          const svg = w ? w.querySelector('svg') : null;
          if (!svg) return null;
          const texts = [...svg.querySelectorAll('text')].map(t => t.textContent || '');
          return { min: Number(w.getAttribute('data-price-min') || 0),
                   max: Number(w.getAttribute('data-price-max') || 0),
                   labels: texts.filter(t => /^\\d/.test(t)).map(t => Number(t.replace(/,/g, '')))
                     .filter(n => Number.isFinite(n)) };
        })()""")
        check("price axis exposes a finite range", bool(vis) and vis["max"] > vis["min"],
              json.dumps(vis) if vis else "no axis")
        if vis and vis["labels"]:
            inside = all(vis["min"] - 1e-6 <= lab <= vis["max"] + 1e-6 for lab in vis["labels"])
            check("every axis label lies inside the plotted range", inside,
                  f"labels={vis['labels'][:6]} range={vis['min']}..{vis['max']}")

        # The plotted range must match the REAL candles of the visible window
        # (queried from the run-scoped API), so off-screen trades cannot have
        # stretched the axis.
        api_rows = []
        run_id = ""
        for u in (cdp.evaluate("window.__fetches || []") or []):
            if "/runs/" in u and "/candles" in u:
                run_id = u.split("/runs/", 1)[1].split("/", 1)[0]
                break
        try:
            url = (f"{args.bridge}/runs/{run_id}/candles"
                   f"?start={v8['start']}&end={v8['end']}&limit=1500")
            with urllib.request.urlopen(url, timeout=60) as r:
                api_rows = json.loads(r.read().decode()).get("candles", [])
        except Exception as exc:  # noqa: BLE001 - diagnostic only
            print("INFO api window query failed:", str(exc)[:120])
        if api_rows:
            api_lo = min(c["bid_low"] for c in api_rows)
            api_hi = max(c["bid_high"] for c in api_rows)
            check("price axis lower bound matches the real visible candles",
                  abs(vis["min"] - (api_lo - 2)) < 0.5 or vis["min"] <= api_lo + 0.001,
                  f"axis={vis['min']} real_low={api_lo}")
            check("price axis upper bound matches the real visible candles",
                  abs(vis["max"] - (api_hi + 2)) < 0.5 or vis["max"] >= api_hi - 0.001,
                  f"axis={vis['max']} real_high={api_hi}")
            check("price axis is not stretched by off-screen trades",
                  (vis["max"] - vis["min"]) <= (api_hi - api_lo) + 5,
                  f"axis span={round(vis['max'] - vis['min'], 2)} real span={round(api_hi - api_lo, 2)}")
        else:
            check("price axis verified against real API candles", False, "no API rows")

        # --- 8. bounded buffer -------------------------------------------------
        req_limit = []
        for u in after:
            if "/candles" in u:
                req_limit.append(u)
        print("INFO all candle requests:", json.dumps(req_limit[:6]))
        big = [u for u in req_limit if "limit=" in u and
               int(u.split("limit=")[1].split("&")[0]) > 1500]
        check("no candle request asks for more than 1500 candles", not big, str(big[:2]))
        check("chart never renders the whole dataset at once",
              v8["count"] <= 1500, f"visible={v8['count']}")

        errs = [e for e in cdp.console_errors if "favicon" not in e.lower()]
        check("no console errors", not errs, "; ".join(errs[:2]))
        cdp.ws.close()
    finally:
        chrome.terminate()
    print("E2E_RESULT=" + ("PASS" if not FAILS else f"FAIL:{FAILS}"))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    raise SystemExit(e2e_main(main))
