"""v1.0.2 visualization E2E — REAL market data, truthfulness, no demo leakage.

Runs against a live bridge + dev server (defaults: bridge :8765, app :12136).

Part 1 (API): temp archived runs at 15min and 5min resolve real candle/tick
windows from the canonical cache, with the run's own symbol/timeframe.

Part 2 (UI): load a real archived run and assert the product shows real market
context — recorded network calls to /runs/<id>/candles and /runs/<id>/ticks, a
populated price chart, real run numbers, and NONE of the removed demo artifacts
(fake check count, seeded visualization copy, Equity Curve title, demo prices).

Args:
  --url     app URL (default http://127.0.0.1:12136/)
  --bridge  bridge base URL (default http://127.0.0.1:8765)
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.e2e_run_switch import CDP, TEXT_JS, WAIT_SEL_JS, WAIT_TEXT_JS, launch_chrome, wait_debugger  # noqa: E402

os.environ.setdefault("no_proxy", "127.0.0.1,localhost")
os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")
urllib.request.install_opener(urllib.request.build_opener(urllib.request.ProxyHandler({})))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

FAILS: list[str] = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f" — {detail}" if detail else ""))
    if not cond:
        FAILS.append(name)
    return bool(cond)


def api(base, path):
    try:
        with urllib.request.urlopen(base + path, timeout=60) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raw = e.read()
        return e.code, (json.loads(raw) if raw else {})


# --------------------------------------------------------------------------
# Part 1 — run-scoped candle/tick API across two timeframes
# --------------------------------------------------------------------------

def part1(bridge: str, runs_dir: Path, cleanup_dirs: list) -> None:
    status, body = api(bridge, "/runs/run_0001/candles?limit=60")
    check("15min candles: 200 + real rows", status == 200 and body.get("count", 0) > 0,
          f"{status} count={body.get('count')}")
    check("15min candles: symbol/timeframe from run",
          body.get("symbol") == "XAUUSD" and body.get("timeframe") == "15min",
          f"{body.get('symbol')} {body.get('timeframe')}")
    check("15min candles: BID+ASK OHLC present",
          all(k in (body.get("candles") or [{}])[0]
              for k in ("bid_open", "bid_high", "bid_low", "bid_close", "ask_open", "ask_close")),
          str(list((body.get("candles") or [{}])[0])[:9]))

    # A second timeframe: a temp run referencing the same dataset at 5min.
    src = runs_dir / "run_0001" / "result.json"
    payload = json.loads(src.read_text(encoding="utf-8"))
    payload["run_id"] = "run_e2e_m5"
    payload.setdefault("config", {})["timeframe"] = "5min"
    m5 = runs_dir / "run_e2e_m5"
    m5.mkdir(parents=True, exist_ok=True)
    (m5 / "result.json").write_text(json.dumps(payload), encoding="utf-8")
    (m5 / "config.json").write_text(json.dumps({"run_id": "run_e2e_m5", "symbol": "XAUUSD",
                                                "timeframe": "5min"}), encoding="utf-8")
    cleanup_dirs.append(m5)
    status, body = api(bridge, "/runs/run_e2e_m5/candles?limit=80")
    check("5min candles: 200 + real rows (no hidden M15)", status == 200 and body.get("count", 0) > 0,
          f"{status} count={body.get('count')}")
    check("5min candles: timeframe echoes the run, not M15",
          body.get("timeframe") == "5min", str(body.get("timeframe")))
    # Same explicit span: 5min must yield more real bars than 15min.
    _, c15 = api(bridge, "/runs/run_0001/candles?start=2025-01-02T00:00:00Z&end=2025-01-02T06:00:00Z&limit=400")
    _, c5 = api(bridge, "/runs/run_e2e_m5/candles?start=2025-01-02T00:00:00Z&end=2025-01-02T06:00:00Z&limit=400")
    check("5min denser than 15min over the same span (no hidden M15)",
          c5.get("count", 0) > c15.get("count", 0),
          f"5min={c5.get('count')} 15min={c15.get('count')}")

    status, ticks = api(bridge, "/runs/run_0001/ticks?start=2025-01-02T00:15:00Z&end=2025-01-02T00:18:00Z&limit=50")
    check("ticks: 200 + real BID/ASK", status == 200 and ticks.get("count", 0) > 0,
          f"{status} count={ticks.get('count')}")
    if ticks.get("ticks"):
        row = ticks["ticks"][0]
        check("ticks: spread equals ask-bid",
              abs(row["spread"] - (row["ask"] - row["bid"])) < 1e-6, str(row))
    check("ticks: bounded limit honoured", ticks.get("count", 0) <= 50, str(ticks.get("count")))


# --------------------------------------------------------------------------
# Part 2 — UI truthfulness
# --------------------------------------------------------------------------

FETCH_RECORDER = """
(() => {
  window.__qblFetches = [];
  const orig = window.fetch;
  window.fetch = function(input, init) {
    try { window.__qblFetches.push(typeof input === 'string' ? input : (input && input.url) || ''); } catch (e) {}
    return orig.apply(this, arguments);
  };
})();
"""

# Radix Tabs respond to the full pointer sequence, not a bare .click().
CLICK_TEXT_JS = """(([root,text]) => { const el=[...document.querySelectorAll(root)].find(b=>b.textContent.includes(text)); if(!el) return 'NO'; for (const t of ['pointerdown','mousedown','mouseup','click']) el.dispatchEvent(new MouseEvent(t,{bubbles:true,cancelable:true,view:window})); return 'OK'; })"""


def part2(app: str, chrome_port: int) -> None:
    chrome = launch_chrome(chrome_port)
    try:
        targets = wait_debugger(chrome_port)
        page = next(t for t in targets if t.get("type") == "page")
        cdp = CDP(page["webSocketDebuggerUrl"])
        for dom in ("Page.enable", "Runtime.enable", "Log.enable", "Network.enable"):
            cdp.call(dom)
        # Install the fetch recorder BEFORE the app boots.
        cdp.call("Page.addScriptToEvaluateOnNewDocument", {"source": FETCH_RECORDER})
        cdp.call("Page.navigate", {"url": app})
        hydrated = cdp.evaluate(f"({WAIT_SEL_JS})('.run-source button')", timeout=45)
        check("app hydrates with a run", hydrated,
              (cdp.evaluate("(document.body.textContent||'').slice(0,140)") or "")
              + " | console:" + "; ".join(cdp.console_errors[:2]))
        check("archived run badge", cdp.evaluate(f"({WAIT_TEXT_JS})(['.demo-badge','ARCHIVED RUN'])"))
        check("bridge connected", cdp.evaluate(f"({WAIT_TEXT_JS})(['.engine','Connected'])"))

        # Wait for the price chart to render real candles.
        chart_ready = cdp.evaluate(
            "(()=>new Promise(res=>{const ok=()=>{const p=document.querySelector('.charts-grid');"
            "if(!p)return false;const shapes=p.querySelectorAll('svg rect,svg path,svg line');"
            "return shapes.length>10 && !/Market candles unavailable/.test(p.textContent||'')};"
            "if(ok())return res(true);const t0=Date.now();"
            "const iv=setInterval(()=>{if(ok()||Date.now()-t0>20000){clearInterval(iv);res(ok())}},400)}))()", timeout=45)
        check("price chart renders real candles", chart_ready,
              cdp.evaluate("(document.querySelector('.equity-empty')||{}).textContent||'chart ok'"))

        fetches = cdp.evaluate("window.__qblFetches || []") or []
        check("UI requested real run candles", any("/candles" in u for u in fetches),
              "; ".join(fetches[-4:]))
        check("UI did NOT request demo/fixture market data",
              not any("demo" in u.lower() for u in fetches), "")

        body = cdp.evaluate("document.body.textContent || ''")
        check("no 'Equity Curve' title", "Equity Curve" not in body)
        check("price-term title present", "Cumulative Trade PnL" in body)
        check("no demo price leaked into run view", "2,799.84" not in body, "demo trade 1 entry absent")
        check("real run price present", "2,626.08" in body or "2,626" in body, "archived trade 1 entry")

        # v1.0.2 QA fix: header shows the requested range + a timeframe label;
        # PnL x-axis labels are thinned while every data point stays.
        header = cdp.evaluate("document.querySelector('.main-heading p')?.textContent || ''")
        check("header shows timeframe label + date range",
              "M15" in header and "→" in header and "UTC" in header, header[:120])
        pnl_circles = cdp.evaluate("document.querySelectorAll('.charts-grid .equity svg circle').length")
        pnl_labels = cdp.evaluate(
            "[...document.querySelectorAll('.charts-grid .equity svg text')].filter(t=>/^\\d+$/.test(t.textContent)).length")
        check("all PnL data points kept", pnl_circles >= 7, str(pnl_circles))
        check("PnL x-axis labels thinned (<=10)", 0 < pnl_labels <= 10, str(pnl_labels))

        # Trade Inspector -> Tick Execution (real ticks).
        cdp.evaluate(f"({CLICK_TEXT_JS})(['.navigation button','Trade Inspector'])")
        check("inspector opens", cdp.evaluate(f"({WAIT_SEL_JS})('.inspector')"),
              cdp.evaluate("[...document.querySelectorAll('.navigation button')].map(b=>b.getAttribute('data-state')).join(',')"))
        # Wait for the debugger tab strip, then switch to Tick Execution.
        cdp.evaluate("(()=>new Promise(res=>{const ok=()=>!!document.querySelector('.compact-tabs button');"
                     "if(ok())return res(true);const t0=Date.now();"
                     "const iv=setInterval(()=>{if(ok()||Date.now()-t0>15000){clearInterval(iv);res(ok())}},300)}))()", timeout=30)
        cdp.evaluate(f"({CLICK_TEXT_JS})(['.compact-tabs button','Tick Execution'])")
        tick_ready = cdp.evaluate(
            "(()=>new Promise(res=>{const ok=()=>{const t=document.querySelector('.tick-chart');"
            "if(!t)return false;const txt=t.textContent||'';"
            "if(/Execution ticks unavailable/.test(txt))return false;"
            "return t.querySelectorAll('path').length>0 || /Real cached ticks/.test(txt)};"
            "if(ok())return res(true);const t0=Date.now();"
            "const iv=setInterval(()=>{if(ok()||Date.now()-t0>20000){clearInterval(iv);res(ok())}},400)}))()", timeout=45)
        check("tick execution renders real ticks", tick_ready,
              cdp.evaluate("(document.querySelector('.tick-chart')?.textContent||'no .tick-chart').slice(0,80)"))
        fetches = cdp.evaluate("window.__qblFetches || []") or []
        check("UI requested real run ticks", any("/ticks" in u for u in fetches),
              "; ".join([u for u in fetches if "/ticks" in u][-3:]))
        body = cdp.evaluate("document.body.textContent || ''")
        check("no seeded-visualization copy", "Seeded visualization only" not in body)

        # Checks tab: real validation only, no fabricated 14/14.
        cdp.evaluate(f"({CLICK_TEXT_JS})(['.detail-tabs button','Checks'])")
        time.sleep(0.8)
        body = cdp.evaluate("document.body.textContent || ''")
        check("no fabricated 14 / 14 check count", "14 / 14" not in body)
        check("validation shows proven/N-A", "proven" in body or "N/A" in body)

        # Data Explorer raw ticks are real.
        cdp.evaluate(f"({CLICK_TEXT_JS})(['.navigation button','Data Explorer'])")
        time.sleep(1.2)
        body = cdp.evaluate("document.body.textContent || ''")
        check("Data Explorer raw ticks labelled real", "real cached ticks" in body,
              body[body.find("Raw Ticks"):body.find("Raw Ticks") + 60] if "Raw Ticks" in body else "")

        errs = [e for e in cdp.console_errors if "favicon" not in e.lower()]
        check("no console errors", not errs, "; ".join(errs[:2]))
        cdp.ws.close()
    finally:
        chrome.terminate()


def _free_port() -> int:
    """A currently-free localhost port, so a zombie Chrome from a prior run can
    never be re-used for the debugger connection."""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=os.environ.get("QBL_APP_URL", "http://127.0.0.1:5173/"))
    ap.add_argument("--bridge", default="http://127.0.0.1:8765")
    ap.add_argument("--chrome-port", type=int, default=0)
    args = ap.parse_args()
    chrome_port = args.chrome_port or _free_port()

    runs_dir = ROOT / "runs"
    public = ROOT / "Quant-Backtest-Lab" / "public"
    manifest = public / "adapters-run-manifest.json"
    manifest_before = manifest.read_text(encoding="utf-8") if manifest.is_file() else None
    public_before = {p.name for p in public.glob("runs-*-result.json")}
    cleanup_dirs: list[Path] = []

    try:
        part1(args.bridge, runs_dir, cleanup_dirs)
        # Publish run_0001 so the app lists it, then restore in finally.
        status, _ = api(args.bridge, "/runs/run_0001/result.json") if False else (0, {})
        req = urllib.request.Request(f"{args.bridge}/runs/run_0001/publish", data=b"{}",
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            urllib.request.urlopen(req, timeout=30).read()
        except urllib.error.HTTPError as e:
            print("INFO publish:", e.code, e.read()[:200])
        part2(args.url, chrome_port)
    finally:
        if manifest_before is not None:
            manifest.write_text(manifest_before, encoding="utf-8")
        for p in public.glob("runs-*-result.json"):
            if p.name not in public_before:
                p.unlink()
        for d in cleanup_dirs:
            shutil.rmtree(d, ignore_errors=True)

    print("E2E_RESULT=" + ("PASS" if not FAILS else f"FAIL:{FAILS}"))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    raise SystemExit(main())
