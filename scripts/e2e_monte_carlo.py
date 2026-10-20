"""Monte Carlo v1 browser E2E (deterministic).

Isolated artifacts: temp runs/montecarlo/studies/walkforwards/strategies, the
read-only repo data dir, and the repo public dir snapshotted/restored. Proves
shuffle vs bootstrap semantics, price vs account basis, envelope/histograms,
history reload, and missing-source handling, without disturbing existing flows.
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
from scripts.e2e_run_switch import e2e_main, launch_bridge, require_bridge_port_free

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
WAIT_NEW_RUN_JS = ("(prev => new Promise(res => {"
                   " const ok=()=>{const t=(document.querySelector('.run-status')||{}).textContent||'';"
                   "  const m=t.match(/Archived Run · (\\S+) ·/); return !!m && m[1]!==prev;};"
                   " if(ok())return res(true);" + _POLL.replace("MAX", "180000") + "}))")
WAIT_MC_JS = ("(() => new Promise(res => {"
              " const ok=()=>!!document.querySelector('.mc-study-head') && !!document.querySelector('.mc-envelope svg');"
              " if(ok())return res(true);" + _POLL.replace("MAX", "60000") + "}))()")
WAIT_MC_NEW_JS = ("(prev => new Promise(res => {"
                  " const ok=()=>{const h=(document.querySelector('.mc-study-head')||{}).textContent||'';"
                  "  const m=h.match(/(mc_[0-9a-f]{8})/); return !!m && m[1]!==prev && !!document.querySelector('.mc-envelope svg');};"
                  " if(ok())return res(true);" + _POLL.replace("MAX", "90000") + "}))")
WAIT_MC_HIST_JS = ("(id => new Promise(res => {"
                   " const ok=()=>[...document.querySelectorAll('.lib-row')].some(r=>r.textContent.includes(id));"
                   " if(ok())return res(true);" + _POLL.replace("MAX", "20000") + "}))")
WAIT_MISSING_JS = ("(id => new Promise(res => {"
                   " const ok=()=>!!document.querySelector('.mc-missing') && (document.querySelector('.mc-study-head')||{}).textContent.includes(id);"
                   " if(ok())return res(true);" + _POLL.replace("MAX", "30000") + "}))")
WAIT_LIB_JS = ("(rid => new Promise(res => {"
               " const ok=()=>[...document.querySelectorAll('.lib-row')].some(r=>r.textContent.includes(rid));"
               " if(ok())return res(true);" + _POLL.replace("MAX", "20000") + "}))")
WAIT_CMP_ROWS_JS = ("(n => new Promise(res => {"
                    " const ok=()=>document.querySelectorAll('.cmp-table tbody tr').length>=n;"
                    " if(ok())return res(true);" + _POLL.replace("MAX", "60000") + "}))")
WAIT_METRICS_JS = ("(text => new Promise(res => {"
                   " const ok=()=>{const e=document.querySelector('.metrics-grouped'); return !!e && e.textContent.includes(text);};"
                   " if(ok())return res(true);" + _POLL.replace("MAX", "20000") + "}))")
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


def http_delete(url):
    req = urllib.request.Request(url, method="DELETE")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, json.loads(r.read())
    except Exception as exc:  # noqa: BLE001
        return 0, {"error": str(exc)}


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


def choose_radix(cdp, trigger_aria, option_text):
    """Select a Radix option and confirm the trigger now shows it (retry-safe)."""
    selector = f'[aria-label="{trigger_aria}"]'
    target = option_text.split(' ')[0].lower()
    for _ in range(5):
        select_radix(cdp, trigger_aria, option_text)
        for _ in range(20):
            shown = (cdp.evaluate(f"({TEXT_JS})({json.dumps(selector)})") or "").lower()
            if target in shown:
                return True
            time.sleep(0.2)
    return False


def mc_study(cdp, montecarlo_dir):
    head = cdp.evaluate(f"({TEXT_JS})('.mc-study-head')") or ""
    study_id = (re.search(r"(mc_[0-9a-f]{8})", head) or [None, None])[1]
    if not study_id:
        return None, None
    return study_id, json.loads((montecarlo_dir / study_id / "study.json").read_text(encoding="utf-8"))


def current_mc_id(cdp):
    head = cdp.evaluate(f"({TEXT_JS})('.mc-study-head')") or ""
    return (re.search(r"(mc_[0-9a-f]{8})", head) or [None, None])[1]


def run_mc(cdp, timeout=120):
    prev = current_mc_id(cdp)
    cdp.evaluate(f"({CLICK_ARIA_JS})('Run Monte Carlo')")
    return cdp.evaluate(f"({WAIT_MC_NEW_JS})({json.dumps(prev)})", timeout=timeout)


def main() -> int:
    ok = True
    manifest_before = MANIFEST.read_text(encoding="utf-8") if MANIFEST.is_file() else None
    files_before = set(p.name for p in PUBLIC.glob("runs-*-result.json"))

    tmp = Path(tempfile.mkdtemp(prefix="qbl-e2e-mc-"))
    runs_dir = tmp / "runs"
    runs_dir.mkdir()
    montecarlo_dir = tmp / "montecarlo"
    strategy_dir = tmp / "strategies"
    strategy_dir.mkdir()
    shutil.copy(THRESHOLD, strategy_dir / "threshold_direction.py")
    shutil.copytree(FIXTURE, runs_dir / "run_0001")

    require_bridge_port_free()
    bridge = launch_bridge(
        [sys.executable, "-m", "src.bridge", "--port", "8765",
         "--runs", str(runs_dir), "--public", str(PUBLIC),
         "--strategy-dir", str(strategy_dir), "--data-dir", str(ROOT / "data"),
         "--studies", str(tmp / "studies"), "--walkforwards", str(tmp / "walkforwards"),
         "--montecarlo", str(montecarlo_dir)],
        cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    chrome = None
    try:
        ok &= check("bridge starts locally", health(), BRIDGE)
        chrome = launch_chrome()
        t = wait_debugger(chrome.debug_port)
        page = next(x for x in t if x.get("type") == "page")
        cdp = CDP(page["webSocketDebuggerUrl"])
        for dom in ("Page.enable", "Runtime.enable", "Log.enable", "Network.enable", "DOM.enable"):
            cdp.call(dom)
        cdp.call("Page.navigate", {"url": APP_URL})
        ok &= check("app opens on Demo", cdp.evaluate(f"({WAIT_BADGE_JS})('DEMO')"))
        ok &= check("bridge Connected", cdp.evaluate(f"({WAIT_TEXT_JS})(['.engine','Connected'])"))

        # 3-7. open the Monte Carlo workflow on run_0001
        open_library(cdp)
        open_tab(cdp, "Monte Carlo")
        ok &= check("Monte Carlo workflow opens", cdp.evaluate(f"({WAIT_SEL_JS})('[aria-label=\"Monte Carlo source run\"]')"))
        select_radix(cdp, "Monte Carlo source run", "run_0001")
        basis_text = cdp.evaluate(f"({TEXT_JS})('.mc-basis')") or ""
        ok &= check("price basis identified", "price terms" in basis_text.lower(), basis_text[:80])
        ok &= check("small-sample warning shown", cdp.evaluate(f"({WAIT_SEL_JS})('.mc-warning')"))

        # 8-13. shuffle study
        ok &= check("method set to Shuffle", choose_radix(cdp, "Monte Carlo method", "Shuffle"))
        set_input(cdp, '[aria-label="Monte Carlo simulations"]', "300")
        set_input(cdp, '[aria-label="Monte Carlo seed"]', "42")
        ok &= check("shuffle study completes", run_mc(cdp))
        sh_id, sh = mc_study(cdp, montecarlo_dir)
        ok &= check("study archive created", sh_id is not None and (montecarlo_dir / sh_id / "study.json").is_file(), str(sh_id))
        ok &= check("shuffle terminal PnL constant by construction",
                    sh["summary"]["terminal_pnl_constant"] is True
                    and sh["summary"]["terminal_pnl"]["min"] == sh["summary"]["terminal_pnl"]["max"] == 21.11)
        ok &= check("drawdown distribution renders", cdp.evaluate(f"({WAIT_SEL_JS})('.mc-hist svg')"))
        ok &= check("path envelope renders", cdp.evaluate(f"({WAIT_SEL_JS})('.mc-envelope svg')"))
        ok &= check("shuffle terminal-variation note shown",
                    "identical in every path" in (cdp.evaluate(f"({TEXT_JS})('.mc-note')") or ""))

        # 14. reopen from history (no rerun)
        ok &= check("study listed in history", cdp.evaluate(f"({WAIT_MC_HIST_JS})({json.dumps(sh_id)})"))
        cdp.evaluate(f"({CLICK_ARIA_JS})({json.dumps(f'Open Monte Carlo {sh_id}')})")
        ok &= check("history restores study without rerun", cdp.evaluate(WAIT_MC_JS, timeout=60))

        # 15-17. bootstrap study
        ok &= check("method set to Bootstrap", choose_radix(cdp, "Monte Carlo method", "Bootstrap"))
        set_input(cdp, '[aria-label="Monte Carlo seed"]', "42")
        ok &= check("bootstrap study completes", run_mc(cdp))
        bo_id, bo = mc_study(cdp, montecarlo_dir)
        prob = bo["summary"]["probability_terminal_negative"]
        ok &= check("bootstrap terminal distribution varies",
                    bo["summary"]["terminal_pnl_constant"] is False
                    and bo["summary"]["terminal_pnl"]["min"] != bo["summary"]["terminal_pnl"]["max"])
        ok &= check("negative-terminal probability matches study.json",
                    prob is not None and fmt(prob * 100) + "%" in (cdp.evaluate(f"({TEXT_JS})('.mc-prob')") or ""),
                    f"{prob}")

        # 18-21. account-ready run -> account basis
        close_library(cdp)
        ok &= check("real-run enabled", cdp.evaluate(WAIT_ENABLED_JS))
        select_radix(cdp, "Real run strategy", "Candle Direction")
        set_input(cdp, '[aria-label="Run start date"]', "2025-01-02")
        set_input(cdp, '[aria-label="Run end date"]', "2025-01-02")
        cdp.evaluate(f"({CLICK_ARIA_JS})('Strategy & Trade Settings')")
        cdp.evaluate(f"({WAIT_SEL_JS})('[aria-label=\"Stop loss pips\"]')")
        cdp.evaluate(f"({CLICK_ARIA_JS})('Enable account model')")
        cdp.evaluate(f"({WAIT_SEL_JS})('[aria-label=\"Account currency\"]')")
        set_input(cdp, '[aria-label="Account currency"]', "USD")
        set_input(cdp, '[aria-label="Account starting balance"]', "10000")
        set_input(cdp, '[aria-label="Account position size lots"]', "1")
        set_input(cdp, '[aria-label="Account commission per lot"]', "7")
        set_input(cdp, '[aria-label="Instrument contract size"]', "100")
        set_input(cdp, '[aria-label="Instrument quote currency"]', "USD")
        escape(cdp)
        time.sleep(0.4)
        prev = (re.search(r"Archived Run · (\S+) ·", cdp.evaluate(f"({TEXT_JS})('.run-status')") or "") or [None, None])[1]
        cdp.evaluate(CLICK_RUN_JS)
        ok &= check("account run created + opened", cdp.evaluate(f"({WAIT_NEW_RUN_JS})({json.dumps(prev)})", timeout=200))
        account_run = (re.search(r"Archived Run · (\S+) ·", cdp.evaluate(f"({TEXT_JS})('.run-status')") or "") or [None, None])[1]
        ok &= check("account workflow shows account metrics",
                    cdp.evaluate(f"({WAIT_METRICS_JS})('Net Account PnL')"))

        open_library(cdp)
        open_tab(cdp, "Monte Carlo")
        select_radix(cdp, "Monte Carlo source run", account_run)
        acct_basis = cdp.evaluate(f"({TEXT_JS})('.mc-basis')") or ""
        ok &= check("account basis identified", "Account" in acct_basis, acct_basis[:80])
        set_input(cdp, '[aria-label="Monte Carlo simulations"]', "300")
        set_input(cdp, '[aria-label="Monte Carlo seed"]', "7")
        ok &= check("account study completes", run_mc(cdp))
        ac_id, ac = mc_study(cdp, montecarlo_dir)
        ok &= check("account basis study archived", ac["basis"] == "account")
        ok &= check("realized-balance labels correct",
                    "Simulated Realized Balance" in ac["labels"]["path"]
                    and "USD" in ac["labels"]["path"])
        ok &= check("account drawdown % available", ac["summary"]["max_drawdown_pct"] is not None)
        ok &= check("account study shows realized balance label",
                    "Simulated Realized Balance" in (cdp.evaluate(f"({TEXT_JS})('.lib-panel')") or ""))
        ok &= check("unsupported interpretations not shown",
                    "risk of ruin" not in (cdp.evaluate(f"({TEXT_JS})('.lib-panel')") or "").lower())

        # 25-27. existing flows still work (account run + run_0001 both exist)
        open_tab(cdp, "Runs")
        ok &= check("account run listed", cdp.evaluate(f"({WAIT_LIB_JS})({json.dumps(account_run)})"))
        cdp.evaluate(f"({CLICK_ARIA_JS})({json.dumps(f'Select {account_run}')})")
        cdp.evaluate(f"({CLICK_ARIA_JS})('Select run_0001')")
        open_tab(cdp, "Compare")
        cdp.evaluate(f"({WAIT_SEL_JS})('[aria-label=\"Compare selected runs\"]')")
        cdp.evaluate(f"({CLICK_ARIA_JS})('Compare selected runs')")
        ok &= check("existing Run Comparison works", cdp.evaluate(f"({WAIT_CMP_ROWS_JS})(2)", timeout=70))
        open_tab(cdp, "Optimize")
        ok &= check("existing Optimization works", cdp.evaluate(f"({WAIT_SEL_JS})('[aria-label=\"Optimization strategy\"]')"))
        open_tab(cdp, "Walk-Forward")
        ok &= check("existing Walk-Forward works", cdp.evaluate(f"({WAIT_SEL_JS})('[aria-label=\"Walk-forward strategy\"]')"))
        close_library(cdp)

        # 23-24. delete the source run -> historical study marks SOURCE RUN MISSING
        status, _ = http_delete(f"{BRIDGE}/runs/{account_run}")
        ok &= check("source run deleted in isolated setup", status == 200, str(status))
        open_library(cdp)
        open_tab(cdp, "Monte Carlo")
        cdp.evaluate(f"({WAIT_MC_HIST_JS})({json.dumps(ac_id)})")
        cdp.evaluate(f"({CLICK_ARIA_JS})({json.dumps(f'Open Monte Carlo {ac_id}')})")
        ok &= check("historical study marks SOURCE RUN MISSING without crashing",
                    cdp.evaluate(f"({WAIT_MISSING_JS})({json.dumps(ac_id)})", timeout=40))
        ok &= check("missing-source study still shows cached analysis",
                    cdp.evaluate(f"({WAIT_SEL_JS})('.mc-envelope svg')"))
        close_library(cdp)

        # 29. run_0001 still works
        cdp.evaluate("(id=>{const b=[...document.querySelectorAll('.run-source button')].find(x=>x.textContent.includes(id)); if(b) b.click(); return !!b;})('run_0001')")
        ok &= check("run_0001 still works", cdp.evaluate(f"({WAIT_BADGE_JS})('ARCHIVED RUN')"))
        # 2. Demo still works
        cdp.evaluate(f"({CLICK_TEXT_JS})(['.run-source button','Demo'])")
        ok &= check("existing Demo works", cdp.evaluate(f"({WAIT_BADGE_JS})('DEMO')"))

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
    raise SystemExit(e2e_main(main))
