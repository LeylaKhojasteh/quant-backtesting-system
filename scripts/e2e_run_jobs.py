"""v1.0.1 run-job E2E: live activity, heartbeat, stop, reload continuity.

Scenarios (milestone §16):
  A. Normal run: activity bar, stage updates, elapsed moves, heartbeat, auto-load.
  B. Reload during run: same job reconnects, no duplicate, heartbeat continues,
     result auto-loads on completion.
  C. Stop during engine: worker stops, bridge alive, previous result visible,
     stopped run absent from library/manifest/archives.
  D. Reload after success: newest successful run auto-loads (not run_0001).
  E. Terminology: price-term title "Cumulative Trade PnL", Account Off without
     account-model-missing warnings.

Runs the real bridge in a temp sandbox with a SLOW strategy so stages are
observable; uses headless Chrome via CDP like the other E2E scripts.
"""

import json
import os
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
urllib.request.install_opener(urllib.request.build_opener(urllib.request.ProxyHandler({})))

APP = os.environ.get("QBL_APP_URL", "http://127.0.0.1:5173/")
BRIDGE = "http://127.0.0.1:8765"
PUBLIC = ROOT / "Quant-Backtest-Lab" / "public"
MANIFEST = PUBLIC / "adapters-run-manifest.json"

SLOW_STRATEGY = """import time
NAME = 'Slow Signal'
VERSION = '1.0'
DESCRIPTION = 'Intentionally slow strategy so run-job stages are observable.'
PARAMETERS = {}
def generate_signals(candles, parameters=None):
    import pandas as pd
    time.sleep(20)
    idx = list(candles.index)[:1]
    rows = [{'signal_candle_start': i, 'signal_available_time': i, 'direction': 'LONG'} for i in idx]
    return pd.DataFrame(rows, columns=['signal_candle_start', 'signal_available_time', 'direction'])
"""

_POL = "const t0=Date.now();const iv=setInterval(()=>{if(ok()||Date.now()-t0>MAX){clearInterval(iv);res(ok());}},300);"
WAIT_ACTIVITY = ("(() => new Promise(res => { const ok=()=>!!document.querySelector('.run-activity');"
                 " if(ok())return res(true);" + _POL.replace("MAX", "30000") + "}))()")
WAIT_ENGINE_STAGE = ("(() => new Promise(res => { const ok=()=>{const a=document.querySelector('.run-activity');"
                     " return !!a && /Engine execution|Generating|Loading|Building|Writing|Report|Publishing/i.test(a.textContent||'')};"
                     " if(ok())return res(true);" + _POL.replace("MAX", "90000") + "}))()")
WAIT_COMPLETED = ("(() => new Promise(res => { const ok=()=>{const a=document.querySelector('.run-activity');"
                  " return !!a && /Backtest completed/i.test(a.textContent||'')};"
                  " if(ok())return res(true);" + _POL.replace("MAX", "180000") + "}))()")
WAIT_HEARTBEAT = ("(() => new Promise(res => { const ok=()=>{const a=document.querySelector('.run-activity');"
                  " return !!a && (/Running|Starting/i.test(a.textContent||''))};"
                  " if(ok())return res(true);" + _POL.replace("MAX", "60000") + "}))()")
CLICK_RUN = """(() => { const b=[...document.querySelectorAll('.real-run button.primary')]
  .find(x=>x.textContent.includes('Run Backtest')||x.textContent.includes('Backtest running'));
  if(!b) return 'NO'; b.click(); return 'CLICKED'; })()"""
CLICK_STOP = """(() => { const b=[...document.querySelectorAll('.run-activity .activity-stop')]
  .find(x=>!x.disabled); if(!b) return 'NO'; b.click(); return 'CLICKED'; })()"""
CONFIRM_STOP = """(() => { const d=[...document.querySelectorAll('button')]
  .find(x=>x.textContent.trim()==='Stop Backtest' && x.className.includes('danger'));
  if(!d) return 'NO_DLG'; d.click(); return 'CONFIRMED'; })()"""
SET_INPUT = """(([sel, value]) => { const el=document.querySelector(sel); if(!el) return 'NO_EL';
  const s=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set;
  s.call(el,value); el.dispatchEvent(new Event('input',{bubbles:true}));
  el.dispatchEvent(new Event('change',{bubbles:true})); return el.value; })"""
SELECT_STRATEGY = """((name) => { const b=document.querySelector('button[aria-label="Real run strategy"]');
  if(!b) return 'NO_BTN'; b.click(); return 'OPENED'; })()"""


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


