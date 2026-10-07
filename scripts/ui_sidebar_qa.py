"""Sidebar no-scroll QA: verifies the Backtest sidebar has no internal vertical
scrollbar at desktop/laptop sizes and that Run Backtest is reachable without
scrolling the sidebar. Also checks no horizontal page overflow.

Run with the dev server on 5173 and a bridge on 8765 (for catalogs).
"""

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.e2e_run_switch import CDP, launch_chrome, wait_debugger  # noqa: E402

APP = "http://127.0.0.1:5173/"
SIZES = [
    ("1920x1080", 1920, 1080),
    ("1600x900", 1600, 900),
    ("1440x900", 1440, 900),
    ("1366x768", 1366, 768),
    ("1280x720", 1280, 720),
    ("768", 768, 1024),
    ("390", 390, 844),
]

MEASURE = """(() => {
  const el = document.querySelector('.config');
  const run = document.querySelector('.real-run .primary.run') || document.querySelector('.real-run button.run');
  const cs = el ? getComputedStyle(el) : null;
  const rb = run ? run.getBoundingClientRect() : null;
  const cb = el ? el.getBoundingClientRect() : null;
  const head = document.querySelector('.metric-tier.primary .metric-tier-head');
  return {
    internalScroll: el ? (el.scrollHeight - el.clientHeight) : null,
    overflowY: cs ? cs.overflowY : null,
    configH: el ? Math.round(el.getBoundingClientRect().height) : 0,
    scrollH: el ? el.scrollHeight : 0,
    clientH: el ? el.clientHeight : 0,
    runFound: !!run,
    runBottom: rb ? Math.round(rb.bottom) : null,
    configBottom: cb ? Math.round(cb.bottom) : null,
    runGapBelow: (rb && cb) ? Math.round(cb.bottom - rb.bottom) : null,
    vh: window.innerHeight,
    runVisible: rb ? (rb.top >= -1 && rb.bottom <= window.innerHeight + 1) : false,
    docOverflow: document.documentElement.scrollWidth - window.innerWidth,
    hasTitle: !!document.querySelector('.sidebar-title'),
    hasHeading: !!document.querySelector('.real-run .config-heading'),
    bodyBg: getComputedStyle(document.body).backgroundColor,
    perfHeadBg: head ? getComputedStyle(head).backgroundColor : null,
  };
})()"""


def _rgb(s):
    import re
    m = re.findall(r"\d+", s or "")
    return tuple(int(x) for x in m[:3]) if len(m) >= 3 else None


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f" — {detail}" if detail else ""))
    return bool(cond)


def main() -> int:
    ok = True
    chrome = launch_chrome(9376)
    try:
        t = wait_debugger(9376)
        page = next(x for x in t if x.get("type") == "page")
        cdp = CDP(page["webSocketDebuggerUrl"])
        for dom in ("Page.enable", "Runtime.enable", "Log.enable", "DOM.enable"):
            cdp.call(dom)
        cdp.call("Page.navigate", {"url": APP})
        time.sleep(3)
        ok &= check("app loads", bool(cdp.evaluate("!!document.querySelector('.config')")))

        for label, w, h in SIZES:
            cdp.call("Emulation.setDeviceMetricsOverride", {
                "width": w, "height": h, "deviceScaleFactor": 1, "mobile": w <= 620,
            })
            time.sleep(0.6)
            m = cdp.evaluate(MEASURE)
            if label == "1920x1080":
                ok &= check("red-marked sidebar title removed", not m["hasTitle"])
                ok &= check("redundant Real Backtest heading removed", not m["hasHeading"])
                b = _rgb(m["bodyBg"])
                ok &= check("global background darker", b is not None and sum(b) <= 46, str(b))
                ph = _rgb(m["perfHeadBg"])
                ok &= check("performance band dark (not bright blue)", ph is not None and sum(ph) <= 70, str(ph))
                ok &= check("no dead space below Run Backtest",
                            m["runGapBelow"] is not None and m["runGapBelow"] <= 46, str(m["runGapBelow"]))
            desktop = w >= 901
            if desktop:
                ok &= check(f"sidebar no internal scroll @{label}",
                            m["internalScroll"] is not None and m["internalScroll"] <= 1,
                            f"scrollH={m['scrollH']} clientH={m['clientH']} overflowY={m['overflowY']}")
                ok &= check(f"Run Backtest visible without sidebar scroll @{label}",
                            m["runFound"] and m["runVisible"],
                            f"runBottom={m['runBottom']} vh={m['vh']}")
            else:
                # Small/tablet screens: the setup area flows with the document; no nested scroll.
                ok &= check(f"no nested sidebar scroll @{label}",
                            m["internalScroll"] is None or m["internalScroll"] <= 1,
                            f"scrollH={m['scrollH']} clientH={m['clientH']} overflowY={m['overflowY']}")
            ok &= check(f"no horizontal overflow @{label}", m["docOverflow"] <= 1, f"overflow={m['docOverflow']}")

        cdp.ws.close()
    finally:
        chrome.terminate()
    print("SIDEBAR_QA_RESULT=" + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
