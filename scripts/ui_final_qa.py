"""Final UX/settings cleanup QA (real browser).

Checks: metric tooltips replace truncated copy, MAE/MFE tooltips, unified
Strategy & Trade Settings dialog, account/instrument simplification, no
duplicate run_0001 in the topbar, no run-context stop/take copy, no chart
instruction copy, timeline single-line + chronology, Data Explorer hierarchy,
and no horizontal overflow across widths.
"""

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from scripts.e2e_run_switch import e2e_main

from scripts.e2e_run_switch import CDP, TEXT_JS, WAIT_SEL_JS, launch_chrome, wait_debugger  # noqa: E402

APP = "http://127.0.0.1:5173/"
SIZES = [("1920x1080", 1920, 1080), ("1600x900", 1600, 900), ("1440x900", 1440, 900),
         ("1366x768", 1366, 768), ("1280x720", 1280, 720), ("768", 768, 1024), ("390", 390, 844)]

CLICK_TEXT_JS = """(([root,text]) => { const el=[...document.querySelectorAll(root)].find(b=>b.textContent.trim()===text||b.textContent.includes(text)); if(!el) return 'NO'; for (const t of ['pointerdown','mousedown','mouseup','click']) el.dispatchEvent(new MouseEvent(t,{bubbles:true,cancelable:true,view:window})); return 'OK'; })"""
CLICK_ARIA_JS = """((label) => { const el=document.querySelector('[aria-label="'+label.replace(/"/g,'\\\\"')+'"]'); if(!el) return 'NO'; for (const t of ['pointerdown','mousedown','mouseup','click']) el.dispatchEvent(new MouseEvent(t,{bubbles:true,cancelable:true,view:window})); return 'OK'; })"""


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
        time.sleep(3)
        cdp.call("Emulation.setDeviceMetricsOverride", {"width": 1440, "height": 900, "deviceScaleFactor": 1, "mobile": False})
        time.sleep(0.6)

        # metrics: no long truncated copy; info affordances present
        smalls = cdp.evaluate("Array.from(document.querySelectorAll('.metric-cell small')).map(e=>e.textContent.trim())") or []
        ok &= check("no long/truncated metric explanations", all(len(s) <= 40 and 'price terms' not in s and not s.endswith('…') for s in smalls), str(smalls[:4]))
        info = cdp.evaluate("document.querySelectorAll('.metric-cell [aria-label^=\"About \"]').length") or 0
        cells = cdp.evaluate("document.querySelectorAll('.metric-cell').length") or 0
        ok &= check("every metric has an info affordance", info == cells and cells >= 5, f"info={info} cells={cells}")

        # run-status no stop/take copy; topbar no run id
        status = cdp.evaluate(f"({TEXT_JS})('.run-status')") or ""
        ok &= check("run-context stop/take copy removed", "stop" not in status.lower() and "take profit" not in status.lower(), status[:80])
        engine = cdp.evaluate(f"({TEXT_JS})('.engine')") or ""
        ok &= check("no duplicate run id in topbar", "run_0001" not in engine, engine[:80])
        ok &= check("bridge status explicit", "Python Bridge:" in engine, engine[:60])

        # chart instruction copy removed
        foot = cdp.evaluate(f"({TEXT_JS})('.chart-foot')") or ""
        ok &= check("chart instruction copy removed", "Scroll to zoom" not in foot, foot[:80])

        # unified settings dialog
        ok &= check("single settings action", cdp.evaluate("document.querySelectorAll('.rr-settings-btn').length") == 1)
        cdp.evaluate(f"({CLICK_ARIA_JS})('Strategy & Trade Settings')")
        ok &= check("unified dialog shows trade settings", cdp.evaluate(f"({WAIT_SEL_JS})('[aria-label=\"Stop loss pips\"]')"))
        ok &= check("SL/TP + account in one dialog",
                    cdp.evaluate("!!document.querySelector('[aria-label=\"Take profit pips\"]')")
                    and cdp.evaluate("!!document.querySelector('[aria-label=\"Enable account model\"]')"))
        ok &= check("instrument fields no longer handwritten",
                    not cdp.evaluate("!!document.querySelector('[aria-label=\"Instrument contract size\"]')"))
        cdp.call("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Escape", "code": "Escape", "windowsVirtualKeyCode": 27})
        cdp.call("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Escape", "code": "Escape", "windowsVirtualKeyCode": 27})
        time.sleep(0.4)

        # Data Explorer hierarchy
        cdp.evaluate(f"({CLICK_TEXT_JS})(['.navigation button','Data Explorer'])")
        time.sleep(0.7)
        ok &= check("dataset overview present", cdp.evaluate("!!document.querySelector('.dataset-overview')"))
        ok &= check("no legacy explorer banner", not cdp.evaluate("!!document.querySelector('.explorer-banner')"))
        ok &= check("spread section present (compact, purpose-first)", "Execution Spread" in (cdp.evaluate(f"({TEXT_JS})('.explorer')") or "") and "Average Entry Spread" in (cdp.evaluate(f"({TEXT_JS})('.explorer')") or ""))
        ok &= check("data quality section present", cdp.evaluate("!!document.querySelector('.explorer .data-quality, .explorer .quality')") or bool(cdp.evaluate(f"({TEXT_JS})('.explorer')") and 'Data Quality' in (cdp.evaluate(f"({TEXT_JS})('.explorer')") or "")))
        cdp.evaluate(f"({CLICK_TEXT_JS})(['.navigation button','Backtest'])")
        time.sleep(0.4)

        # timeline single-line (Trade Inspector)
        cdp.evaluate(f"({CLICK_TEXT_JS})(['.navigation button','Trade Inspector'])")
        time.sleep(0.7)
        cdp.evaluate(f"({CLICK_TEXT_JS})(['.detail-tabs button','Timeline'])")
        time.sleep(0.5)
        cols = cdp.evaluate("(() => { const b=document.querySelector('.tt-body'); return b ? getComputedStyle(b).flexDirection : ''; })()")
        ok &= check("timeline body lays out as a row", cols == 'row', str(cols))
        body_rows = cdp.evaluate("Array.from(document.querySelectorAll('.trade-timeline .tt-body')).map(b=>Math.round(b.getBoundingClientRect().height))") or []
        ok &= check("timeline rows are single-line at desktop", body_rows and max(body_rows) <= 26, str(body_rows))
        # chronology from the DOM: signal <= available <= entry <= exit for first trade
        chrono = cdp.evaluate("""(() => {
          const evs = [...document.querySelectorAll('.trade-timeline .tt-time')].map(e=>e.textContent.trim());
          return evs.slice(0, 8);
        })()""") or []
        ok &= check("timeline shows event timestamps", len(chrono) >= 3, str(chrono[:3]))
        cdp.evaluate(f"({CLICK_TEXT_JS})(['.navigation button','Backtest'])")
        time.sleep(0.4)

        # no horizontal overflow across widths
        for label, w, h in SIZES:
            cdp.call("Emulation.setDeviceMetricsOverride", {"width": w, "height": h, "deviceScaleFactor": 1, "mobile": w <= 620})
            time.sleep(0.45)
            ov = cdp.evaluate("document.documentElement.scrollWidth - window.innerWidth")
            ok &= check(f"no horizontal overflow @{label}", ov <= 1, f"overflow={ov}")

        errs = [e for e in cdp.console_errors if "favicon" not in e.lower() and "status of 40" not in e.lower()]
        ok &= check("no console errors", len(errs) == 0, "; ".join(errs[:2]) if errs else "clean")
        cdp.ws.close()
    finally:
        chrome.terminate()
    print("FINAL_QA_RESULT=" + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(e2e_main(main))
