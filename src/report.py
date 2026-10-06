"""Standalone HTML report generator for archived BacktestResult runs.

Reference: ARCHITECTURE.md Section 6 (Report Archive System).

Flow: BacktestResult (runs/<run_id>/result.json) -> this module
(presentation only) -> runs/<run_id>/report.html

Rules: source of truth is the validated archived BacktestResult; no
strategy/execution recalculation; portable artifact (inline CSS + SVG,
no backend/API/external deps); account metrics render Not available;
MAE/MFE None renders N/A. Standard library only.
"""

from __future__ import annotations

import html
import json
from pathlib import Path

ARCHIVE_FILES = ("config.json", "result.json", "trades.csv",
                 "equity.csv", "report.html", "strategy.py")

ACCOUNT_ONLY = ("Balance", "Return %", "Sharpe", "Sortino",
                "Calmar", "Annualized return")
#: Unsupported in v1 even when an account model exists — must stay Pending.
ACCOUNT_UNSUPPORTED = ("Sharpe", "Sortino", "Calmar", "Annualized return")


def _esc(v: object) -> str:
    return "N/A" if v is None else html.escape(str(v), quote=True)


def _num(v: object, digits: int = 4) -> str:
    if v is None:
        return "N/A"
    if isinstance(v, float):
        t = f"{v:.{digits}f}".rstrip("0").rstrip(".")
        return t if t else "0"
    return _esc(v)


def _dur(s: object) -> str:
    try:
        t = int(float(s))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "N/A"
    h, r = divmod(t, 3600)
    m, sec = divmod(r, 60)
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m {sec}s"
    return f"{sec}s"


