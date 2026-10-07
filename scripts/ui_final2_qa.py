"""Final pre-commit QA #2: hover the ACTUAL MAE/MFE N/A values, verify timeline
one-line-per-event + chronology, and verify the trimmed Data Explorer.

Uses real CDP mouse moves to trigger the shared tooltip.
"""

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.e2e_run_switch import CDP, TEXT_JS, WAIT_SEL_JS, launch_chrome, wait_debugger  # noqa: E402

APP = "http://127.0.0.1:5173/"
SIZES = [("1920", 1920, 1080), ("1600", 1600, 900), ("1440", 1440, 900),
         ("1366", 1366, 768), ("1280", 1280, 720)]

CLICK_TEXT_JS = """(([root,text]) => { const el=[...document.querySelectorAll(root)].find(b=>b.textContent.trim()===text||b.textContent.includes(text)); if(!el) return 'NO'; for (const t of ['pointerdown','mousedown','mouseup','click']) el.dispatchEvent(new MouseEvent(t,{bubbles:true,cancelable:true,view:window})); return 'OK'; })"""


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f" — {detail}" if detail else ""))
    return bool(cond)


def hover(cdp, selector):
    box = cdp.evaluate(
        "(() => { const e=document.querySelector(%s); if(!e) return null; e.scrollIntoView({block:'center'});"
        " const r=e.getBoundingClientRect(); return {x: Math.round(r.left+r.width/2), y: Math.round(r.top+r.height/2)}; })()"
        % json.dumps(selector)
    )
    if not box:
        return False
    cdp.call("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": box["x"], "y": box["y"], "button": "none"})
    time.sleep(0.7)
    return True


def hover_index(cdp, selector, idx):
    box = cdp.evaluate(
        "(([sel, i]) => { const e=document.querySelectorAll(sel)[i]; if(!e) return null; e.scrollIntoView({block:'center'});"
        " const r=e.getBoundingClientRect(); return {x: Math.round(r.left+r.width/2), y: Math.round(r.top+r.height/2)}; })("
        + json.dumps([selector, idx]) + ")"
    )
    if not box:
        return False
    cdp.call("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": box["x"], "y": box["y"], "button": "none"})
    time.sleep(0.8)
    return True


def tooltip_text(cdp):
    return cdp.evaluate("(() => { const t=document.querySelector('[data-slot=\"tooltip-content\"][data-state=\"open\"],[role=\"tooltip\"][data-state=\"open\"],[data-slot=\"tooltip-content\"],[role=\"tooltip\"]'); return t ? t.textContent.trim() : ''; })()") or ""


