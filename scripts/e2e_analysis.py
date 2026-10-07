"""Research Analysis v2 browser E2E: run comparison + optimization.

Isolated deterministic artifacts (temp runs + studies + strategy dir; repo public
snapshotted/restored). Proves comparison, deterministic grid study through the
normal single-run pipeline, ranking, publish-on-open, compare candidates, study
history restore, and preserved existing flows.
"""

import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.e2e_run_switch import (  # noqa: E402
    CDP, TEXT_JS, WAIT_BADGE_JS, WAIT_SEL_JS, WAIT_TEXT_JS, launch_chrome, wait_debugger,
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

APP_URL = "http://127.0.0.1:5173/?fixture=demo"
BRIDGE = "http://127.0.0.1:8765"
PUBLIC = ROOT / "Quant-Backtest-Lab" / "public"
MANIFEST = PUBLIC / "adapters-run-manifest.json"
FIXTURE = ROOT / "tests" / "fixtures" / "run_0001"
EMA_PATH = ROOT / "tests" / "fixtures" / "import" / "ema_cross.py"
DEMO_ENTRY = "2,799.84"

_POLL = "const t0=Date.now();const iv=setInterval(()=>{if(ok()||Date.now()-t0>MAX){clearInterval(iv);res(ok());}},250);"

SET_INPUT_JS = """(([sel, value]) => {
  const el = document.querySelector(sel); if (!el) return 'NO_EL';
  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
  setter.call(el, value); el.dispatchEvent(new Event('input', { bubbles: true })); el.dispatchEvent(new Event('change', { bubbles: true }));
  return el.value;
})"""
CLICK_EL_JS = """((sel) => { const el=document.querySelector(sel); if(!el) return 'NO'; for (const t of ['pointerdown','mousedown','mouseup','click']) el.dispatchEvent(new MouseEvent(t,{bubbles:true,cancelable:true,view:window})); return 'OK'; })"""
CLICK_ARIA_JS = """((label) => { const el=document.querySelector('[aria-label="'+label.replace(/"/g,'\\\\"')+'"]'); if(!el) return 'NO'; for (const t of ['pointerdown','mousedown','mouseup','click']) el.dispatchEvent(new MouseEvent(t,{bubbles:true,cancelable:true,view:window})); return 'OK'; })"""
CLICK_TEXT_JS = """(([root,text]) => { const el=[...document.querySelectorAll(root)].find(b=>b.textContent.trim()===text||b.textContent.includes(text)); if(!el) return 'NO'; for (const t of ['pointerdown','mousedown','mouseup','click']) el.dispatchEvent(new MouseEvent(t,{bubbles:true,cancelable:true,view:window})); return 'OK'; })"""
CLICK_RUN_JS = """(() => { const b=[...document.querySelectorAll('.real-run button')].find(x=>x.textContent.includes('Run Backtest')||x.textContent.includes('Running')); if(!b) return 'NO'; b.click(); return 'CLICKED'; })()"""

WAIT_ENABLED_JS = ("(() => new Promise(res => {"
                   " const ok=()=>{const b=[...document.querySelectorAll('.real-run button')].find(x=>x.textContent.includes('Run Backtest'));return !!b && !b.disabled;};"
                   " if(ok())return res(true);" + _POLL.replace("MAX", "30000") + "}))()")
WAIT_ROW_JS = ("(text => new Promise(res => {"
               " const ok=()=>{const r=document.querySelector('.overview-trade-log tbody tr');return !!r && r.textContent.includes(text);};"
               " if(ok())return res(true);" + _POLL.replace("MAX", "25000") + "}))")
WAIT_NEW_RUN_JS = ("(prev => new Promise(res => {"
                   " const ok=()=>{const t=(document.querySelector('.run-status')||{}).textContent||'';"
                   "  const m=t.match(/Archived Run · (\\S+) ·/); return !!m && m[1]!==prev;};"
                   " if(ok())return res(true);" + _POLL.replace("MAX", "150000") + "}))")
WAIT_CMP_ROWS_JS = ("(n => new Promise(res => {"
                    " const ok=()=>document.querySelectorAll('.cmp-table tbody tr').length>=n;"
                    " if(ok())return res(true);" + _POLL.replace("MAX", "60000") + "}))")
WAIT_STUDY_JS = ("(() => new Promise(res => {"
                 " const ok=()=>!!document.querySelector('.opt-study-head') && document.querySelectorAll('.cmp-table tbody tr').length>=4;"
                 " if(ok())return res(true);" + _POLL.replace("MAX", "240000") + "}))()")
WAIT_GONE_JS = ("(sel => new Promise(res => { const ok=()=>!document.querySelector(sel); if(ok())return res(true);" + _POLL.replace("MAX", "10000") + "}))")
WAIT_HISTORY_JS = ("(sid => new Promise(res => {"
                   " const ok=()=>[...document.querySelectorAll('.lib-row')].some(r=>r.textContent.includes(sid));"
                   " if(ok())return res(true);" + _POLL.replace("MAX", "20000") + "}))")
WAIT_LIB_JS = ("(rid => new Promise(res => {"
               " const ok=()=>[...document.querySelectorAll('.lib-row')].some(r=>r.textContent.includes(rid));"
               " if(ok())return res(true);" + _POLL.replace("MAX", "20000") + "}))")


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


def http_get(url):
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            return r.status, r.read(), dict(r.headers)
    except Exception as exc:  # noqa: BLE001
        return 0, b"", {"error": str(exc)}


def fmt(n):
    return f"{n:,.2f}"


def set_input(cdp, sel, value):
    cdp.evaluate(f"({SET_INPUT_JS})([{json.dumps(sel)},{json.dumps(value)}])")


def escape(cdp):
    cdp.call("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Escape", "code": "Escape", "windowsVirtualKeyCode": 27})
    cdp.call("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Escape", "code": "Escape", "windowsVirtualKeyCode": 27})