def _svg(equity: list, w: int = 720, h: int = 180) -> str:
    vals = [float(p.get("cumulative_pnl", 0.0)) for p in equity]
    if not vals:
        return '<p class="muted">No equity points archived.</p>'
    pl, pr, pt, pb = 56, 12, 12, 24
    iw, ih = w - pl - pr, h - pt - pb
    lo = min(0.0, min(vals))
    hi = max(0.0, max(vals))
    sp = (hi - lo) or 1.0
    n = len(vals)
    xs = [pl + (iw * i / (n - 1) if n > 1 else iw / 2) for i in range(n)]
    ys = [pt + ih * (1.0 - (v - lo) / sp) for v in vals]
    d = [f"M {xs[0]:.1f} {ys[0]:.1f}"]
    for i in range(1, n):
        d.append(f"L {xs[i]:.1f} {ys[i-1]:.1f} L {xs[i]:.1f} {ys[i]:.1f}")
    z = pt + ih * (1.0 - (0.0 - lo) / sp)
    lab = ""
    for t in (lo, (lo + hi) / 2, hi):
        yy = pt + ih * (1.0 - (t - lo) / sp)
        lab += (f'<text x="{pl-6}" y="{yy+3.5}" text-anchor="end"'
                f' class="tick">{_num(round(t,4))}</text>'
                f'<line x1="{pl}" y1="{yy:.1f}" x2="{w-pr}" y2="{yy:.1f}" class="grid"/>')
    dot = "".join(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2.4" class="dot"/>' for x, y in zip(xs, ys))
    return (f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="Cumulative PnL">'
            f"{lab}"
            f'<line x1="{pl}" y1="{z:.1f}" x2="{w-pr}" y2="{z:.1f}" class="zero"/>'
            f'<path d="{" ".join(d)}" class="line"/>{dot}'
            f'<text x="{pl}" y="{h-8}" class="tick">first exit</text>'
            f'<text x="{w-pr}" y="{h-8}" text-anchor="end" class="tick">last exit</text></svg>')


CSS = (""
":root{color-scheme:dark;--bg:#0d1117;--surface:#161b22;--border:#30363d;"
"--text:#e6edf3;--muted:#8b949e;--good:#3fb950;--bad:#f85149;--info:#58a6ff}"
"*{box-sizing:border-box}"
"body{margin:0;background:var(--bg);color:var(--text);"
"font:13px/1.55 -apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif}"
".wrap{max-width:1024px;margin:0 auto;padding:24px 20px 64px}"
"header.top{border:1px solid var(--border);background:var(--surface);"
"border-radius:8px;padding:16px 20px;margin-bottom:16px}"
".eyebrow{font-size:10.5px;letter-spacing:.12em;color:var(--muted);text-transform:uppercase}"
"h1{margin:4px 0 2px;font-size:20px}"
"h2{font-size:14px;margin:0 0 10px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted)}"
".sub{color:var(--muted);font-size:12px;margin:0}"
".grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}"
"@media(max-width:760px){.grid{grid-template-columns:1fr}}"
".card{border:1px solid var(--border);background:var(--surface);"
"border-radius:8px;padding:14px 16px;margin-bottom:16px}"
"dl.kv{display:grid;grid-template-columns:190px 1fr;gap:3px 12px;margin:0}"
"dl.kv dt{color:var(--muted)}"
"dl.kv dd{margin:0;font-variant-numeric:tabular-nums;overflow-wrap:anywhere}"
"table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}"
"th,td{text-align:right;padding:6px 8px;border-bottom:1px solid var(--border);white-space:nowrap}"
"th:first-child,td:first-child{text-align:left}"
"th{color:var(--muted);font-weight:600;font-size:11.5px}"
".good{color:var(--good)}.bad{color:var(--bad)}.muted{color:var(--muted)}"
".badge{display:inline-block;font-size:11px;border:1px solid var(--border);"
"border-radius:4px;padding:1px 8px;margin-right:6px}"
".badge.archived{color:var(--info);border-color:#1f6feb}"
".pill-na{color:var(--muted);border:1px dashed var(--border);border-radius:4px;"
"padding:0 6px;font-size:11.5px}"
"svg{width:100%;height:auto;display:block}"
"svg .line{fill:none;stroke:var(--info);stroke-width:1.8}"
"svg .dot{fill:var(--info)}svg .grid{stroke:var(--border);stroke-width:1}"
"svg .zero{stroke:var(--muted);stroke-width:1;stroke-dasharray:4 3}"
"svg .tick{fill:var(--muted);font-size:10px}"
"footer{color:var(--muted);font-size:11.5px;margin-top:8px}"
".scroll{overflow-x:auto}")


def _rows(trades: list, acmap: dict | None = None) -> str:
    out = []
    for t in trades:
        p = float(t.get("pnl_price", 0.0) or 0.0)
        c = "good" if p > 0 else ("bad" if p < 0 else "")
        cells = [_esc(t.get("trade_id")), _esc(t.get("direction")),
                 _esc(t.get("signal_available_time")), _esc(t.get("entry_time")),
                 _num(t.get("entry_price")), _esc(t.get("exit_time")),
                 _num(t.get("exit_price")), _esc(t.get("exit_reason"))]
        row = ("<tr><td>" + "</td><td>".join(cells)
               + f'</td><td class="{c}">{_num(p)}</td>'
               + f"<td>{_dur(t.get('duration_seconds'))}</td>"
               + f"<td>{_num(t.get('mae'))}</td><td>{_num(t.get('mfe'))}</td>")
        if acmap is not None:
            a = acmap.get(int(t.get("trade_id") or 0), {})
            net = a.get("net_account_pnl")
            nc = "good" if (net or 0) > 0 else ("bad" if (net or 0) < 0 else "")
            row += (f"<td>{_num(a.get('gross_account_pnl'))}</td>"
                    f"<td>{_num(a.get('commission'))}</td>"
                    f'<td class="{nc}">{_num(net)}</td>')
        out.append(row + "</tr>")
    return "\n".join(out)


def _account_lookup(account: dict | None) -> dict | None:
    """Trade-id -> account row map, or None when no account model is available."""
    if not account or not account.get("available"):
        return None
    return {int(r.get("trade_id", 0)): r for r in (account.get("trades") or [])}


def _perf(me: dict, trades: list, account_available: bool = False):
    longs = sum(1 for t in trades if t.get("direction") == "LONG")
    shorts = sum(1 for t in trades if t.get("direction") == "SHORT")
    total = me.get("total_trades", len(trades))
    avg = (me.get("net_pnl", 0.0) / total) if total else 0.0
    rows = [("Total trades", _num(total, 0)), ("Long trades", _num(longs, 0)),
            ("Short trades", _num(shorts, 0)), ("Wins", _num(me.get("wins"), 0)),
            ("Losses", _num(me.get("losses"), 0)),
            ("Breakeven", _num(me.get("breakeven"), 0)),
            ("Win rate %", _num(me.get("win_rate"))),
            ("Net PnL (price terms)", _num(me.get("net_pnl"))),
            ("Gross profit", _num(me.get("gross_profit"))),
            ("Gross loss", _num(me.get("gross_loss"))),
            ("Average trade", _num(avg)), ("Expectancy", _num(me.get("expectancy"))),
            ("Average win", _num(me.get("avg_win"))),
            ("Average loss", _num(me.get("avg_loss"))),
            ("Profit factor", _num(me.get("profit_factor"))),
            ("Max drawdown (price terms)", _num(me.get("max_drawdown")))]
    body = "".join(f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in rows)
    pending = ACCOUNT_UNSUPPORTED if account_available else ACCOUNT_ONLY
    na = "".join('<tr><td>' + m + '</td><td><span class="pill-na">'
                 "Not available — requires account model</span></td></tr>"
                 for m in pending)
    return body + na


def _account_cards(account: dict) -> str:
    """Account configuration, instrument economics, performance and balance chart."""
    model = account.get("model") or {}
    inst = account.get("instrument") or {}
    met = account.get("metrics") or {}
    cfg = "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in [
        ("Account currency", _esc(account.get("account_currency"))),
        ("Starting balance", _num(model.get("starting_balance"))),
        ("Position size (lots)", _num(model.get("position_size_lots"))),
        ("Commission / lot / round turn", _num(model.get("commission_per_lot_round_turn"))),
    ])
    con = "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in [
        ("Symbol", _esc(inst.get("symbol"))),
        ("Quote currency", _esc(inst.get("quote_currency"))),
        ("Contract size", _num(inst.get("contract_size"))),
        ("Tick size", _num(inst.get("tick_size"))),
    ])
    perf = "".join(f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in [
        ("Starting balance", _num(met.get("starting_balance"))),
        ("Ending balance", _num(met.get("ending_balance"))),
        ("Net account PnL", _num(met.get("net_pnl"))),
        ("Gross profit", _num(met.get("gross_profit"))),
        ("Gross loss", _num(met.get("gross_loss"))),
        ("Total commission", _num(met.get("total_commission"))),
        ("Total return %", _num(met.get("total_return_pct"))),
        ("Average trade (account)", _num(met.get("avg_trade_pnl"))),
        ("Max realized-balance drawdown", _num(met.get("max_drawdown"))),
        ("Max realized-balance drawdown %", _num(met.get("max_drawdown_pct"))),
        ("Recovery factor", _num(met.get("recovery_factor"))),
    ])
    return (
        "<div class='grid'>"
        f"<section class='card'><h2>Account configuration</h2><dl class='kv'>{cfg}</dl></section>"
        f"<section class='card'><h2>Instrument economics</h2><dl class='kv'>{con}</dl></section>"
        "</div>"
        "<section class='card'><h2>Account performance</h2>"
        "<div class='scroll'><table><thead><tr><th>Metric</th><th>Value</th>"
        f"</tr></thead><tbody>{perf}</tbody></table></div>"
        "<p class='muted'>Realized balance from closed trades only — not tick-by-tick "
        "floating equity. Spread is already inside the engine's BID/ASK fills and is "
        "not re-applied. No slippage, swap, leverage or margin simulation.</p></section>"
    )


