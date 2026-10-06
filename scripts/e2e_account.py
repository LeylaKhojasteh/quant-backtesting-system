"""Account & Trading Economics v1 browser E2E.

Isolated deterministic artifacts: temp runs/studies/walkforwards/strategies, the
read-only repo data dir, and the repo public dir snapshotted/restored. Proves the
optional account model converts price-term results truthfully, archives its
config, renders in dashboard/report, stays off by default, and that comparison,
optimization and walk-forward still work.
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

APP_URL = "http://127.0.0.1:5173/"
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
WAIT_NEW_RUN_JS = ("(prev => new Promise(res => {"
                   " const ok=()=>{const t=(document.querySelector('.run-status')||{}).textContent||'';"
                   "  const m=t.match(/Archived reference · (\\S+) ·/); return !!m && m[1]!==prev;};"
                   " if(ok())return res(true);" + _POLL.replace("MAX", "180000") + "}))")
WAIT_METRICS_JS = ("(text => new Promise(res => {"
                   " const ok=()=>{const e=document.querySelector('.metrics-grouped'); return !!e && e.textContent.includes(text);};"
                   " if(ok())return res(true);" + _POLL.replace("MAX", "20000") + "}))")
WAIT_EQUITY_JS = ("(text => new Promise(res => {"
                  " const ok=()=>{const e=document.querySelector('.equity'); return !!e && e.textContent.includes(text);};"
                  " if(ok())return res(true);" + _POLL.replace("MAX", "20000") + "}))")
WAIT_LIB_JS = ("(rid => new Promise(res => {"
               " const ok=()=>[...document.querySelectorAll('.lib-row')].some(r=>r.textContent.includes(rid));"
               " if(ok())return res(true);" + _POLL.replace("MAX", "20000") + "}))")
WAIT_CMP_WARN_JS = ("(text => new Promise(res => {"
                    " const ok=()=>{const e=document.querySelector('.cmp-warnings'); return !!e && e.textContent.includes(text);};"
                    " if(ok())return res(true);" + _POLL.replace("MAX", "60000") + "}))")
WAIT_GONE_JS = ("(sel => new Promise(res => { const ok=()=>!document.querySelector(sel); if(ok())return res(true);" + _POLL.replace("MAX", "10000") + "}))")


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


def num(v, digits=4):
    """Mirror src/report.py _num() formatting for floats."""
    if v is None:
        return "N/A"
    return f"{float(v):.{digits}f}".rstrip("0").rstrip(".")


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
    fixture_result = json.loads((FIXTURE / "result.json").read_text(encoding="utf-8"))

    tmp = Path(tempfile.mkdtemp(prefix="qbl-e2e-acct-"))
    runs_dir = tmp / "runs"
    runs_dir.mkdir()
    strategy_dir = tmp / "strategies"
    strategy_dir.mkdir()
    shutil.copy(THRESHOLD, strategy_dir / "threshold_direction.py")
    shutil.copytree(FIXTURE, runs_dir / "run_0001")

    bridge = subprocess.Popen(
        [sys.executable, "-m", "src.bridge", "--port", "8765",
         "--runs", str(runs_dir), "--public", str(PUBLIC),
         "--strategy-dir", str(strategy_dir), "--data-dir", str(ROOT / "data"),
         "--studies", str(tmp / "studies"), "--walkforwards", str(tmp / "walkforwards")],
        cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    chrome = None
    try:
        ok &= check("bridge starts locally", health(), BRIDGE)
        chrome = launch_chrome(9358)
        t = wait_debugger(9358)
        page = next(x for x in t if x.get("type") == "page")
        cdp = CDP(page["webSocketDebuggerUrl"])
        for dom in ("Page.enable", "Runtime.enable", "Log.enable", "Network.enable", "DOM.enable"):
            cdp.call(dom)
        cdp.call("Page.navigate", {"url": APP_URL})
        ok &= check("app opens on Demo", cdp.evaluate(f"({WAIT_BADGE_JS})('DEMO')"))
        ok &= check("bridge Connected", cdp.evaluate(f"({WAIT_TEXT_JS})(['.engine','Connected'])"))
        cdp.evaluate("window.open=(u)=>{window.__opened=u;return null;};")

        # 3. run_0001 remains account-unavailable
        cdp.evaluate("(id=>{const b=[...document.querySelectorAll('.run-source button')].find(x=>x.textContent.includes(id)); if(b) b.click(); return !!b;})('run_0001')")
        ok &= check("run_0001 opens as archived", cdp.evaluate(f"({WAIT_BADGE_JS})('ARCHIVED RUN')"))
        ok &= check("run_0001 is account-unavailable",
                    cdp.evaluate(f"({WAIT_SEL_JS})('[aria-label=\"Balance: Pending\"]')")
                    and cdp.evaluate(f"({WAIT_SEL_JS})('[aria-label=\"Return %: Pending\"]')"))

        # 4-6. configure a real run with an explicit USD account + XAUUSD economics
        ok &= check("real-run enabled", cdp.evaluate(WAIT_ENABLED_JS))
        select_radix(cdp, "Real run strategy", "Candle Direction")
        set_input(cdp, '[aria-label="Run start date"]', "2025-01-02")
        set_input(cdp, '[aria-label="Run end date"]', "2025-01-02")
        cdp.evaluate(f"({CLICK_ARIA_JS})('Enable account model')")
        ok &= check("account model form opens", cdp.evaluate(f"({WAIT_SEL_JS})('[aria-label=\"Account currency\"]')"))
        set_input(cdp, '[aria-label="Account currency"]', "USD")
        set_input(cdp, '[aria-label="Account starting balance"]', "10000")
        set_input(cdp, '[aria-label="Account position size lots"]', "1")
        set_input(cdp, '[aria-label="Account commission per lot"]', "7")
        set_input(cdp, '[aria-label="Instrument contract size"]', "100")
        set_input(cdp, '[aria-label="Instrument quote currency"]', "USD")

        prev = (re.search(r"Archived reference · (\S+) ·", cdp.evaluate(f"({TEXT_JS})('.run-status')") or "") or [None, None])[1]
        cdp.evaluate(CLICK_RUN_JS)
        ok &= check("account run publishes and opens", cdp.evaluate(f"({WAIT_NEW_RUN_JS})({json.dumps(prev)})", timeout=200))
        run_id = (re.search(r"Archived reference · (\S+) ·", cdp.evaluate(f"({TEXT_JS})('.run-status')") or "") or [None, None])[1]
        result = json.loads((PUBLIC / f"runs-{run_id}-result.json").read_text(encoding="utf-8"))
        acct = result.get("account") or {}
        metrics = acct.get("metrics") or {}

        # 8. archive carries account/instrument config
        _st, detail_raw, _ = http_get(f"{BRIDGE}/runs/{run_id}")
        detail = (json.loads(detail_raw) if detail_raw else {}).get("run", {})
        ok &= check("archive carries account + instrument config",
                    detail.get("account", {}).get("commission_per_lot_round_turn") == 7
                    and detail.get("instrument", {}).get("contract_size") == 100)

        # 9. price-term result matches the same execution as run_0001
        ok &= check("price-term result unchanged by account layer",
                    abs(result["metrics"]["net_pnl"] - fixture_result["metrics"]["net_pnl"]) < 1e-6
                    and result["metrics"]["total_trades"] == fixture_result["metrics"]["total_trades"])

        # 10-13. account math
        lots, commission = 1, 7
        gross_expected = result["metrics"]["net_pnl"] * 100 * lots
        comm_expected = commission * result["metrics"]["total_trades"]
        ending_expected = 10000 + gross_expected - comm_expected
        ok &= check("account PnL calculated", acct.get("available") is True)
        ok &= check("commission applied exactly once",
                    abs(metrics["total_commission"] - comm_expected) < 1e-6, str(metrics.get("total_commission")))
        ok &= check("starting/ending balance correct",
                    metrics["starting_balance"] == 10000 and abs(metrics["ending_balance"] - ending_expected) < 1e-6,
                    f"{metrics.get('starting_balance')} -> {metrics.get('ending_balance')}")
        ok &= check("return % correct",
                    abs(metrics["total_return_pct"] - (ending_expected - 10000) / 10000 * 100) < 1e-6,
                    str(metrics.get("total_return_pct")))

        # 14. dashboard renders truthful account values
        ok &= check("dashboard shows ending balance", cdp.evaluate(f"({WAIT_METRICS_JS})({json.dumps(fmt(ending_expected))})"))
        ok &= check("dashboard shows net account PnL + commission",
                    cdp.evaluate(f"({WAIT_METRICS_JS})('Net Account PnL')")
                    and cdp.evaluate(f"({WAIT_METRICS_JS})('Commission')"))

        # realized balance chart renders (isolated Equity panel, Balance mode)
        select_radix(cdp, "Performance series", "Balance")
        ok &= check("realized balance curve renders", cdp.evaluate(f"({WAIT_EQUITY_JS})('Balance · account currency')"))

        # 15. unsupported metrics stay unavailable
        ok &= check("Sharpe stays unavailable",
                    cdp.evaluate(f"({WAIT_SEL_JS})('[aria-label=\"Sharpe Ratio: Pending\"]')"))

        # 16-17. report
        st, raw, headers = http_get(f"{BRIDGE}/runs/{run_id}/report")
        report = raw.decode("utf-8", "replace")
        ok &= check("report served as HTML", st == 200 and "text/html" in headers.get("Content-Type", ""))
        ok &= check("report contains account config + economics",
                    "Account configuration" in report and "Instrument economics" in report
                    and "Realized account balance" in report)
        ok &= check("report contains account performance",
                    "Ending balance" in report and num(metrics["net_pnl"]) in report)
        first_net = acct["trades"][0]["net_account_pnl"] if acct.get("trades") else None
        ok &= check("report trade table shows account PnL",
                    first_net is not None and num(first_net) in report, str(first_net))

        # 18-20. open + reload from Run Library, metrics identical
        close_library(cdp)
        open_library(cdp)
        open_tab(cdp, "Runs")
        ok &= check("account run listed in library", cdp.evaluate(f"({WAIT_LIB_JS})({json.dumps(run_id)})"))
        cdp.evaluate(f"({CLICK_ARIA_JS})({json.dumps(f'Open {run_id} in dashboard')})")
        ok &= check("open run from library", cdp.evaluate(f"({WAIT_BADGE_JS})('ARCHIVED RUN')"))
        cdp.evaluate("(id=>{const b=[...document.querySelectorAll('.run-source button')].find(x=>x.textContent.includes(id)); if(b) b.click(); return !!b;})('run_0001')")
        cdp.evaluate(f"({CLICK_ARIA_JS})({json.dumps(f'Open {run_id} in dashboard')})")
        ok &= check("account metrics identical after reload", cdp.evaluate(f"({WAIT_METRICS_JS})({json.dumps(fmt(ending_expected))})"))

        # 21-22. compare to a no-account run -> warning, no misleading crown
        close_library(cdp)
        open_library(cdp)
        open_tab(cdp, "Runs")
        cdp.evaluate(f"({CLICK_ARIA_JS})({json.dumps(f'Select {run_id}')})")
        cdp.evaluate(f"({CLICK_ARIA_JS})('Select run_0001')")
        open_tab(cdp, "Compare")
        cdp.evaluate(f"({WAIT_SEL_JS})('[aria-label=\"Compare selected runs\"]')")
        cdp.evaluate(f"({CLICK_ARIA_JS})('Compare selected runs')")
        ok &= check("comparison warns about mixed account models",
                    cdp.evaluate(f"({WAIT_CMP_WARN_JS})('account model')", timeout=70))
        ok &= check("no-account run shown Not available in account table",
                    cdp.evaluate("(() => [...document.querySelectorAll('.cmp-table')].some(t => t.textContent.includes('Not available')))()"))

        # 23-24. existing optimization + walk-forward still function
        open_tab(cdp, "Optimize")
        ok &= check("existing Optimization still works", cdp.evaluate(f"({WAIT_SEL_JS})('[aria-label=\"Optimization strategy\"]')"))
        open_tab(cdp, "Walk-Forward")
        ok &= check("existing Walk-Forward still works", cdp.evaluate(f"({WAIT_SEL_JS})('[aria-label=\"Walk-forward strategy\"]')"))
        close_library(cdp)

        # Demo unchanged
        cdp.evaluate(f"({CLICK_TEXT_JS})(['.run-source button','Demo'])")
        ok &= check("Demo still works", cdp.evaluate(f"({WAIT_TEXT_JS})(['.demo-badge','DEMO'])"))
        ok &= check("demo price-term engine unchanged",
                    cdp.evaluate(f"({WAIT_METRICS_JS})({json.dumps('7')})"))

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