def open_library(cdp):
    cdp.evaluate(f"({CLICK_EL_JS})('.library-button')")
    cdp.evaluate(f"({WAIT_SEL_JS})('.workspace-dialog')")


def close_library(cdp):
    escape(cdp)
    cdp.evaluate(f"({WAIT_GONE_JS})('.workspace-dialog')")


def open_tab(cdp, name):
    cdp.evaluate(f"({CLICK_TEXT_JS})(['.workspace-tabs button','{name}'])")


def select_radix(cdp, trigger_aria, option_text):
    cdp.evaluate(f"({CLICK_ARIA_JS})({json.dumps(trigger_aria)})")
    for _ in range(60):
        if cdp.evaluate(f"(text => [...document.querySelectorAll('[role=option]')].some(o=>o.textContent.includes(text)))({json.dumps(option_text)})"):
            break
        time.sleep(0.25)
    cdp.evaluate(
        "(text => { const o=[...document.querySelectorAll('[role=option]')].find(x=>x.textContent.includes(text));"
        " if(!o) return 'NO'; for (const t of ['pointerdown','mousedown','mouseup','click']) o.dispatchEvent(new MouseEvent(t,{bubbles:true,cancelable:true,view:window})); return 'OK'; })"
        f"({json.dumps(option_text)})")