def _balance_svg(account: dict) -> str:
    curve = account.get("balance_curve") or []
    pts = [{"cumulative_pnl": p.get("realized_balance", 0.0)} for p in curve]
    if not pts:
        return "<p class='muted'>No realized balance points archived.</p>"
    return _svg(pts)


def render_report(result: dict) -> str:
    """Render a validated BacktestResult dict to standalone HTML."""
    st = result.get("strategy", {})
    cf = result.get("config", {})
    me = result.get("metrics", {})
    dq = result.get("data_quality", {})
    ex = result.get("execution", {})
    trades = list(result.get("trades", []))
    eq = list(result.get("equity", []))
    account = result.get("account") or {}
    account_available = bool(account.get("available"))
    acmap = _account_lookup(account) if account_available else None
    prm = cf.get("parameters", {})
    ptxt = ", ".join(f"{k}={v}" for k, v in prm.items()) if prm else "--"
    ident = "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in [
        ("Run id", _esc(result.get("run_id"))),
        ("Created (UTC)", _esc(result.get("created_at"))),
        ("Strategy", _esc(st.get("name"))),
        ("Strategy version", _esc(st.get("version"))),
        ("Symbol", _esc(cf.get("symbol"))),
        ("Timeframe", _esc(cf.get("timeframe"))),
        ("Tested range (UTC)", _esc(cf.get("start")) + " to " + _esc(cf.get("end"))),
        ("Parameters", _esc(ptxt))])
    data = "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in [
        ("Data source", _esc(dq.get("data_source") or cf.get("data_source"))),
        ("Tick count", _num(dq.get("tick_count"), 0)),
        ("Candle count", _num(dq.get("candle_count"), 0)),
        ("Signals received", _num(ex.get("signals_received"), 0)),
        ("Signals executed", _num(ex.get("signals_executed"), 0)),
        ("Signals ignored", _num(ex.get("signals_ignored"), 0)),
        ("Engine", _esc(ex.get("engine") or "src.backtest")),
        ("Engine window (UTC)",
         _esc(ex.get("started_at")) + " to " + _esc(ex.get("finished_at")))])
    val = dq.get("validation", {})
    wlist = dq.get("warnings", []) or []
    warn = "".join(f"<li>{_esc(x)}</li>" for x in wlist) or "<li>None archived.</li>"
    n = len(trades)
    account_sections = (
        _account_cards(account)
        + "<section class='card'><h2>Realized account balance</h2>"
        + _balance_svg(account)
        + "<p class='muted'>Realized balance from closed trades only — not "
        "tick-by-tick floating equity.</p></section>"
    ) if account_available else ""
    if account_available:
        account_note = (
            "<p class='muted'>Account metrics available (see account sections below). "
            "Price terms remain authoritative.</p>"
        )
        badge = "account currency"
        balance_note = ("<p class='muted'>From archived equity points. Price-term PnL, "
                        "not account balance.</p>")
    elif account:
        account_note = ("<p class='muted'>Account metrics Not available: "
                        + _esc(account.get("reason_unavailable") or "incomplete account model.")
                        + "</p>")
        badge = "price terms"
        balance_note = ("<p class='muted'>From archived equity points. No balance curve: "
                        "no account balance series archived.</p>")
    else:
        # Byte-identical to the original non-account rendering.
        account_note = "<p class='muted'>Account metrics Not available: no account model.</p>"
        badge = "price terms"
        balance_note = ("<p class='muted'>From archived equity points. No balance curve: "
                        "no account balance series archived.</p>")
    trade_head = ("<th>#</th><th>Dir</th><th>Signal time</th><th>Entry time</th>"
                  "<th>Entry</th><th>Exit time</th><th>Exit</th><th>Reason</th><th>PnL</th>"
                  "<th>Duration</th><th>MAE</th><th>MFE</th>")
    if account_available:
        trade_head += "<th>Gross acct</th><th>Commission</th><th>Net acct</th>"
    return ("<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width, initial-scale=1'>"
            f"<title>QBL archived run {_esc(result.get('run_id'))}</title>"
            f"<style>{CSS}</style></head><body><div class='wrap'>"
            "<header class='top'><div class='eyebrow'>"
            "Quant Backtest Lab - archived research run</div>"
            f"<h1>Run {_esc(result.get('run_id'))} "
            "<span class='badge archived'>ARCHIVED</span>"
            f"<span class='badge'>{badge}</span></h1>"
            "<p class='sub'>From validated archived result.json. "
            "No strategy or execution logic re-run. PnL in price terms.</p></header>"
            "<div class='grid'>"
            f"<section class='card'><h2>Run identity</h2><dl class='kv'>{ident}</dl></section>"
            f"<section class='card'><h2>Data / execution</h2><dl class='kv'>{data}</dl>"
            "</section></div>"
            f"{account_sections}"
            "<section class='card'><h2>Performance (price terms)</h2>"
            "<div class='scroll'><table><thead><tr><th>Metric</th><th>Value</th>"
            f"</tr></thead><tbody>{_perf(me, trades, account_available)}</tbody></table></div>"
            f"{account_note}</section>"
            "<section class='card'><h2>Cumulative PnL (price terms)</h2>"
            f"{_svg(eq)}"
            f"{balance_note}</section>"
            f"<section class='card'><h2>Trades ({n})</h2>"
            "<div class='scroll'><table><thead><tr>"
            f"{trade_head}"
            f"</tr></thead><tbody>{_rows(trades, acmap)}</tbody>"
            "</table></div>"
            "<p class='muted'>MAE/MFE N/A where the archive carries none.</p></section>"
            "<section class='card'><h2>Data quality</h2><dl class='kv'>"
            f"<dt>Validation</dt><dd>{_esc(json.dumps(val) if val else '--')}</dd></dl>"
            f"<ul class='muted'>{warn}</ul></section>"
            "<footer>Quant Backtest Lab archive artifact - contract "
            f"{_esc(result.get('contract_version'))} - fingerprint "
            f"<code>{_esc(cf.get('data_fingerprint'))}</code></footer>"
            "</div></body></html>")


def generate_report(run_dir, *, overwrite: bool = False):
    """Write report.html for one archived run directory. Returns path."""
    from src.result_contract import BacktestResult
    d = Path(run_dir)
    payload = json.loads((d / "result.json").read_text(encoding="utf-8"))
    errs = BacktestResult.from_dict(payload).validate()
    if errs:
        raise ValueError("result.json invalid: " + "; ".join(errs))
    out = d / "report.html"
    if out.exists() and not overwrite:
        raise FileExistsError(str(out) + " exists (use --overwrite)")
    out.write_text(render_report(payload), encoding="utf-8")
    return out


def main(argv=None) -> str:
    """CLI: python -m src.report runs/run_0001 [--overwrite]."""
    import argparse
    ap = argparse.ArgumentParser(description="Render archived run report.html.")
    ap.add_argument("run_dir")
    ap.add_argument("--overwrite", action="store_true")
    a = ap.parse_args(argv)
    return str(generate_report(a.run_dir, overwrite=a.overwrite))


if __name__ == "__main__":
    print(main())




