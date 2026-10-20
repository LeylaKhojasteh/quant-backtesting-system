"""Real browser E2E: import a local .py strategy from the UI and run it.

Starts the local bridge with a temporary strategy directory (seeded with a
trusted file strategy), drives Chrome over CDP, and verifies:

  bridge Connected -> existing strategies load -> Import .py Strategy (valid
  file) -> catalog refreshes without reload -> imported strategy auto-selected
  -> generic parameters appear -> configure -> Run Backtest -> archive +
  report + publish -> new run auto-loads and matches result.json -> strategy.py
  snapshot equals the imported source -> invalid/missing-interface/syntax/
  non-.py/traversal/duplicate are rejected -> no permanent/temp garbage ->
  Demo and run_0001 still work.

The repo's frontend public artifacts are snapshotted and restored afterwards.
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
    CDP,
    TEXT_JS,
    WAIT_BADGE_JS,
    WAIT_SEL_JS,
    WAIT_TEXT_JS,
    launch_chrome,
    wait_debugger,
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

APP_URL = "http://127.0.0.1:5173/?fixture=demo"
BRIDGE_PORT = 8765
BRIDGE_URL = f"http://127.0.0.1:{BRIDGE_PORT}"
PUBLIC = ROOT / "Quant-Backtest-Lab" / "public"
MANIFEST = PUBLIC / "adapters-run-manifest.json"
IMPORT_FIXTURES = ROOT / "tests" / "fixtures" / "import"
EMA_PATH = IMPORT_FIXTURES / "ema_cross.py"
EMA_SOURCE = EMA_PATH.read_text(encoding="utf-8")
DEMO_ENTRY = "2,799.84"
RUN0001_ENTRY = "2,626.08"

_POLL = "const t0=Date.now();const iv=setInterval(()=>{if(ok()||Date.now()-t0>MAX){clearInterval(iv);res(ok());}},250);"
WAIT_DONE_JS = ("(() => new Promise(res => {"
                " const ok=()=>{const n=document.querySelector('.rr-notice');const b=document.querySelector('.demo-badge');"
                "  return !!n && !!b && b.textContent.trim()==='ARCHIVED RUN';};"
                " if(ok())return res(true);" + _POLL.replace("MAX", "150000") + "}))()")
WAIT_ENABLED_JS = ("(() => new Promise(res => {"
                   " const ok=()=>{const b=[...document.querySelectorAll('.real-run button')].find(x=>x.textContent.includes('Run Backtest'));return !!b && !b.disabled;};"
                   " if(ok())return res(true);" + _POLL.replace("MAX", "30000") + "}))()")
WAIT_ROW_JS = ("(([text,notText]) => new Promise(res => {"
               " const ok=()=>{const r=document.querySelector('.overview-trade-log tbody tr');"
               "  return !!r && r.textContent.includes(text) && (!notText || !r.textContent.includes(notText));};"
               " if(ok())return res(true);" + _POLL.replace("MAX", "25000") + "}))")
INSTALL_OBSERVER_JS = """(() => {
  window.__sawRunning = false;
  const scan = () => {
    if ([...document.querySelectorAll('.real-run button')].some(b => b.textContent.includes('Running'))) window.__sawRunning = true;
  };
  scan();
  if (window.__runMo) window.__runMo.disconnect();
  const mo = new MutationObserver(scan);
  mo.observe(document.documentElement, { subtree: true, childList: true, characterData: true, attributes: true });
  window.__runMo = mo;
  return 'installed';
})()"""

SET_INPUT_JS = """(([sel, value]) => {
  const el = document.querySelector(sel);
  if (!el) return 'NO_EL:' + sel;
  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
  setter.call(el, value);
  el.dispatchEvent(new Event('input', { bubbles: true }));
  el.dispatchEvent(new Event('change', { bubbles: true }));
  return el.value;
})"""
CLICK_RUN_JS = """(() => {
  const b = [...document.querySelectorAll('.real-run button')].find(x => x.textContent.includes('Run Backtest') || x.textContent.includes('Running'));
  if (!b) return 'NO_BTN';
  b.click(); return 'CLICKED';
})()"""
CLICK_SOURCE_JS = """((needle) => {
  const b = [...document.querySelectorAll('.run-source button')].find(x => x.textContent.includes(needle));
  if (!b) return 'NOT_FOUND';
  b.click(); return 'CLICKED';
})"""


def check(name: str, cond, detail: str = "") -> bool:
    print(("PASS " if cond else "FAIL ") + name + (f" — {detail}" if detail else ""))
    return bool(cond)


def fmt(n: float) -> str:
    return f"{n:,.2f}"


def bridge_health(timeout: int = 30) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        try:
            with urllib.request.urlopen(f"{BRIDGE_URL}/health", timeout=3) as r:
                return json.loads(r.read())["status"] == "ok"
        except Exception:
            time.sleep(0.4)
    return False


def set_input(cdp: CDP, sel: str, value: str) -> None:
    cdp.evaluate(f"({SET_INPUT_JS})([{json.dumps(sel)},{json.dumps(value)}])")


def import_settled(cdp: CDP, prev_error: str, timeout: float = 45.0) -> dict:
    """Poll until a new import result (ok or changed error) appears."""
    end = time.time() + timeout
    while time.time() < end:
        state = cdp.evaluate(
            "(() => { const o=document.querySelector('.rr-import-ok'); "
            "const e=document.querySelector('.rr-import .rr-error'); "
            "return { ok: o?o.textContent:'', err: e?e.textContent:'' }; })()"
        )
        if state.get("ok"):
            return {"kind": "ok", "text": state["ok"]}
        if state.get("err") and state["err"] != prev_error:
            return {"kind": "error", "text": state["err"]}
        time.sleep(0.5)
    return {"kind": "timeout", "text": ""}


def current_error(cdp: CDP) -> str:
    return cdp.evaluate("document.querySelector('.rr-import .rr-error')?.textContent || ''")


def escape(cdp):
    cdp.call("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Escape", "code": "Escape", "windowsVirtualKeyCode": 27})
    cdp.call("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Escape", "code": "Escape", "windowsVirtualKeyCode": 27})


def click_aria(cdp, label):
    return cdp.evaluate(
        "(label => { const el=document.querySelector('[aria-label=\"'+label.replace(/\"/g,'\\\\\"')+'\"]');"
        " if(!el) return 'NO'; for (const t of ['pointerdown','mousedown','mouseup','click']) el.dispatchEvent(new MouseEvent(t,{bubbles:true,cancelable:true,view:window})); return 'OK'; })"
        f"({json.dumps(label)})")


def pick_file(cdp: CDP, path: Path) -> dict:
    """Set the hidden file input and wait for the import to settle."""
    prev = current_error(cdp)
    doc = cdp.call("DOM.getDocument", {"depth": -1})
    node = cdp.call("DOM.querySelector", {"nodeId": doc["root"]["nodeId"], "selector": "input[type=file]"})
    if not node.get("nodeId"):
        return {"kind": "no_input", "text": ""}
    cdp.call("DOM.setFileInputFiles", {"files": [str(path)], "nodeId": node["nodeId"]})
    result = import_settled(cdp, prev)
    if result["kind"] == "timeout":
        # Some environments do not fire change from setFileInputFiles.
        cdp.evaluate("(() => { const el=document.querySelector('input[type=file]'); if(el) el.dispatchEvent(new Event('change',{bubbles:true})); return 'ok'; })()")
        result = import_settled(cdp, prev)
    return result


def main() -> int:
    ok = True
    manifest_before = MANIFEST.read_text(encoding="utf-8") if MANIFEST.is_file() else None
    files_before = set(p.name for p in PUBLIC.glob("runs-*-result.json"))

    tmp = Path(tempfile.mkdtemp(prefix="qbl-e2e-import-"))
    strategy_dir = tmp / "strategies"
    strategy_dir.mkdir()
    # Seed a trusted file strategy so the catalog has both builtin + file sources.
    shutil.copy(ROOT / "tests" / "fixtures" / "strategies" / "threshold_direction.py", strategy_dir / "threshold_direction.py")
    runs_dir = tmp / "runs"
    bad_txt = tmp / "notes.txt"
    bad_txt.write_text("not a strategy\n", encoding="utf-8")

    require_bridge_port_free()
    bridge = launch_bridge(
        [sys.executable, "-m", "src.bridge", "--port", str(BRIDGE_PORT),
         "--runs", str(runs_dir), "--public", str(PUBLIC),
         "--strategy-dir", str(strategy_dir), "--data-dir", "data"],
        cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    chrome = None
    try:
        ok &= check("bridge starts locally", bridge_health(), BRIDGE_URL)

        chrome = launch_chrome()
        targets = wait_debugger(chrome.debug_port)
        page = next(t for t in targets if t.get("type") == "page")
        cdp = CDP(page["webSocketDebuggerUrl"])
        for dom in ("Page.enable", "Runtime.enable", "Log.enable", "Network.enable", "DOM.enable"):
            cdp.call(dom)
        cdp.call("Page.navigate", {"url": APP_URL})

        # ---- existing state ---------------------------------------------------
        ok &= check("app loads Demo", cdp.evaluate(f"({WAIT_BADGE_JS})('DEMO')"), "DEMO")
        ok &= check("bridge Connected", cdp.evaluate(f"({WAIT_TEXT_JS})(['.engine','Connected'])"))
        ok &= check("existing strategies load", cdp.evaluate(
            f"({WAIT_TEXT_JS})(['[aria-label=\"Real run strategy\"]','Candle Direction'])"),
            cdp.evaluate(f"({TEXT_JS})('[aria-label=\"Real run strategy\"]')"))
        catalog_before = cdp.evaluate(
            "(async () => (await (await fetch('http://127.0.0.1:8765/strategies')).json())"
            ".strategies.map(s => s.name))()", timeout=30) or []
        ok &= check("trusted file strategy also in catalog", "Threshold Direction" in catalog_before,
                    str(catalog_before))
        # dataset catalog must be ready before a run
        cdp.evaluate(f"({WAIT_TEXT_JS})(['[aria-label=\"Real run dataset\"]','XAUUSD'])")

        # ---- import a valid strategy -----------------------------------------
        files_before_dir = sorted(p.name for p in strategy_dir.iterdir())
        r = pick_file(cdp, EMA_PATH)
        ok &= check("valid import succeeds", r["kind"] == "ok", r["text"])
        ok &= check("imported file stored in approved dir", (strategy_dir / "ema_cross.py").is_file())
        ok &= check("no extra files created", sorted(p.name for p in strategy_dir.iterdir()) == sorted(files_before_dir + ["ema_cross.py"]))
        ok &= check("catalog refreshed (no reload): imported selected", cdp.evaluate(
            f"({WAIT_TEXT_JS})(['[aria-label=\"Real run strategy\"]','EMA Cross'])"),
            cdp.evaluate(f"({TEXT_JS})('[aria-label=\"Real run strategy\"]')"))
        click_aria(cdp, "Strategy & Trade Settings")
        cdp.evaluate(f"({WAIT_SEL_JS})('[aria-label=\"Strategy parameter fast_period\"]')")
        ok &= check("generic parameters appear", cdp.evaluate(
            "(() => { const f=document.querySelector('[aria-label=\"Strategy parameter fast_period\"]');"
            " const s=document.querySelector('[aria-label=\"Strategy parameter slow_period\"]');"
            " return !!f && !!s && f.value==='3' && s.value==='8'; })()"),
            cdp.evaluate("document.querySelector('[aria-label=\"Strategy parameter fast_period\"]')?.value"))

        # ---- invalid imports are rejected, no garbage ------------------------
        r = pick_file(cdp, IMPORT_FIXTURES / "syntax_error.py")
        ok &= check("syntax error rejected", r["kind"] == "error" and "invalid" in r["text"].lower(), r["text"][:80])
        r = pick_file(cdp, ROOT / "tests" / "fixtures" / "strategies" / "not_a_strategy.py")
        ok &= check("missing interface rejected", r["kind"] == "error", r["text"][:80])
        r = pick_file(cdp, bad_txt)
        ok &= check("non-.py rejected", r["kind"] == "error" and ".py" in r["text"], r["text"][:80])
        r = pick_file(cdp, EMA_PATH)
        ok &= check("duplicate filename conflict", r["kind"] == "error" and "exist" in r["text"].lower(), r["text"][:80])
        ok &= check("duplicate did not overwrite", (strategy_dir / "ema_cross.py").read_text(encoding="utf-8") == EMA_SOURCE)
        ok &= check("no garbage after failed imports", sorted(p.name for p in strategy_dir.iterdir()) == sorted(files_before_dir + ["ema_cross.py"]),
                    str(sorted(p.name for p in strategy_dir.iterdir())))

        # path traversal via the bridge is rejected (cannot come from a real file pick)
        trav = cdp.evaluate(
            "(async () => { const r = await fetch('http://127.0.0.1:8765/strategies/import',"
            " {method:'POST',headers:{'Content-Type':'application/json'},"
            "  body:JSON.stringify({filename:'../evil.py',source:'x = 1\\n'})});"
            " const j = await r.json(); return {status:r.status, code:j.error&&j.error.code}; })()",
            timeout=30)
        ok &= check("path traversal rejected", trav.get("status") == 400 and trav.get("code") == "invalid_strategy_file", str(trav))

        # ---- run the imported strategy (existing pipeline) -------------------
        # Re-select the imported strategy (invalid attempts left it selected already).
        ok &= check("imported strategy still selected", cdp.evaluate(
            f"({WAIT_TEXT_JS})(['[aria-label=\"Real run strategy\"]','EMA Cross'])"))
        set_input(cdp, '[aria-label="Strategy parameter fast_period"]', "2")
        set_input(cdp, '[aria-label="Strategy parameter slow_period"]', "5")
        set_input(cdp, '[aria-label="Run start date"]', "2025-01-02")
        set_input(cdp, '[aria-label="Run end date"]', "2025-01-02")
        escape(cdp)
        time.sleep(0.4)
        runs_before = cdp.evaluate("document.querySelectorAll('.run-source button').length")
        ok &= check("Run enabled", cdp.evaluate(WAIT_ENABLED_JS))
        cdp.evaluate(INSTALL_OBSERVER_JS)
        cdp.evaluate(CLICK_RUN_JS)
        cdp.evaluate("(() => { const b=[...document.querySelectorAll('.real-run button')].find(x=>x.textContent.includes('Running')||x.textContent.includes('Run Backtest')); if(b){b.click();b.click();} return 'ok'; })()")
        ok &= check("imported run completes + auto-loads", cdp.evaluate(WAIT_DONE_JS, timeout=180),
                    cdp.evaluate(f"({TEXT_JS})('.rr-notice')"))
        ok &= check("Running state appeared", cdp.evaluate("!!window.__sawRunning"))
        runs_after = cdp.evaluate("document.querySelectorAll('.run-source button').length")
        ok &= check("exactly one new run (duplicate clicks ignored)", runs_after == runs_before + 1, f"{runs_before} -> {runs_after}")

        status_text = cdp.evaluate(f"({TEXT_JS})('.run-status')") or ""
        m = re.search(r"Archived Run · (\S+) ·", status_text)
        new_run = m.group(1) if m else None
        ok &= check("UI switched to new run", bool(new_run), status_text[:120])

        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        entry = next((r for r in manifest["runs"] if r["runId"] == new_run), None)
        published = PUBLIC / f"runs-{new_run}-result.json" if new_run else None
        ok &= check("manifest lists new run", entry is not None)
        archive_dir = runs_dir / new_run if new_run else None
        ok &= check("strategy.py snapshot matches imported source",
                    bool(archive_dir) and (archive_dir / "strategy.py").read_text(encoding="utf-8") == EMA_SOURCE)
        ok &= check("report.html created", bool(archive_dir) and (archive_dir / "report.html").is_file())
        ok &= check("published result present", bool(published) and published.is_file(), str(published))

        payload = json.loads(published.read_text(encoding="utf-8")) if published and published.is_file() else {}
        trades = payload.get("trades", [])
        metrics = payload.get("metrics", {})
        first_entry = fmt(trades[0]["entry_price"]) if trades else ""
        ok &= check("archive strategy identity", payload.get("strategy", {}).get("name") == "EMA Cross", str(payload.get("strategy")))
        ok &= check("archive effective parameters", payload.get("config", {}).get("parameters") == {"fast_period": 2, "slow_period": 5},
                    str(payload.get("config", {}).get("parameters")))
        ok &= check("auto-loaded run rendered", cdp.evaluate(f"({WAIT_ROW_JS})(['{first_entry}','{DEMO_ENTRY}'])") if first_entry else False, first_entry)
        trade_rows = cdp.evaluate("document.querySelectorAll('.overview-trade-log tbody tr').length")
        ok &= check("trade count matches archive", trade_rows == len(trades), f"dom={trade_rows} archive={len(trades)}")
        ok &= check("first trade matches archive", first_entry in (cdp.evaluate("(() => { const r=document.querySelector('.overview-trade-log tbody tr'); return r?r.textContent.replace(/\\s+/g,' '):''; })()") or ""))

        # ---- existing flows still work ---------------------------------------
        ok &= check("run_0001 still loads", cdp.evaluate(f"({CLICK_SOURCE_JS})('run_0001')") == "CLICKED")
        ok &= check("run_0001 data restored", cdp.evaluate(f"({WAIT_ROW_JS})(['{RUN0001_ENTRY}',''])") )
        ok &= check("switch to Demo", cdp.evaluate(f"({CLICK_SOURCE_JS})('Demo')") == "CLICKED")
        ok &= check("Demo data restored", cdp.evaluate(f"({WAIT_ROW_JS})(['{DEMO_ENTRY}',''])") )

        errs = [e for e in cdp.console_errors
                if "favicon" not in e.lower()
                and "status of 400" not in e.lower() and "status of 409" not in e.lower()]
        ok &= check("no unexpected console errors", len(errs) == 0, "; ".join(errs[:2]) if errs else "clean")
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