def main() -> int:
    ok = True
    manifest_before = MANIFEST.read_text(encoding="utf-8") if MANIFEST.is_file() else None
    files_before = set(p.name for p in PUBLIC.glob("runs-*-result.json"))

    tmp = Path(tempfile.mkdtemp(prefix="qbl-e2e-analysis-"))
    runs_dir = tmp / "runs"
    runs_dir.mkdir()
    studies_dir = tmp / "studies"
    strategy_dir = tmp / "strategies"
    strategy_dir.mkdir()
    shutil.copy(ROOT / "tests" / "fixtures" / "strategies" / "threshold_direction.py", strategy_dir / "threshold_direction.py")

    shutil.copytree(FIXTURE, runs_dir / "run_0001")
    shutil.copytree(FIXTURE, runs_dir / "run_0002")
    r2 = json.loads((runs_dir / "run_0002" / "result.json").read_text(encoding="utf-8"))
    r2["run_id"] = "run_0002"
    r2["created_at"] = "2025-03-01T00:00:00Z"
    r2["config"]["symbol"] = "EURUSD"
    (runs_dir / "run_0002" / "result.json").write_text(json.dumps(r2, indent=2), encoding="utf-8")
    r1_net = json.loads((runs_dir / "run_0001" / "result.json").read_text(encoding="utf-8"))["metrics"]["net_pnl"]
    r2_net = r2["metrics"]["net_pnl"]

    bridge = subprocess.Popen(
        [sys.executable, "-m", "src.bridge", "--port", "8765",
         "--runs", str(runs_dir), "--public", str(PUBLIC),
         "--strategy-dir", str(strategy_dir), "--data-dir", "data", "--studies", str(studies_dir)],
        cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    chrome = None
    try:
        ok &= check("bridge starts locally", health(), BRIDGE)
        chrome = launch_chrome(9354)
        t = wait_debugger(9354)
        page = next(x for x in t if x.get("type") == "page")
        cdp = CDP(page["webSocketDebuggerUrl"])
        for dom in ("Page.enable", "Runtime.enable", "Log.enable", "Network.enable", "DOM.enable"):
            cdp.call(dom)
        cdp.call("Page.navigate", {"url": APP_URL})
        ok &= check("app opens on Demo", cdp.evaluate(f"({WAIT_BADGE_JS})('DEMO')"))
        ok &= check("bridge Connected", cdp.evaluate(f"({WAIT_TEXT_JS})(['.engine','Connected'])"))
        cdp.evaluate("window.open=(u)=>{window.__opened=u;return null;};")

        # ---- compare two existing runs ---------------------------------------
        open_library(cdp)
        ok &= check("Run Library lists run_0001", cdp.evaluate(f"({WAIT_SEL_JS})('.lib-row')") and
                    bool(cdp.evaluate("(id=>[...document.querySelectorAll('.lib-row')].some(r=>r.textContent.includes(id)))('run_0001')")))
        cdp.evaluate(f"({CLICK_ARIA_JS})('Select run_0001')")
        cdp.evaluate(f"({CLICK_ARIA_JS})('Select run_0002')")
        open_tab(cdp, "Compare")
        cdp.evaluate(f"({CLICK_ARIA_JS})('Compare selected runs')")
        ok &= check("comparison renders two runs", cdp.evaluate(f"({WAIT_CMP_ROWS_JS})(2)"))
        row1 = cdp.evaluate("(() => { const r=[...document.querySelectorAll('.cmp-table tbody tr')].find(x=>x.textContent.includes('run_0001')); return r?r.textContent.replace(/\\s+/g,' '):''; })()")
        row2 = cdp.evaluate("(() => { const r=[...document.querySelectorAll('.cmp-table tbody tr')].find(x=>x.textContent.includes('run_0002')); return r?r.textContent.replace(/\\s+/g,' '):''; })()")
        ok &= check("comparison metrics match run_0001", fmt(r1_net) in row1, row1[:120])
        ok &= check("comparison metrics match run_0002", fmt(r2_net) in row2, row2[:120])
        ok &= check("incompatible-symbol warning shown", "Symbols differ" in (cdp.evaluate(f"({TEXT_JS})('.cmp-warnings')") or ""))
        ok &= check("cumulative PnL comparison renders", cdp.evaluate("!!document.querySelector('.cmp-chart svg')"))

        # ---- optimization: deterministic grid study --------------------------
        open_tab(cdp, "Optimize")
        ok &= check("optimization form loads", cdp.evaluate(f"({WAIT_SEL_JS})('[aria-label=\"Optimization strategy\"]')"))
        select_radix(cdp, "Optimization strategy", "Threshold Direction")
        ok &= check("parameterized strategy selected", cdp.evaluate(f"({WAIT_SEL_JS})('[aria-label=\"Optimization values threshold\"]')"))
        set_input(cdp, '[aria-label="Optimization values threshold"]', "0")
        set_input(cdp, '[aria-label="Optimization values side"]', "both, long")
        set_input(cdp, '[aria-label="Optimization values enabled"]', "true")
        set_input(cdp, '[aria-label="Optimization stop values"]', "3, 5")
        set_input(cdp, '[aria-label="Optimization take values"]', "10")
        set_input(cdp, '[aria-label="Optimization start date"]', "2025-01-02")
        set_input(cdp, '[aria-label="Optimization end date"]', "2025-01-02")
        count_text = cdp.evaluate(f"({TEXT_JS})('.opt-count')") or ""
        ok &= check("candidate count displayed", "4" in count_text, count_text[:60])

        manifest_ids_before = {e["runId"] for e in json.loads(MANIFEST.read_text(encoding="utf-8"))["runs"]}
        cdp.evaluate(f"({CLICK_ARIA_JS})('Run optimization')")
        ok &= check("study completes with ranked table", cdp.evaluate(WAIT_STUDY_JS, timeout=300),
                    cdp.evaluate(f"({TEXT_JS})('.opt-study-head')"))
        study_id = (re.search(r"(study_[0-9a-f]{8})", cdp.evaluate(f"({TEXT_JS})('.opt-study-head')") or "") or [None, None])[1]
        study = json.loads((studies_dir / study_id / "study.json").read_text(encoding="utf-8")) if study_id else {}
        ok &= check("study archive exists", bool(study_id) and (studies_dir / study_id / "study.json").is_file(), str(study_id))
        ok &= check("four candidate runs archived", len(study.get("candidates", [])) == 4 and all(
            (runs_dir / c["run_id"] / "result.json").is_file() for c in study.get("candidates", []) if c["status"] == "ok"))
        ok_candidates = [c for c in study.get("candidates", []) if c["status"] == "ok"]
        best_expected = max(ok_candidates, key=lambda c: c["objective_value"])["run_id"]
        ok &= check("best run matches configured objective", study.get("best_run_id") == best_expected, f"{study.get('best_run_id')} vs {best_expected}")
        ok &= check("candidates not published by default",
                    not (set(c["run_id"] for c in study.get("candidates", [])) & manifest_ids_before))

        # open best candidate -> publish + dashboard
        cdp.evaluate(f"({CLICK_ARIA_JS})({json.dumps(f'Open candidate {best_expected}')})")
        ok &= check("best candidate opens in dashboard", cdp.evaluate(f"({WAIT_BADGE_JS})('ARCHIVED RUN')"))
        best_payload = json.loads((PUBLIC / f"runs-{best_expected}-result.json").read_text(encoding="utf-8"))
        best_first = fmt(best_payload["trades"][0]["entry_price"]) if best_payload.get("trades") else ""
        ok &= check("dashboard matches candidate result.json", cdp.evaluate(f"({WAIT_ROW_JS})({json.dumps(best_first)})"), best_first)
        manifest_ids_after = {e["runId"] for e in json.loads(MANIFEST.read_text(encoding="utf-8"))["runs"]}
        ok &= check("candidate published only when opened", best_expected in manifest_ids_after and
                    len([r for r in study["candidates"] if r["run_id"] in manifest_ids_after]) == 1)

        # open candidate report
        cdp.evaluate("window.__opened=null;")
        open_library(cdp)
        open_tab(cdp, "Optimize")
        cdp.evaluate(f"({CLICK_ARIA_JS})({json.dumps(f'Open candidate report {best_expected}')})")
        opened = cdp.evaluate("window.__opened")
        ok &= check("candidate report opens", bool(opened) and f"/runs/{best_expected}/report" in (opened or ""), str(opened))
        if opened:
            st, raw, headers = http_get(opened)
            ok &= check("candidate report served as HTML", st == 200 and "text/html" in headers.get("Content-Type", ""))

        # compare two optimization candidates (Runs-tab selection of their run ids)
        second = next(c["run_id"] for c in ok_candidates if c["run_id"] != best_expected)
        close_library(cdp)
        open_library(cdp)
        open_tab(cdp, "Runs")
        ok &= check("candidate runs listed", cdp.evaluate(f"({WAIT_LIB_JS})({json.dumps(best_expected)})")
                    and cdp.evaluate(f"({WAIT_LIB_JS})({json.dumps(second)})"))
        cdp.evaluate(f"({CLICK_ARIA_JS})({json.dumps(f'Select {best_expected}')})")
        cdp.evaluate(f"({CLICK_ARIA_JS})({json.dumps(f'Select {second}')})")
        open_tab(cdp, "Compare")
        cdp.evaluate(f"({WAIT_SEL_JS})('[aria-label=\"Compare selected runs\"]')")
        cdp.evaluate(f"({CLICK_ARIA_JS})('Compare selected runs')")
        cmp_ok = cdp.evaluate(f"({WAIT_CMP_ROWS_JS})(2)", timeout=70)
        ok &= check("optimization candidates compare", cmp_ok,
                    cdp.evaluate(f"({TEXT_JS})('.cmp-warnings')") or cdp.evaluate(f"({TEXT_JS})('.lib-error')") or "")

        # study history reload (no rerun)
        close_library(cdp)
        open_library(cdp)
        open_tab(cdp, "Optimize")
        ok &= check("study history lists study", cdp.evaluate(f"({WAIT_HISTORY_JS})({json.dumps(study_id or '')})"))
        cdp.evaluate(f"({CLICK_ARIA_JS})({json.dumps(f'Open study {study_id}')})")
        ok &= check("study restores ranked results without rerun", cdp.evaluate(f"({WAIT_CMP_ROWS_JS})(4)", timeout=70))

        # ---- existing flows still work ---------------------------------------
        close_library(cdp)
        set_input(cdp, '[aria-label="Run start date"]', "2025-01-02")
        set_input(cdp, '[aria-label="Run end date"]', "2025-01-02")
        # import remains functional
        doc = cdp.call("DOM.getDocument", {"depth": -1})
        node = cdp.call("DOM.querySelector", {"nodeId": doc["root"]["nodeId"], "selector": "input[type=file]"})
        cdp.call("DOM.setFileInputFiles", {"files": [str(EMA_PATH)], "nodeId": node["nodeId"]})
        cdp.pump(1.2)
        catalog = cdp.evaluate("(async()=>(await (await fetch('http://127.0.0.1:8765/strategies')).json()).strategies.map(s=>s.name))()", timeout=30) or []
        ok &= check("strategy import still works", "EMA Cross" in catalog, str(catalog))
        # real run still works
        select_radix(cdp, "Real run strategy", "Candle Direction")
        ok &= check("real-run enabled", cdp.evaluate(WAIT_ENABLED_JS))
        cdp.evaluate(CLICK_RUN_JS)
        ok &= check("real run still works", cdp.evaluate(f"({WAIT_NEW_RUN_JS})({json.dumps(best_expected)})", timeout=180))
        cdp.evaluate(f"({CLICK_TEXT_JS})(['.run-source button','Demo'])")
        ok &= check("Demo still works", cdp.evaluate(f"({WAIT_ROW_JS})(['{DEMO_ENTRY}'])"))
        ok &= check("run_0001 still works", cdp.evaluate("(id=>{const b=[...document.querySelectorAll('.run-source button')].find(x=>x.textContent.includes(id)); if(!b)return false; b.click(); return true;})('run_0001')")
                    and cdp.evaluate(f"({WAIT_BADGE_JS})('ARCHIVED RUN')"))

        errs = [e for e in cdp.console_errors if "favicon" not in e.lower() and "status of 40" not in e.lower()]
        hydra = [e for e in cdp.console_errors if "hydrat" in e.lower()]
        ok &= check("no unexpected console errors", len(errs) == 0, "; ".join(errs[:2]) if errs else "clean")
        ok &= check("no hydration errors", len(hydra) == 0)
        cdp.ws.close()
    finally:
        if chrome is not None:
            chrome.terminate()
        if bridge.poll() is None:
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
    raise SystemExit(main())
