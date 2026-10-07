"""Research Workspace v1 browser E2E.

Covers the full milestone against an isolated bridge (temp runs + temp strategy
dir; repo public snapshotted/restored): Run Library listing/ordering/open/report/
reuse, strategy management + safe delete, builtin protection, unpublished->publish
->dashboard, run delete (archive + artifact + manifest + selector), historical
integrity, and no console/hydration errors.
"""

import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

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
EMA_PATH = ROOT / "tests" / "fixtures" / "import" / "ema_cross.py"
EMA_SOURCE = EMA_PATH.read_text(encoding="utf-8")
DEMO_ENTRY = "2,799.84"
RUN0001_ENTRY = "2,626.08"

_POLL = "const t0=Date.now();const iv=setInterval(()=>{if(ok()||Date.now()-t0>MAX){clearInterval(iv);res(ok());}},250);"
WAIT_SOURCE_JS = ("(needle => new Promise(res => {"
                  " const ok=()=>[...document.querySelectorAll('.run-source button')].some(b=>b.textContent.includes(needle));"
                  " if(ok())return res(true);" + _POLL.replace("MAX", "25000") + "}))")
WAIT_NEW_RUN_JS = ("(prev => new Promise(res => {"
                   " const ok=()=>{const t=(document.querySelector('.run-status')||{}).textContent||'';"
                   "  const m=t.match(/Archived Run · (\\S+) ·/); return !!m && m[1]!==prev;};"
                   " if(ok())return res(true);" + _POLL.replace("MAX", "150000") + "}))")
WAIT_STATUS_ID_JS = ("(rid => new Promise(res => {"
                     " const ok=()=>(((document.querySelector('.run-status')||{}).textContent)||'').includes(rid);"
                     " if(ok())return res(true);" + _POLL.replace("MAX", "30000") + "}))")
WAIT_ROW_JS = ("(([text]) => new Promise(res => {"
               " const ok=()=>{const r=document.querySelector('.overview-trade-log tbody tr');"
               "  return !!r && r.textContent.includes(text);};"
               " if(ok())return res(true);" + _POLL.replace("MAX", "25000") + "}))")
WAIT_LIB_JS = ("((runId) => new Promise(res => {"
               " const ok=()=>[...document.querySelectorAll('.lib-row')].some(r=>r.textContent.includes(runId));"
               " if(ok())return res(true);" + _POLL.replace("MAX", "20000") + "}))")
WAIT_GONE_JS = ("(sel => new Promise(res => {"
                " const ok=()=>!document.querySelector(sel);"
                " if(ok())return res(true);" + _POLL.replace("MAX", "10000") + "}))")
WAIT_ENABLED_JS = ("(() => new Promise(res => {"
                   " const ok=()=>{const b=[...document.querySelectorAll('.real-run button')].find(x=>x.textContent.includes('Run Backtest'));return !!b && !b.disabled;};"
                   " if(ok())return res(true);" + _POLL.replace("MAX", "30000") + "}))()")
INSTALL_OBSERVER_JS = """(() => {
  window.__sawRunning=false;
  const scan=()=>{if([...document.querySelectorAll('.real-run button')].some(b=>b.textContent.includes('Running')))window.__sawRunning=true;};
  scan(); if(window.__runMo)window.__runMo.disconnect();
  const mo=new MutationObserver(scan); mo.observe(document.documentElement,{subtree:true,childList:true,characterData:true,attributes:true}); window.__runMo=mo; return 'ok';
})()"""

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
CLICK_SOURCE_JS = """((needle) => { const b=[...document.querySelectorAll('.run-source button')].find(x=>x.textContent.includes(needle)); if(!b) return 'NO'; b.click(); return 'OK'; })"""


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


def open_runs_tab(cdp):
    cdp.evaluate(f"({CLICK_TEXT_JS})(['.workspace-tabs button','Runs'])")


def open_strategies_tab(cdp):
    cdp.evaluate(f"({CLICK_TEXT_JS})(['.workspace-tabs button','Strategies'])")


