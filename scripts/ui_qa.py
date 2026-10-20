"""UI/UX refinement QA: real browser inspection across widths (1920..390).

Measures horizontal overflow, duplicate readiness rows, metric tier shape,
run-context grouping and captures screenshots for manual review. Read-only.
"""

import base64
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from scripts.e2e_run_switch import e2e_main

from scripts.e2e_run_switch import CDP, TEXT_JS, WAIT_TEXT_JS, launch_chrome, wait_debugger  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

APP = "http://127.0.0.1:5173/"
OUT = Path("C:/Users/Lenovo/AppData/Local/Temp/qbl-uiqa")
OUT.mkdir(parents=True, exist_ok=True)
WIDTHS = [1920, 1600, 1440, 1366, 1280, 768, 390]

CLICK_TEXT_JS = """(([root,text]) => { const el=[...document.querySelectorAll(root)].find(b=>b.textContent.trim()===text||b.textContent.includes(text)); if(!el) return 'NO'; for (const t of ['pointerdown','mousedown','mouseup','click']) el.dispatchEvent(new MouseEvent(t,{bubbles:true,cancelable:true,view:window})); return 'OK'; })"""
CLICK_EL_JS = """((sel) => { const el=document.querySelector(sel); if(!el) return 'NO'; for (const t of ['pointerdown','mousedown','mouseup','click']) el.dispatchEvent(new MouseEvent(t,{bubbles:true,cancelable:true,view:window})); return 'OK'; })"""


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f" — {detail}" if detail else ""))
    return bool(cond)


def measure(cdp):
    return cdp.evaluate("""(() => {
      const de = document.documentElement;
      const overflow = de.scrollWidth - window.innerWidth;
      const ready = document.querySelectorAll('.readiness-bar').length;
      const primary = document.querySelectorAll('.metric-tier-cells.primary .metric-cell').length;
      const secondary = document.querySelectorAll('.metric-tier-cells.secondary > *').length;
      const band = document.querySelector('.run-context-bar');
      const grouped = !!band && !!band.querySelector('.main-heading') && !!band.querySelector('.run-source') && !!band.querySelector('.run-status');
      const chart = document.querySelector('.charts-grid .panel');
      const chartW = chart ? Math.round(chart.getBoundingClientRect().width) : 0;
      const sidebar = document.querySelector('.config');
      const sideW = sidebar ? Math.round(sidebar.getBoundingClientRect().width) : 0;
      // widest overflowing element (for diagnosis)
      let worst = null, worstW = 0;
      document.querySelectorAll('main * , .topbar *').forEach(el => {
        const r = el.getBoundingClientRect();
        const over = r.right - window.innerWidth;
        if (over > worstW) { worstW = Math.round(over); worst = el.className && typeof el.className === 'string' ? el.className.split(' ').slice(0,2).join('.') : el.tagName; }
      });
      return { overflow, ready, primary, secondary, grouped, chartW, sideW, worst, worstW, inner: window.innerWidth };
    })()""")


def shot(cdp, name):
    data = cdp.call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False})
    (OUT / f"{name}.png").write_bytes(base64.b64decode(data["data"]))


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
        ok &= check("app loads", bool(cdp.evaluate(f"({TEXT_JS})('.engine')")))

        by_width = {}
        for w in WIDTHS:
            cdp.call("Emulation.setDeviceMetricsOverride", {
                "width": w, "height": 900 if w > 900 else (1024 if w > 500 else 844),
                "deviceScaleFactor": 1, "mobile": w <= 500,
            })
            time.sleep(0.7)
            m = measure(cdp)
            by_width[w] = m
            if w in (1920, 1440, 1366, 1280, 768, 390):
                shot(cdp, f"overview-{w}")
            ok &= check(f"no horizontal overflow @{w}", m["overflow"] <= 1, f"overflow={m['overflow']} worst={m['worst']}+{m['worstW']} sidebar={m['sideW']} chart={m['chartW']}")

        m = by_width[1366]
        ok &= check("one run-context band groups identity+source+status", m["grouped"])
        ok &= check("primary metric tier has 5 headline cells", m["primary"] == 5, str(m["primary"]))
        ok &= check("secondary detail tier populated", m["secondary"] >= 8, str(m["secondary"]))
        ok &= check("sidebar stays compact at 1366", m["sideW"] <= 260, str(m["sideW"]))
        ok &= check("price chart keeps dominant width at 1366", m["chartW"] >= 560, str(m["chartW"]))
        ok &= check("exactly one readiness row on Backtest", by_width[1366]["ready"] == 1, str(by_width[1366]["ready"]))

        # other screens at 1440 and 1366
        for w in (1440, 1366):
            cdp.call("Emulation.setDeviceMetricsOverride", {"width": w, "height": 900, "deviceScaleFactor": 1, "mobile": False})
            time.sleep(0.5)
            cdp.evaluate(f"({CLICK_TEXT_JS})(['.navigation button','Backtest'])")
            cdp.evaluate(f"({CLICK_TEXT_JS})(['.result-tabs button','Full Report'])")
            time.sleep(0.6)
            fr = measure(cdp)
            ok &= check(f"full report no overflow @{w}", fr["overflow"] <= 1, f"overflow={fr['overflow']}")
            shot(cdp, f"fullreport-{w}")
            cdp.evaluate(f"({CLICK_TEXT_JS})(['.result-tabs button','Analytics'])")
            time.sleep(0.6)
            an = measure(cdp)
            ok &= check(f"analytics no overflow @{w}", an["overflow"] <= 1, f"overflow={an['overflow']}")
            ok &= check(f"analytics local nav present @{w}", cdp.evaluate("!!document.querySelector('.analytics-nav')"))
            shot(cdp, f"analytics-{w}")

        cdp.call("Emulation.setDeviceMetricsOverride", {"width": 1366, "height": 900, "deviceScaleFactor": 1, "mobile": False})
        cdp.evaluate(f"({CLICK_TEXT_JS})(['.result-tabs button','Overview'])")
        cdp.evaluate(f"({CLICK_TEXT_JS})(['.navigation button','Trade Inspector'])")
        time.sleep(0.6)
        ti = measure(cdp)
        ok &= check("trade inspector no overflow @1366", ti["overflow"] <= 1, f"overflow={ti['overflow']}")
        shot(cdp, "inspector-1366")
        cdp.evaluate(f"({CLICK_TEXT_JS})(['.navigation button','Data Explorer'])")
        time.sleep(0.6)
        de = measure(cdp)
        ok &= check("data explorer no horizontal overflow @1366", de["overflow"] <= 1, f"overflow={de['overflow']}")
        ok &= check("data explorer readiness is not duplicated", de["ready"] == 1, str(de["ready"]))
        shot(cdp, "dataexplorer-1366")
        cdp.evaluate(f"({CLICK_EL_JS})('.library-button')")
        time.sleep(0.8)
        ok &= check("research workspace opens", cdp.evaluate("!!document.querySelector('.workspace-dialog')"))
        shot(cdp, "workspace-1366")

        errs = [e for e in cdp.console_errors if "favicon" not in e.lower() and "status of 40" not in e.lower()]
        ok &= check("no console errors", len(errs) == 0, "; ".join(errs[:2]) if errs else "clean")
        cdp.ws.close()
    finally:
        chrome.terminate()
    print("SCREENSHOTS=" + str(OUT))
    print("QA_RESULT=" + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(e2e_main(main))
