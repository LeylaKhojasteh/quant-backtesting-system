"""Walk-Forward v1 browser E2E: deterministic out-of-sample evaluation.

Isolated deterministic artifacts (temp runs/studies/walkforwards/strategies/data;
repo public snapshotted/restored). Proves fold generation, per-fold training
optimization reused, OOS runs strictly after training and using the selected
parameters, OOS-only aggregation, no auto-publish, publish-on-open, compare OOS
runs, study-history restore, and preserved existing flows.
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
THRESHOLD = ROOT / "tests" / "fixtures" / "strategies" / "threshold_direction.py"
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
WAIT_WF_JS = ("(() => new Promise(res => {"
              " const ok=()=>!!document.querySelector('.opt-study-head') && document.querySelectorAll('.wf-fold-table tbody tr').length>=3;"
              " if(ok())return res(true);" + _POLL.replace("MAX", "420000") + "}))()")
WAIT_WF_ROWS_JS = ("(n => new Promise(res => {"
                   " const ok=()=>document.querySelectorAll('.wf-fold-table tbody tr').length>=n;"
                   " if(ok())return res(true);" + _POLL.replace("MAX", "120000") + "}))")
WAIT_WF_PARAM_ROWS_JS = ("(n => new Promise(res => {"
                         " const ok=()=>document.querySelectorAll('.wf-params tbody tr').length>=n;"
                         " if(ok())return res(true);" + _POLL.replace("MAX", "60000") + "}))")
WAIT_HISTORY_JS = ("(sid => new Promise(res => {"
                   " const ok=()=>[...document.querySelectorAll('.lib-row')].some(r=>r.textContent.includes(sid));"
                   " if(ok())return res(true);" + _POLL.replace("MAX", "20000") + "}))")
WAIT_LIB_JS = ("(rid => new Promise(res => {"
               " const ok=()=>[...document.querySelectorAll('.lib-row')].some(r=>r.textContent.includes(rid));"
               " if(ok())return res(true);" + _POLL.replace("MAX", "20000") + "}))")
WAIT_GONE_JS = ("(sel => new Promise(res => { const ok=()=>!document.querySelector(sel); if(ok())return res(true);" + _POLL.replace("MAX", "10000") + "}))")
WAIT_CHART_JS = ("(() => new Promise(res => { const ok=()=>!!document.querySelector('.wf-chart svg'); if(ok())return res(true);" + _POLL.replace("MAX", "20000") + "}))()")


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


def _write_ticks(path: Path, start: str, days: int, minutes: int = 15) -> None:
    begin = pd.Timestamp(start, tz="UTC")
    per_day = (24 * 60) // minutes
    rows = []
    for i in range(days * per_day):
        t = begin + pd.Timedelta(minutes=minutes * i)
        price = 2600.0 + 40.0 * math.sin(i / 9.0)
        rows.append({"timestamp": t, "bid": round(price, 2), "ask": round(price + 0.5, 2)})
    pd.DataFrame(rows).to_csv(path, index=False)


def main() -> int:
    ok = True
    manifest_before = MANIFEST.read_text(encoding="utf-8") if MANIFEST.is_file() else None
    files_before = set(p.name for p in PUBLIC.glob("runs-*-result.json"))

    tmp = Path(tempfile.mkdtemp(prefix="qbl-e2e-wf-"))
    runs_dir = tmp / "runs"
    runs_dir.mkdir()
    studies_dir = tmp / "studies"
    walkforwards_dir = tmp / "walkforwards"
    strategy_dir = tmp / "strategies"
    strategy_dir.mkdir()
    data_dir = ROOT / "data"
    shutil.copy(THRESHOLD, strategy_dir / "threshold_direction.py")
    shutil.copytree(FIXTURE, runs_dir / "run_0001")
    shutil.copytree(FIXTURE, runs_dir / "run_0002")

    bridge = subprocess.Popen(
        [sys.executable, "-m", "src.bridge", "--port", "8765",
         "--runs", str(runs_dir), "--public", str(PUBLIC),
         "--strategy-dir", str(strategy_dir), "--data-dir", str(data_dir),
         "--studies", str(studies_dir), "--walkforwards", str(walkforwards_dir)],
        cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    chrome = None
    try:
        ok &= check("bridge starts locally", health(), BRIDGE)
        chrome = launch_chrome(9356)
        t = wait_debugger(9356)
        page = next(x for x in t if x.get("type") == "page")
        cdp = CDP(page["webSocketDebuggerUrl"])
        for dom in ("Page.enable", "Runtime.enable", "Log.enable", "Network.enable", "DOM.enable"):
            cdp.call(dom)
        cdp.call("Page.navigate", {"url": APP_URL})
        ok &= check("app opens on Demo", cdp.evaluate(f"({WAIT_BADGE_JS})('DEMO')"))
        ok &= check("bridge Connected", cdp.evaluate(f"({WAIT_TEXT_JS})(['.engine','Connected'])"))
        cdp.evaluate("window.open=(u)=>{window.__opened=u;return null;};")

        # ---- walk-forward workflow ------------------------------------------
        open_library(cdp)
        open_tab(cdp, "Walk-Forward")
        ok &= check("walk-forward form loads", cdp.evaluate(f"({WAIT_SEL_JS})('[aria-label=\"Walk-forward strategy\"]')"))
        select_radix(cdp, "Walk-forward strategy", "Threshold Direction")
        ok &= check("parameterized strategy selected", cdp.evaluate(f"({WAIT_SEL_JS})('[aria-label=\"Walk-forward values threshold\"]')"))
        set_input(cdp, '[aria-label="Walk-forward values threshold"]', "0")
        set_input(cdp, '[aria-label="Walk-forward values side"]', "both, long")
        set_input(cdp, '[aria-label="Walk-forward values enabled"]', "true")
        set_input(cdp, '[aria-label="Walk-forward stop values"]', "5")
        set_input(cdp, '[aria-label="Walk-forward take values"]', "10")
        set_input(cdp, '[aria-label="Walk-forward start date"]', "2025-01-06")
        set_input(cdp, '[aria-label="Walk-forward end date"]', "2025-01-10")
        set_input(cdp, '[aria-label="Walk-forward train days"]', "2")
        set_input(cdp, '[aria-label="Walk-forward test days"]', "1")
        set_input(cdp, '[aria-label="Walk-forward step days"]', "1")
        count_text = cdp.evaluate(f"({TEXT_JS})('.wf-count')") or ""
        ok &= check("fold count preview correct", "Folds: 3" in count_text and "candidates/fold: 2" in count_text
                    and "total executions: 6" in count_text, count_text[:90])

        manifest_ids_before = {e["runId"] for e in json.loads(MANIFEST.read_text(encoding="utf-8"))["runs"]}
        cdp.evaluate(f"({CLICK_ARIA_JS})('Run walk-forward')")
        ok &= check("walk-forward completes with 3 folds", cdp.evaluate(WAIT_WF_JS, timeout=430),
                    cdp.evaluate(f"({TEXT_JS})('.opt-study-head')"))
        wf_id = (re.search(r"(wf_[0-9a-f]{8})", cdp.evaluate(f"({TEXT_JS})('.opt-study-head')") or "") or [None, None])[1]
        study = json.loads((walkforwards_dir / wf_id / "study.json").read_text(encoding="utf-8")) if wf_id else {}
        ok &= check("walk-forward archive exists", bool(wf_id) and (walkforwards_dir / wf_id / "study.json").is_file(), str(wf_id))
        ok &= check("training optimization study per fold created", all(
            f["optimization_study_id"] and (studies_dir / f["optimization_study_id"] / "study.json").is_file()
            for f in study.get("folds", [])))
        ok &= check("each test range follows its training range", all(
            f["train_end"] < f["test_start"] for f in study.get("folds", [])))
        params_ok, archived_ok = True, True
        for f in study.get("folds", []):
            opt = json.loads((studies_dir / f["optimization_study_id"] / "study.json").read_text(encoding="utf-8"))
            best = next(c for c in opt["candidates"] if c["run_id"] == opt["best_run_id"])
            params_ok &= (f["selected_parameters"] == best["parameters"] and f["selected_run_id"] == opt["best_run_id"])
            archived_ok &= (runs_dir / f["test_run_id"] / "result.json").is_file()
        ok &= check("selected parameters match training study best", params_ok)
        ok &= check("OOS test runs are normal archives", archived_ok)
        ok &= check("nothing auto-published", not (set(f["test_run_id"] for f in study.get("folds", [])) & manifest_ids_before))
        expected_oos = sum(f["test_metrics"]["net_pnl"] for f in study.get("folds", []) if f["status"] == "ok")
        ok &= check("summary matches archived fold results",
                    abs((study.get("aggregate", {}).get("total_oos_net_pnl") or 0) - expected_oos) < 1e-6,
                    f"{study.get('aggregate', {}).get('total_oos_net_pnl')} vs {expected_oos}")
        ok &= check("fold table renders", cdp.evaluate(f"({WAIT_WF_ROWS_JS})(3)"))
        ok &= check("parameter selection history renders", cdp.evaluate(f"({WAIT_WF_PARAM_ROWS_JS})(3)"))
        ok &= check("OOS PnL visualization renders", cdp.evaluate(WAIT_CHART_JS))

        # open one OOS run -> publish + dashboard
        ok_folds = [f for f in study.get("folds", []) if f["status"] == "ok" and (f.get("test_metrics", {}).get("total_trades") or 0) > 0]
        target = max(ok_folds, key=lambda f: f["test_metrics"]["total_trades"]) if ok_folds else study["folds"][0]
        target_run = target["test_run_id"]
        cdp.evaluate(f"({CLICK_ARIA_JS})({json.dumps(f'Open OOS {target_run}')})")
        ok &= check("OOS run opens in dashboard", cdp.evaluate(f"({WAIT_BADGE_JS})('ARCHIVED RUN')"))
        pub = PUBLIC / f"runs-{target_run}-result.json"
        ok &= check("OOS run published through existing mechanism", pub.is_file())
        manifest_after = {e["runId"] for e in json.loads(MANIFEST.read_text(encoding="utf-8"))["runs"]}
        oos_ids = {f["test_run_id"] for f in study.get("folds", [])}
        ok &= check("only the opened OOS run is published", (oos_ids & manifest_after) == {target_run},
                    str(sorted(oos_ids & manifest_after)))
        payload = json.loads(pub.read_text(encoding="utf-8"))
        first = fmt(payload["trades"][0]["entry_price"]) if payload.get("trades") else ""
        ok &= check("dashboard matches OOS result.json", cdp.evaluate(f"({WAIT_ROW_JS})({json.dumps(first)})") if first else False, first)

        # open OOS report (dialog still open on the Walk-Forward tab, so the
        # in-memory study and its report action are still present)
        cdp.evaluate("window.__opened=null;")
        cdp.evaluate(f"({CLICK_ARIA_JS})({json.dumps(f'Open OOS report {target_run}')})")
        opened = cdp.evaluate("window.__opened")
        ok &= check("OOS report opens", bool(opened) and f"/runs/{target_run}/report" in (opened or ""), str(opened))
        if opened:
            st, raw, headers = http_get(opened)
            ok &= check("OOS report served as HTML", st == 200 and "text/html" in headers.get("Content-Type", ""))

        # compare two OOS runs through the existing comparison UI
        second = next(f["test_run_id"] for f in study.get("folds", []) if f["status"] == "ok" and f["test_run_id"] != target_run)
        close_library(cdp)
        open_library(cdp)
        open_tab(cdp, "Runs")
        ok &= check("OOS runs listed in library", cdp.evaluate(f"({WAIT_LIB_JS})({json.dumps(target_run)})")
                    and cdp.evaluate(f"({WAIT_LIB_JS})({json.dumps(second)})"))
        cdp.evaluate(f"({CLICK_ARIA_JS})({json.dumps(f'Select {target_run}')})")
        cdp.evaluate(f"({CLICK_ARIA_JS})({json.dumps(f'Select {second}')})")
        open_tab(cdp, "Compare")
        cdp.evaluate(f"({WAIT_SEL_JS})('[aria-label=\"Compare selected runs\"]')")
        cdp.evaluate(f"({CLICK_ARIA_JS})('Compare selected runs')")
        ok &= check("OOS runs compare through existing UI", cdp.evaluate(f"({WAIT_CMP_ROWS_JS})(2)", timeout=70))

        # reopen the saved study from history (no rerun)
        close_library(cdp)
        open_library(cdp)
        open_tab(cdp, "Walk-Forward")
        ok &= check("history lists walk-forward study", cdp.evaluate(f"({WAIT_HISTORY_JS})({json.dumps(wf_id or '')})"))
        cdp.evaluate(f"({CLICK_ARIA_JS})({json.dumps(f'Open walk-forward {wf_id}')})")
        ok &= check("history restores folds without rerun", cdp.evaluate(f"({WAIT_WF_ROWS_JS})(3)", timeout=70))

        # existing Optimization still works
        open_tab(cdp, "Optimize")
        ok &= check("existing Optimization still works",
                    cdp.evaluate(f"({WAIT_SEL_JS})('[aria-label=\"Optimization strategy\"]')"))
        close_library(cdp)

        # existing real run still works
        set_input(cdp, '[aria-label="Run start date"]', "2025-01-02")
        set_input(cdp, '[aria-label="Run end date"]', "2025-01-02")
        select_radix(cdp, "Real run strategy", "Candle Direction")
        ok &= check("real-run enabled", cdp.evaluate(WAIT_ENABLED_JS))
        cdp.evaluate(CLICK_RUN_JS)
        ok &= check("existing Real Run still works", cdp.evaluate(f"({WAIT_NEW_RUN_JS})({json.dumps(target_run)})", timeout=180))

        # existing run_0001 + Demo still work
        ok &= check("run_0001 still works", cdp.evaluate("(id=>{const b=[...document.querySelectorAll('.run-source button')].find(x=>x.textContent.includes(id)); if(!b)return false; b.click(); return true;})('run_0001')")
                    and cdp.evaluate(f"({WAIT_BADGE_JS})('ARCHIVED RUN')"))
        cdp.evaluate(f"({CLICK_TEXT_JS})(['.run-source button','Demo'])")
        ok &= check("Demo still works", cdp.evaluate(f"({WAIT_ROW_JS})({json.dumps(DEMO_ENTRY)})"))

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