def job_api(path, method="GET", payload=None):
    body = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(BRIDGE + path, data=body, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


import urllib.error  # noqa: E402  (after helpers; stdlib only)


def main() -> int:
    ok = True
    manifest_before = MANIFEST.read_text(encoding="utf-8") if MANIFEST.is_file() else None
    files_before = set(p.name for p in PUBLIC.glob("runs-*-result.json"))
    tmp = Path(tempfile.mkdtemp(prefix="qbl-e2e-jobs-"))
    runs = tmp / "runs"
    # The app advertises run_0001 from the repo manifest; the bridge must own
    # that archive too so run-scoped market context resolves.
    runs.mkdir(parents=True, exist_ok=True)
    seed = ROOT / "runs" / "run_0001"
    if seed.is_dir():
        shutil.copytree(seed, runs / "run_0001")
    strat = tmp / "strat"
    strat.mkdir(parents=True)
    (strat / "slow_signal.py").write_text(SLOW_STRATEGY, encoding="utf-8")
    shutil.copy(ROOT / "tests" / "fixtures" / "strategies" / "threshold_direction.py",
                strat / "threshold_direction.py")
    bridge = subprocess.Popen(
        [sys.executable, "-m", "src.bridge", "--port", "8765", "--runs", str(runs),
         "--public", str(PUBLIC), "--data-dir", str(ROOT / "data"),
         "--strategy-dir", str(strat)],
        cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    chrome = None
    try:
        ok &= check("bridge starts", health(), BRIDGE)
        chrome = launch_chrome(9412)
        t = wait_debugger(9412)
        page = next(x for x in t if x.get("type") == "page")
        cdp = CDP(page["webSocketDebuggerUrl"])
        for dom in ("Page.enable", "Runtime.enable", "Log.enable", "Network.enable"):
            cdp.call(dom)
        cdp.call("Page.navigate", {"url": APP})
        ok &= check("app hydrates", cdp.evaluate(f"({WAIT_SEL_JS})('.run-source button')"))
        ok &= check("bridge connected", cdp.evaluate(f"({WAIT_TEXT_JS})(['.engine','Connected'])"))

        # ---- A: normal run (fast strategy default) --------------------------
        cdp.evaluate(f"({SET_INPUT})(['[aria-label=\"Run start date\"]','2025-01-02'])")
        cdp.evaluate(f"({SET_INPUT})(['[aria-label=\"Run end date\"]','2025-01-02'])")
        time.sleep(0.5)
        run_id_a = (cdp.evaluate(f"({TEXT_JS})('.run-status')") or "")
        cdp.evaluate(CLICK_RUN)
        ok &= check("A: activity bar appears", cdp.evaluate(WAIT_ACTIVITY),
                    cdp.evaluate(f"({TEXT_JS})('.run-activity')"))
        ok &= check("A: heartbeat Running/Starting", cdp.evaluate(WAIT_HEARTBEAT),
                    cdp.evaluate(f"({TEXT_JS})('.run-activity')"))
        ok &= check("A: stage updates (no fake %/ETA)",
                    cdp.evaluate(WAIT_ENGINE_STAGE),
                    cdp.evaluate(f"({TEXT_JS})('.run-activity')"))
        bar = cdp.evaluate(f"({TEXT_JS})('.run-activity')") or ""
        ok &= check("A: no fake progress text", "%" not in bar and "ETA" not in bar, bar[:120])
        # Run button disabled while active (duplicate protection in UI).
        ok &= check("A: Run Backtest disabled while active",
                    cdp.evaluate("(()=>{const b=[...document.querySelectorAll('.real-run button.primary')]"
                                 ".find(x=>/Run Backtest|Backtest running/.test(x.textContent));"
                                 "return !!b && b.disabled})()"))
        # Duplicate POST rejected at the API (409, same job keeps running).
        dup_payload = {"strategy": {"selector": "threshold_direction", "parameters": {}},
                       "market": {"symbol": "XAUUSD", "timeframe": "15min",
                                  "start": "2025-01-02", "end": "2025-01-02",
                                  "dataset_id": "xauusd_dukas_ticks"},
                       "execution": {"stop_distance": 5, "take_profit_distance": 10}}
        code, dup = job_api("/run-jobs", "POST", dup_payload)
        ok &= check("A: duplicate POST rejected (409)", code == 409
                    and dup.get("error", {}).get("code") == "run_in_progress", str(code))
        ok &= check("A: completion auto-loads new result", cdp.evaluate(WAIT_COMPLETED, timeout=200),
                    cdp.evaluate(f"({TEXT_JS})('.run-activity')"))
        m = __import__("re").search(r"Archived Run · (\S+) ·",
                                    cdp.evaluate(f"({TEXT_JS})('.run-status')") or "")
        run_a = m.group(1) if m else None
        ok &= check("A: new run archived + rendered", bool(run_a), run_id_a[:80])
        # v1.0.2 QA fix: a terminal job must not be shown as an ACTIVE tracked job.
        sidebar = cdp.evaluate(f"({TEXT_JS})('.real-run')") or ""
        ok &= check("A: sidebar no longer shows a tracked active job",
                    "Tracking " not in sidebar, sidebar[:120])
        status, actv = job_api("/run-jobs/active")
        ok &= check("A: /run-jobs/active is null after completion",
                    status == 200 and (actv.get("job") is None), str(actv.get("job"))[:80])

        # ---- B: reload during run -> reconnect same job ---------------------
        # Start the slow job via the API (the worker runs independently of
        # the tab); the reloaded page must reconnect to THIS job id.
        code, created = job_api("/run-jobs", "POST", {
            "strategy": {"selector": str(strat / "slow_signal.py"), "parameters": {}},
            "market": {"symbol": "XAUUSD", "timeframe": "15min",
                       "start": "2025-01-02", "end": "2025-01-02",
                       "dataset_id": "xauusd_dukas_ticks"},
            "execution": {"stop_distance": 5, "take_profit_distance": 10},
            "publish": True})
        ok &= check("B: slow job started via API", code == 201, str(code))
        job_b = (created.get("job") or {}).get("job_id") if code == 201 else None
        # Wait for a real stage, then reload the browser tab.
        time.sleep(4)
        cdp.call("Page.navigate", {"url": APP})
        ok &= check("B: app rehydrates after reload",
                    cdp.evaluate(f"({WAIT_SEL_JS})('.run-source button')"))
        ok &= check("B: same job reconnected (activity bar, no new job)",
                    cdp.evaluate(WAIT_ACTIVITY),
                    cdp.evaluate(f"({TEXT_JS})('.run-activity')"))
        code, active2 = job_api("/run-jobs/active")
        ok &= check("B: still exactly one active job, same id",
                    code == 200 and (active2.get("job") or {}).get("job_id") == job_b,
                    str((active2.get("job") or {}).get("job_id")))
        ok &= check("B: heartbeat continues after reload", cdp.evaluate(WAIT_HEARTBEAT),
                    cdp.evaluate(f"({TEXT_JS})('.run-activity')"))
        ok &= check("B: reloaded run completes + auto-loads", cdp.evaluate(WAIT_COMPLETED, timeout=240),
                    cdp.evaluate(f"({TEXT_JS})('.run-activity')"))
        m = __import__("re").search(r"Archived Run · (\S+) ·",
                                    cdp.evaluate(f"({TEXT_JS})('.run-status')") or "")
        run_b = m.group(1) if m else None
        ok &= check("B: reloaded completion rendered new run", bool(run_b)
                    and run_b not in (run_a, None), str(run_b))

        # ---- C: stop during engine ------------------------------------------
        code, created = job_api("/run-jobs", "POST", {
            "strategy": {"selector": str(strat / "slow_signal.py"), "parameters": {}},
            "market": {"symbol": "XAUUSD", "timeframe": "15min",
                       "start": "2025-01-02", "end": "2025-01-02",
                       "dataset_id": "xauusd_dukas_ticks"},
            "execution": {"stop_distance": 5, "take_profit_distance": 10},
            "publish": True})
        ok &= check("C: slow job started", code == 201, str(code))
        job_c = created["job"]["job_id"]
        run_c = created["job"]["run_id"]
        time.sleep(6)  # let it reach signal/engine stages
        code, mid = job_api(f"/run-jobs/{job_c}")
        ok &= check("C: job running before stop",
                    (mid.get("job") or {}).get("state") in ("queued", "running"),
                    str((mid.get("job") or {}).get("state")))
        # Stop via the UI when the activity bar offers it, else via the API:
        # either way the worker must die and the UI must converge to stopped.
        clicked = cdp.evaluate(CLICK_STOP)
        if clicked == "CLICKED":
            time.sleep(0.5)
            ok &= check("C: confirmation text",
                        cdp.evaluate("(()=>{const d=document.body.textContent||'';"
                                     "return d.includes('Stop this backtest?')&&"
                                     "d.includes('Market data and cache files will not be affected')})()"))
            ok &= check("C: confirmed", cdp.evaluate(CONFIRM_STOP) == "CONFIRMED")
        else:
            print("INFO stop button not clickable from CDP — stopping via API")
            code, payload = job_api(f"/run-jobs/{job_c}/stop", "POST", {})
            ok &= check("C: stop accepted", code == 200
                        and (payload.get("job") or {}).get("state") in ("stopping", "stopped"),
                        str(code))
        stopped = None
        for _ in range(40):
            _, cur = job_api(f"/run-jobs/{job_c}")
            stopped = (cur.get("job") or {})
            if stopped.get("state") in ("stopped", "failed", "completed"):
                break
            time.sleep(1)
        ok &= check("C: job stopped (not completed)", stopped.get("state") == "stopped",
                    str(stopped.get("state")))
        # The tab's 1 Hz poll must converge the activity bar to the stopped
        # state without a manual reload.
        ok &= check("C: UI shows stopped",
                    cdp.evaluate(f"({WAIT_TEXT_JS})(['.run-activity','Backtest stopped'])"),
                    cdp.evaluate(f"({TEXT_JS})('.run-activity')"))
        ok &= check("C: bridge survives stop", health(timeout=10))
        # Previous result still visible; stopped run absent everywhere.
        status = cdp.evaluate(f"({TEXT_JS})('.run-status')") or ""
        ok &= check("C: previous result stays visible", run_b in status, status[:120])
        lib_runs = [r["run_id"] for r in job_api("/runs")[1].get("runs", [])]
        ok &= check("C: stopped run absent from library",
                    run_c is None or run_c not in lib_runs, str(run_c))
        # run_c may be None when the stop lands before the worker writes its
        # status (queued): then no run id was ever allocated — assert on the
        # stopped job's own record instead of a phantom directory.
        stopped_run = stopped.get("run_id") or run_c
        if stopped_run:
            ok &= check("C: no partial archive dir", not (runs / stopped_run).exists())
            ok &= check("C: no partial public artifact",
                        not list(PUBLIC.glob(f"runs-{stopped_run}-result.json")))
        else:
            ok &= check("C: no run id allocated before stop", True, "queued stop")
            ok &= check("C: no stray run dirs", sorted(
                p.name for p in runs.iterdir()
                if p.is_dir() and p.name != "jobs") == sorted(
                r for r in lib_runs), "only library runs on disk")
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        ok &= check("C: manifest untouched by stop",
                    stopped_run is None
                    or all(r.get("runId") != stopped_run for r in manifest.get("runs", [])))

        # ---- D: reload after success -> newest run auto-loads ----------------
        cdp.call("Page.navigate", {"url": APP})
        ok &= check("D: app rehydrates", cdp.evaluate(f"({WAIT_SEL_JS})('.run-source button')"))
        ok &= check("D: newest successful run auto-loads (not run_0001)",
                    cdp.evaluate(f"({WAIT_BADGE_JS})('ARCHIVED RUN')")
                    and run_b in (cdp.evaluate(f"({TEXT_JS})('.run-status')") or ""),
                    cdp.evaluate(f"({TEXT_JS})('.run-status')"))

        # ---- E: terminology --------------------------------------------------
        ok &= check("E: price-term title is Cumulative Trade PnL",
                    cdp.evaluate("!![...document.querySelectorAll('.panel h2,.panel-title,.panel-header')]"
                                 ".find(e=>/Cumulative Trade PnL/.test(e.textContent||''))"))
        ok &= check("E: no Equity Curve title",
                    cdp.evaluate("!(document.body.textContent||'').includes('Equity Curve')"))
        readiness = cdp.evaluate(f"({TEXT_JS})('.readiness-bar')") or ""
        ok &= check("E: Account Off, run Validated", "Account" in readiness and "Off" in readiness
                    and "Validated" in readiness, readiness[:200])
        ok &= check("E: no account-model-missing warning",
                    "Account model missing" not in (cdp.evaluate("(document.body.textContent||'')") or ""))

        errs = [e for e in cdp.console_errors if "favicon" not in e.lower()]
        hydra = [e for e in cdp.console_errors if "hydrat" in e.lower()]
        ok &= check("no console errors", len(errs) == 0, "; ".join(errs[:2]) if errs else "clean")
        ok &= check("no hydration errors", len(hydra) == 0)
        cdp.ws.close()
    finally:
        if chrome is not None:
            chrome.terminate()
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
