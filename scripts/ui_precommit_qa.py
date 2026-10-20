"""Pre-commit cleanup QA: instrument details hidden, topbar archive text gone,
bottom debugger strip gone, Balance disabled when unavailable with stable
layout, and no overflow across widths.
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

        # 2) topbar archive text removed
        engine = cdp.evaluate(f"({TEXT_JS})('.engine')") or ""
        ok &= check("topbar has no archive text", "archived" not in engine.lower(), engine[:70])
        ok &= check("topbar shows Python Bridge", "Python Bridge:" in engine, engine[:50])

        # 3) bottom debugger strip removed
        ok &= check("bottom trade-debugger strip removed", not cdp.evaluate("!!document.querySelector('.selected-trade-strip')"))

        # 1) instrument details hidden; core account controls present
        cdp.evaluate(f"({CLICK_ARIA_JS})('Strategy & Trade Settings')")
        ok &= check("settings dialog opens", cdp.evaluate(f"({WAIT_SEL_JS})('[aria-label=\"Stop loss pips\"]')"))
        ok &= check("no Instrument details section", not cdp.evaluate("!!document.querySelector('.rr-instrument')"))
        dlg = cdp.evaluate("[...document.querySelectorAll('.settings-dialog')].map(d=>d.textContent).join(' ')") or ""
        ok &= check("no contract/tick/quotes exposed", "Contract size" not in dlg and "Tick size" not in dlg, dlg[:80])
        ok &= check("account model gate present", "Enable Account Model" in dlg)
        cdp.evaluate(f"({CLICK_ARIA_JS})('Enable account model')")
        ok &= check("account controls appear when enabled", cdp.evaluate(f"({WAIT_SEL_JS})('[aria-label=\"Account currency\"]')"))
        dlg2 = cdp.evaluate("[...document.querySelectorAll('.settings-dialog')].map(d=>d.textContent).join(' ')") or ""
        ok &= check("account controls present", "Starting Balance" in dlg2 and "Commission" in dlg2 and "Position Size" in dlg2)
        ok &= check("instrument details still hidden when enabled", "Contract size" not in dlg2 and "Tick size" not in dlg2, dlg2[:60])
        cdp.call("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Escape", "code": "Escape", "windowsVirtualKeyCode": 27})
        cdp.call("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Escape", "code": "Escape", "windowsVirtualKeyCode": 27})
        time.sleep(0.4)

        # 4) Balance disabled when unavailable + stable layout
        before = cdp.evaluate("(() => { const g=document.querySelector('.charts-grid'); const e=document.querySelector('.equity'); return { grid: Math.round(g.getBoundingClientRect().height), eq: Math.round(e.getBoundingClientRect().height), mode: document.querySelector('[aria-label=\"Performance series\"]').textContent.trim() }; })()")
        cdp.evaluate(f"({CLICK_ARIA_JS})('Performance series')")
        time.sleep(0.4)
        bal_disabled = cdp.evaluate("(() => { const o=[...document.querySelectorAll('[role=option]')].find(x=>x.textContent.includes('Balance')); return o ? o.getAttribute('aria-disabled') : 'NO_OPTION'; })()")
        ok &= check("Balance option disabled for price-term run", bal_disabled == "true", str(bal_disabled))
        # try to click it (should not switch)
        cdp.evaluate("(() => { const o=[...document.querySelectorAll('[role=option]')].find(x=>x.textContent.includes('Balance')); if(o) for (const t of ['pointerdown','mousedown','mouseup','click']) o.dispatchEvent(new MouseEvent(t,{bubbles:true,cancelable:true,view:window})); })()")
        time.sleep(0.4)
        after = cdp.evaluate("(() => { const g=document.querySelector('.charts-grid'); const e=document.querySelector('.equity'); return { grid: Math.round(g.getBoundingClientRect().height), eq: Math.round(e.getBoundingClientRect().height), mode: document.querySelector('[aria-label=\"Performance series\"]').textContent.trim() }; })()")
        ok &= check("layout stable after Balance attempt", before["grid"] == after["grid"] and before["eq"] == after["eq"], f'{before} -> {after}')
        ok &= check("stays on Cumulative PnL", "Cumulative" in after["mode"], after["mode"])
        ok &= check("no oversized balance empty state", not cdp.evaluate("!!document.querySelector('.equity-empty')"))

        # 5) no horizontal overflow across widths
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
    print("PRECOMMIT_QA_RESULT=" + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(e2e_main(main))