def main() -> int:
    ok = True
    manifest_before = MANIFEST.read_text(encoding="utf-8") if MANIFEST.is_file() else None
    files_before = set(p.name for p in PUBLIC.glob("runs-*-result.json"))

    tmp = Path(tempfile.mkdtemp(prefix="qbl-e2e-ws-"))
    runs_dir = tmp / "runs"
    runs_dir.mkdir()
    strategy_dir = tmp / "strategies"
    strategy_dir.mkdir()
    shutil.copytree(ROOT / "tests" / "fixtures" / "run_0001", runs_dir / "run_0001")
    shutil.copy(ROOT / "tests" / "fixtures" / "strategies" / "threshold_direction.py", strategy_dir / "threshold_direction.py")

    # Unpublished local run created directly through the shared workflow (no publish).
    sys.path.insert(0, str(ROOT))
    from src import run_backtest, run_request  # noqa: E402
    local_request = run_request.BacktestRunRequest(
        strategy=run_request.StrategyRequest(selector="candle_direction", parameters={}),
        market=run_request.MarketRequest(
            symbol="XAUUSD", timeframe="15min", start="2025-01-02", end="2025-01-02",
            data_source=str(ROOT / "data" / "XAUUSD_DUKAS_2025_01_ticks.csv"),
        ),
        execution=run_request.ExecutionRequest(stop_distance=5, take_profit_distance=10),
        publish=False,
    )
    run_backtest.execute_run(local_request, out=str(runs_dir), report=True, public=str(PUBLIC), run_id="run_local")

    bridge = subprocess.Popen(
        [sys.executable, "-m", "src.bridge", "--port", "8765",
         "--runs", str(runs_dir), "--public", str(PUBLIC),
         "--strategy-dir", str(strategy_dir), "--data-dir", "data"],
        cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    chrome = None
    try:
        ok &= check("bridge starts locally", health(), BRIDGE)
        chrome = launch_chrome(9352)
        t = wait_debugger(9352)
        page = next(x for x in t if x.get("type") == "page")
        cdp = CDP(page["webSocketDebuggerUrl"])
        for dom in ("Page.enable", "Runtime.enable", "Log.enable", "Network.enable", "DOM.enable"):
            cdp.call(dom)
        cdp.call("Page.navigate", {"url": APP_URL})

        ok &= check("Demo still loads", cdp.evaluate(f"({WAIT_BADGE_JS})('DEMO')"), "DEMO")
        ok &= check("bridge Connected (hydrated)", cdp.evaluate(f"({WAIT_TEXT_JS})(['.engine','Connected'])"),
                    cdp.evaluate(f"({TEXT_JS})('.engine')"))
        ok &= check("run_0001 present in selector", cdp.evaluate(f"({WAIT_SOURCE_JS})('run_0001')"))
        ok &= check("real-run catalogs load", cdp.evaluate(f"({WAIT_TEXT_JS})(['[aria-label=\"Real run strategy\"]','Candle Direction'])"),
                    cdp.evaluate(f"({TEXT_JS})('[aria-label=\"Real run strategy\"]')"))
        ok &= check("dataset catalog loads", cdp.evaluate(f"({WAIT_TEXT_JS})(['[aria-label=\"Real run dataset\"]','XAUUSD_'])"))

        # ---- Run Library lists runs ------------------------------------------
        open_library(cdp)
        ok &= check("Run Library loads", cdp.evaluate(f"({WAIT_SEL_JS})('.lib-row')"))
        ok &= check("run_0001 appears", cdp.evaluate(f"({WAIT_LIB_JS})('run_0001')"))
        ok &= check("unpublished local run listed", cdp.evaluate(f"({WAIT_LIB_JS})('run_local')"))
        local_row = cdp.evaluate("(() => { const r=[...document.querySelectorAll('.lib-row')].find(x=>x.textContent.includes('run_local')); return r?r.textContent.replace(/\\s+/g,' '):''; })()")
        ok &= check("unpublished state shown as LOCAL", "LOCAL" in local_row and "PUBLISHED" not in local_row, local_row[:80])
        close_library(cdp)

        # ---- create a real run from the existing form ------------------------
        set_input(cdp, '[aria-label="Run start date"]', "2025-01-02")
        set_input(cdp, '[aria-label="Run end date"]', "2025-01-02")
        ok &= check("Run enabled", cdp.evaluate(WAIT_ENABLED_JS))
        cdp.evaluate(INSTALL_OBSERVER_JS)
        cdp.evaluate(CLICK_RUN_JS)
        ok &= check("new run completes + auto-loads", cdp.evaluate(f"({WAIT_NEW_RUN_JS})('')", timeout=180),
                    cdp.evaluate(f"({TEXT_JS})('.rr-notice')"))
        status_text = cdp.evaluate(f"({TEXT_JS})('.run-status')") or ""
        run_a = (re.search(r"Archived Run · (\S+) ·", status_text) or [None, None])[1]
        ok &= check("run A created", bool(run_a), status_text[:100])

        # ---- library shows run A; open it in dashboard -----------------------
        open_library(cdp)
        ok &= check("run A appears in Run Library", cdp.evaluate(f"({WAIT_LIB_JS})({json.dumps(run_a or '')})"))
        cdp.evaluate(f"({CLICK_ARIA_JS})({json.dumps(f'Open {run_a} in dashboard')})")
        close_library(cdp)
        ok &= check("run A opens in dashboard", cdp.evaluate(f"({WAIT_BADGE_JS})('ARCHIVED RUN')"))
        published_a = PUBLIC / f"runs-{run_a}-result.json"
        payload_a = json.loads(published_a.read_text(encoding="utf-8")) if published_a.is_file() else {}
        first_a = f"{payload_a['trades'][0]['entry_price']:,.2f}" if payload_a.get("trades") else ""
        ok &= check("run A data matches result.json", cdp.evaluate(f"({WAIT_ROW_JS})(['{first_a}'])"), first_a)

        # ---- open standalone report ------------------------------------------
        cdp.evaluate("window.__opened=null; window.open=(u)=>{window.__opened=u;return null;};")
        open_library(cdp)
        ok &= check("run A appears for report", cdp.evaluate(f"({WAIT_LIB_JS})({json.dumps(run_a or '')})"))
        cdp.evaluate(f"({CLICK_ARIA_JS})({json.dumps(f'Open report for {run_a}')})")
        opened = cdp.evaluate("window.__opened")
        ok &= check("report action opens bridge URL", bool(opened) and f"/runs/{run_a}/report" in (opened or ""), str(opened))
        if opened:
            status, raw, headers = http_get(opened)
            ok &= check("standalone report served as HTML", status == 200 and "text/html" in headers.get("Content-Type", "") and run_a.encode() in raw, f"HTTP {status}")
        close_library(cdp)

        # ---- reuse configuration --------------------------------------------
        open_library(cdp)
        ok &= check("reuse: run A row", cdp.evaluate(f"({WAIT_LIB_JS})({json.dumps(run_a or '')})"))
        cdp.evaluate(f"({CLICK_ARIA_JS})({json.dumps(f'Reuse configuration from {run_a}')})")
        close_library(cdp)
        ok &= check("reuse restored dates", cdp.evaluate(
            "(document.querySelector('[aria-label=\"Run start date\"]').value==='2025-01-02')"))
        strat_text = cdp.evaluate(f"({TEXT_JS})('[aria-label=\"Real run strategy\"]')") or ""
        ok &= check("reuse restored strategy", "Candle Direction" in strat_text, strat_text[:60])
        # change execution settings -> second run differs
        cdp.evaluate(f"({CLICK_ARIA_JS})('Strategy & Trade Settings')")
        cdp.evaluate(f"({WAIT_SEL_JS})('[aria-label=\"Stop distance\"]')")
        set_input(cdp, '[aria-label="Stop distance"]', "25")
        escape(cdp)
        set_input(cdp, '[aria-label="Run start date"]', "2025-01-02")
        set_input(cdp, '[aria-label="Run end date"]', "2025-01-02")
        cdp.evaluate(CLICK_RUN_JS)
        ok &= check("re-run with changed execution completes", cdp.evaluate(f"({WAIT_NEW_RUN_JS})({json.dumps(run_a or '')})", timeout=180))
        status_text = cdp.evaluate(f"({TEXT_JS})('.run-status')") or ""
        run_ctrl = (re.search(r"Archived Run · (\S+) ·", status_text) or [None, None])[1]
        payload_ctrl = json.loads((PUBLIC / f"runs-{run_ctrl}-result.json").read_text(encoding="utf-8")) if run_ctrl and (PUBLIC / f"runs-{run_ctrl}-result.json").is_file() else {}
        a_trades = (payload_a.get("metrics") or {}).get("total_trades")
        c_trades = (payload_ctrl.get("metrics") or {}).get("total_trades")
        ok &= check("changed execution produced a different run", run_ctrl and run_ctrl != run_a and a_trades != c_trades,
                    f"A={a_trades} ctrl={c_trades}")

        # ---- import strategy + run it, then delete it ------------------------
        doc = cdp.call("DOM.getDocument", {"depth": -1})
        node = cdp.call("DOM.querySelector", {"nodeId": doc["root"]["nodeId"], "selector": "input[type=file]"})
        cdp.call("DOM.setFileInputFiles", {"files": [str(EMA_PATH)], "nodeId": node["nodeId"]})
        cdp.pump(1.0)
        ok &= check("strategy imported and selected", cdp.evaluate(f"({WAIT_TEXT_JS})(['[aria-label=\"Real run strategy\"]','EMA Cross'])"),
                    cdp.evaluate(f"({TEXT_JS})('[aria-label=\"Real run strategy\"]')"))
        ok &= check("imported file on disk", (strategy_dir / "ema_cross.py").is_file())
        # run EMA with non-default parameters
        cdp.evaluate(f"({CLICK_ARIA_JS})('Strategy & Trade Settings')")
        cdp.evaluate(f"({WAIT_SEL_JS})('[aria-label=\"Strategy parameter fast_period\"]')")
        set_input(cdp, '[aria-label="Strategy parameter fast_period"]', "2")
        set_input(cdp, '[aria-label="Strategy parameter slow_period"]', "5")
        escape(cdp)
        set_input(cdp, '[aria-label="Run start date"]', "2025-01-02")
        set_input(cdp, '[aria-label="Run end date"]', "2025-01-02")
        cdp.evaluate(CLICK_RUN_JS)
        ok &= check("EMA run completes", cdp.evaluate(f"({WAIT_NEW_RUN_JS})({json.dumps(run_ctrl or '')})", timeout=180))
        status_text = cdp.evaluate(f"({TEXT_JS})('.run-status')") or ""
        run_b = (re.search(r"Archived Run · (\S+) ·", status_text) or [None, None])[1]
        payload_b = json.loads((PUBLIC / f"runs-{run_b}-result.json").read_text(encoding="utf-8")) if run_b and (PUBLIC / f"runs-{run_b}-result.json").is_file() else {}
        ok &= check("EMA run archived with imported strategy", payload_b.get("strategy", {}).get("name") == "EMA Cross")
        ok &= check("EMA archive keeps strategy snapshot",
                    (runs_dir / run_b / "strategy.py").read_text(encoding="utf-8") == EMA_SOURCE if run_b else False)

        # Strategy Management shows it; delete with confirmation
        open_library(cdp)
        open_strategies_tab(cdp)
        ok &= check("Strategy Management lists EMA Cross", cdp.evaluate(f"({WAIT_TEXT_JS})(['.workspace-dialog','EMA Cross'])"))
        cdp.evaluate(f"({CLICK_ARIA_JS})('Delete strategy EMA Cross')")
        cdp.pump(0.3)
        cdp.evaluate(f"({CLICK_TEXT_JS})(['.lib-confirm button','Delete'])")
        cdp.pump(1.0)
        ok &= check("imported strategy deleted from disk", not (strategy_dir / "ema_cross.py").exists())
        catalog_after = cdp.evaluate("(async()=>(await (await fetch('http://127.0.0.1:8765/strategies')).json()).strategies.map(s=>s.name))()", timeout=30) or []
        ok &= check("catalog no longer lists EMA Cross", "EMA Cross" not in catalog_after, str(catalog_after))

        # builtin protected
        open_runs_tab(cdp)
        open_strategies_tab(cdp)
        builtin_delete = cdp.evaluate(
            "(async()=>{const r=await fetch('http://127.0.0.1:8765/strategies/candle_direction',{method:'DELETE'});const j=await r.json();return {status:r.status,code:j.error&&j.error.code};})()",
            timeout=30)
        ok &= check("builtin strategy deletion rejected", builtin_delete.get("status") == 400 and builtin_delete.get("code") == "builtin_strategy_protected", str(builtin_delete))
        close_library(cdp)

        # historical EMA run remains viewable + report available
        ok &= check("historical EMA report still available", http_get(f"{BRIDGE}/runs/{run_b}/report")[0] == 200)
        ok &= check("historical EMA archive intact", (runs_dir / run_b / "strategy.py").is_file() if run_b else False)

        # ---- open an unpublished run -> auto publish -> dashboard ------------
        open_library(cdp)
        open_runs_tab(cdp)
        ok &= check("unpublished run listed", cdp.evaluate(f"({WAIT_LIB_JS})('run_local')"))
        cdp.evaluate(f"({CLICK_ARIA_JS})('Open run_local in dashboard')")
        close_library(cdp)
        ok &= check("unpublished run published + opened",
                    cdp.evaluate(f"({WAIT_STATUS_ID_JS})('run_local')") and (PUBLIC / "runs-run_local-result.json").is_file())

        # ---- delete a published run ------------------------------------------
        # make run A active so we can assert the dashboard returns to Demo
        cdp.evaluate(f"({CLICK_SOURCE_JS})({json.dumps(run_a or '')})")
        cdp.pump(0.6)
        runs_before_del = cdp.evaluate("document.querySelectorAll('.run-source button').length")
        open_library(cdp)
        open_runs_tab(cdp)
        ok &= check("run A listed for delete", cdp.evaluate(f"({WAIT_LIB_JS})({json.dumps(run_a or '')})"))
        cdp.evaluate(f"({CLICK_ARIA_JS})({json.dumps(f'Delete {run_a}')})")
        cdp.pump(0.3)
        cdp.evaluate(f"({CLICK_TEXT_JS})(['.lib-confirm button','Delete'])")
        cdp.pump(1.5)
        ok &= check("run A archive deleted", not (runs_dir / run_a).exists() if run_a else False)
        ok &= check("run A public artifact deleted", not (PUBLIC / f"runs-{run_a}-result.json").exists())
        manifest_now = json.loads(MANIFEST.read_text(encoding="utf-8"))
        ok &= check("run A manifest entry removed", run_a not in [r["runId"] for r in manifest_now["runs"]])
        close_library(cdp)
        cdp.pump(0.6)
        runs_after_del = cdp.evaluate("document.querySelectorAll('.run-source button').length")
        ok &= check("run-source selector refreshed", runs_after_del == runs_before_del - 1, f"{runs_before_del} -> {runs_after_del}")
        ok &= check("other runs untouched", (runs_dir / "run_0001").is_dir() and (PUBLIC / "runs-run_0001-result.json").is_file())

        # demo still works
        cdp.evaluate(f"({CLICK_SOURCE_JS})('Demo')")
        ok &= check("Demo restores", cdp.evaluate(f"({WAIT_ROW_JS})(['{DEMO_ENTRY}'])") )
        ok &= check("run_0001 still opens", cdp.evaluate(f"({CLICK_SOURCE_JS})('run_0001')") == "OK" and cdp.evaluate(f"({WAIT_ROW_JS})(['{RUN0001_ENTRY}'])") )

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