def main() -> int:
    ok = True
    chrome = launch_chrome(9388)
    try:
        t = wait_debugger(9388)
        page = next(x for x in t if x.get("type") == "page")
        cdp = CDP(page["webSocketDebuggerUrl"])
        for dom in ("Page.enable", "Runtime.enable", "Log.enable", "DOM.enable"):
            cdp.call(dom)
        cdp.call("Page.navigate", {"url": APP})
        time.sleep(3)
        cdp.call("Emulation.setDeviceMetricsOverride", {"width": 1440, "height": 900, "deviceScaleFactor": 1, "mobile": False})
        time.sleep(0.6)

        # 1) hover the ACTUAL MAE N/A / MFE N/A values in the Trade Log
        n = cdp.evaluate("document.querySelectorAll('.overview-trade-log .excursion-value').length") or 0
        ok &= check("trade-log MAE/MFE values present", n >= 2, str(n))
        hover_index(cdp, ".overview-trade-log .excursion-value", 0)
        tt = tooltip_text(cdp)
        ok &= check("MAE N/A hover shows explanation", "Maximum Adverse Excursion" in tt, tt[:70])
        cdp.call("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": 5, "y": 5, "button": "none"})
        time.sleep(0.5)
        hover_index(cdp, ".overview-trade-log .excursion-value", 1)
        tt2 = tooltip_text(cdp)
        ok &= check("MFE N/A hover shows explanation", "Maximum Favorable Excursion" in tt2, tt2[:70])

        # also the Trade Explorer rows in the Inspector
        cdp.evaluate(f"({CLICK_TEXT_JS})(['.navigation button','Trade Inspector'])")
        time.sleep(0.7)
        ok &= check("inspector explorer MAE/MFE values present", cdp.evaluate("document.querySelectorAll('.trade-explorer .excursion-value').length") >= 2)
        hovx = hover(cdp, ".trade-explorer .excursion-value")
        ok &= check("inspector MAE hover shows explanation", hovx and "Maximum Adverse Excursion" in tooltip_text(cdp), tooltip_text(cdp)[:60])

        # 2) timeline one-line per event + chronology
        cdp.evaluate(f"({CLICK_TEXT_JS})(['.detail-tabs button','Timeline'])")
        time.sleep(0.5)
        body_h = cdp.evaluate("Array.from(document.querySelectorAll('.trade-timeline .tt-body')).map(b=>Math.round(b.getBoundingClientRect().height))") or []
        ok &= check("each timeline event is one line", body_h and max(body_h) <= 26, str(body_h))
        nowrap = cdp.evaluate("(() => { const n=document.querySelector('.trade-timeline .tt-note'); return n ? getComputedStyle(n).whiteSpace : ''; })()")
        ok &= check("timeline note is nowrap on desktop", nowrap == 'nowrap', str(nowrap))
        times = cdp.evaluate("Array.from(document.querySelectorAll('.trade-timeline .tt-time')).map(e=>e.textContent.replace(' UTC','').trim())") or []
        parsed = []
        for s in times:
            try:
                import datetime as _dt
                parsed.append(_dt.datetime.strptime(s, "%Y-%m-%d %H:%M:%S").timestamp())
            except Exception:
                parsed.append(None)
        nums = [p for p in parsed if p is not None]
        ok &= check("timeline chronology non-decreasing", len(nums) >= 4 and nums == sorted(nums), str(times[:4]))

        cdp.evaluate(f"({CLICK_TEXT_JS})(['.navigation button','Backtest'])")
        time.sleep(0.3)

        # 3) Data Explorer trimmed
        cdp.evaluate(f"({CLICK_TEXT_JS})(['.navigation button','Data Explorer'])")
        time.sleep(0.7)
        de = cdp.evaluate(f"({TEXT_JS})('.explorer')") or ""
        for bad in ["Modeling Quality", "REQUIRED FOR CALCULATION", "Required for calculation", "No percentage is synthesized",
                    "Counts reported by the archived engine result", "Measured run", "One-minute data",
                    "Gap validation from the data-validation engine", "Not supplied by the run"]:
            ok &= check(f"Data Explorer removed: {bad!r}", bad not in de)
        ok &= check("Average Entry Spread present", "Average Entry Spread" in de)
        ok &= check("Average Exit Spread present", "Average Exit Spread" in de)
        ok &= check("no fake min/max spread cards", "Minimum Spread" not in de and "Maximum Spread" not in de)
        ok &= check("Data Quality present", "Data Quality" in de)
        ok &= check("no .rr-instrument", not cdp.evaluate("!!document.querySelector('.rr-instrument')"))
        ok &= check("no giant explorer-banner", not cdp.evaluate("!!document.querySelector('.explorer-banner')"))

        # 4) no horizontal overflow + timeline per width
        for label, w, h in SIZES:
            cdp.call("Emulation.setDeviceMetricsOverride", {"width": w, "height": h, "deviceScaleFactor": 1, "mobile": False})
            time.sleep(0.45)
            ov = cdp.evaluate("document.documentElement.scrollWidth - window.innerWidth")
            ok &= check(f"no horizontal overflow @{label}", ov <= 1, f"overflow={ov}")

        errs = [e for e in cdp.console_errors if "favicon" not in e.lower() and "status of 40" not in e.lower()]
        ok &= check("no console errors", len(errs) == 0, "; ".join(errs[:2]) if errs else "clean")
        cdp.ws.close()
    finally:
        chrome.terminate()
    print("FINAL2_QA_RESULT=" + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
