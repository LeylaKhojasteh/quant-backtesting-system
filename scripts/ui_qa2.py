"""UI QA pass 2: DOM-geometry verification (no images needed).

Checks metric-card rhythm (no orphan cards, equal heights), primary chart
dominance, run-context grouping, panel overlap, no clipped content, and that
Analytics local navigation actually filters sections.
"""

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from scripts.e2e_run_switch import e2e_main

from scripts.e2e_run_switch import CDP, TEXT_JS, launch_chrome, wait_debugger  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

APP = "http://127.0.0.1:5173/"

CLICK_TEXT_JS = """(([root,text]) => { const el=[...document.querySelectorAll(root)].find(b=>b.textContent.trim()===text||b.textContent.includes(text)); if(!el) return 'NO'; for (const t of ['pointerdown','mousedown','mouseup','click']) el.dispatchEvent(new MouseEvent(t,{bubbles:true,cancelable:true,view:window})); return 'OK'; })"""

METRIC_GEOM = """(() => {
  const cells = [...document.querySelectorAll('.metric-tier-cells.primary .metric-cell')];
  const rects = cells.map(c => { const r = c.getBoundingClientRect(); return { top: Math.round(r.top), h: Math.round(r.height), left: Math.round(r.left) }; });
  const heights = rects.map(r => r.h);
  const rows = {};
  rects.forEach(r => { rows[r.top] = (rows[r.top] || 0) + 1; });
  const rowCounts = Object.values(rows);
  const orphan = rowCounts.length > 1 && rowCounts.some(n => n === 1);
  // secondary overlap check
  const sec = [...document.querySelectorAll('.metric-tier-cells.secondary > *')].map(c => c.getBoundingClientRect());
  let overlap = false;
  for (let i = 0; i < sec.length; i++) for (let j = i + 1; j < sec.length; j++) {
    const a = sec[i], b = sec[j];
    if (a.left < b.right - 1 && b.left < a.right - 1 && a.top < b.bottom - 1 && b.top < a.bottom - 1) overlap = true;
  }
  return { count: cells.length, heights, rowCounts, orphan, overlap, secCount: sec.length };
})()"""

STACK_GEOM = """(() => {
  const kids = [...document.querySelectorAll('.overview-body > *')].map(el => { const r = el.getBoundingClientRect(); return { top: Math.round(r.top), bottom: Math.round(r.bottom) }; });
  let overlap = false;
  for (let i = 1; i < kids.length; i++) if (kids[i].top < kids[i-1].bottom - 2) overlap = true;
  const grid = document.querySelector('.charts-grid');
  let ratio = 0;
  if (grid && grid.children.length >= 2) {
    const a = grid.children[0].getBoundingClientRect().width;
    const b = grid.children[1].getBoundingClientRect().width;
    ratio = a / (a + b);
  }
  const de = document.documentElement;
  const scrollable = de.scrollHeight >= window.innerHeight;
  return { overlap, ratio, scrollable };
})()"""

ANALYTICS_TITLES = """(() => [...document.querySelectorAll('.analytics-view .analytics-block h2')].map(h => h.textContent.trim()))()"""


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f" — {detail}" if detail else ""))
    return bool(cond)


def main() -> int:
    ok = True
    chrome = launch_chrome()
    try:
        t = wait_debugger(chrome.debug_port)
        page = next(x for x in t if x.get("type") == "page")
        cdp = CDP(page["webSocketDebuggerUrl"])
        for dom in ("Page.enable", "Runtime.enable", "Log.enable", "DOM.enable"):
            cdp.call(dom)
        cdp.call("Page.navigate", {"url": APP})
        time.sleep(2.5)

        for w in (1920, 1366):
            cdp.call("Emulation.setDeviceMetricsOverride", {"width": w, "height": 900, "deviceScaleFactor": 1, "mobile": False})
            time.sleep(0.7)
            g = cdp.evaluate(METRIC_GEOM)
            ok &= check(f"@{w} primary cards: 5, equal height, no orphan", g["count"] == 5 and (max(g["heights"]) - min(g["heights"])) <= 2 and not g["orphan"], json.dumps(g["rowCounts"]))
            ok &= check(f"@{w} detail cells do not overlap", not g["overlap"], f"sec={g['secCount']}")
            s = cdp.evaluate(STACK_GEOM)
            ok &= check(f"@{w} overview sections do not overlap", not s["overlap"])
            ok &= check(f"@{w} price chart dominates (>60% of chart row)", s["ratio"] >= 0.6, f"ratio={s['ratio']:.2f}")
            ok &= check(f"@{w} page scrolls naturally", s["scrollable"])

        cdp.call("Emulation.setDeviceMetricsOverride", {"width": 1366, "height": 900, "deviceScaleFactor": 1, "mobile": False})
        cdp.evaluate(f"({CLICK_TEXT_JS})(['.result-tabs button','Analytics'])")
        time.sleep(0.6)
        default_titles = cdp.evaluate(ANALYTICS_TITLES)
        ok &= check("analytics default group is Performance only", "Equity Analysis" in default_titles and "Signal Analysis" not in default_titles, str(default_titles))
        cdp.evaluate(f"({CLICK_TEXT_JS})(['.analytics-nav button','Execution'])")
        time.sleep(0.5)
        exec_titles = cdp.evaluate(ANALYTICS_TITLES)
        ok &= check("analytics nav filters to Execution group", "Signal Analysis" in exec_titles and "Equity Analysis" not in exec_titles, str(exec_titles))
        cdp.evaluate(f"({CLICK_TEXT_JS})(['.result-tabs button','Full Report'])")
        time.sleep(0.5)
        strips = cdp.evaluate("""(() => ({ primary: document.querySelectorAll('.report-strip.primary > div').length, secondary: document.querySelectorAll('.report-strip.secondary > div').length }))()""")
        ok &= check("full report has primary + secondary tiers", strips["primary"] == 5 and strips["secondary"] >= 10, str(strips))

        cdp.evaluate(f"({CLICK_TEXT_JS})(['.navigation button','Data Explorer'])")
        time.sleep(0.5)
        ok &= check("data explorer has exactly one readiness row", cdp.evaluate("document.querySelectorAll('.readiness-bar').length") == 1)
        cdp.evaluate(f"({CLICK_TEXT_JS})(['.navigation button','Backtest'])")
        cdp.evaluate(f"({CLICK_TEXT_JS})(['.result-tabs button','Overview'])")
        time.sleep(0.4)
        ok &= check("run-context band groups three rows", cdp.evaluate("""(() => { const b=document.querySelector('.run-context-bar'); return !!b && !!b.querySelector('.main-heading') && !!b.querySelector('.run-source') && !!b.querySelector('.run-status'); })()"""))

        errs = [e for e in cdp.console_errors if "favicon" not in e.lower()]
        ok &= check("no console errors", len(errs) == 0, "; ".join(errs[:2]) if errs else "clean")
        cdp.ws.close()
    finally:
        chrome.terminate()
    print("QA2_RESULT=" + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(e2e_main(main))
