'use client';
import { useState } from 'react';
import { AlertTriangle, GitCompare, Loader2 } from 'lucide-react';
import { BridgeError, compareRuns, type ComparisonResult, type ComparisonRun } from './bridge-client';

function errText(err: unknown): string {
  if (err instanceof BridgeError) return `${err.code}: ${err.message}`;
  return err instanceof Error ? err.message : 'comparison failed';
}

function fmtNum(value: number | null | undefined, digits = 2): string {
  return value === null || value === undefined
    ? '—'
    : value.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

function paramsSummary(params: Record<string, unknown>): string {
  const entries = Object.entries(params ?? {});
  return entries.length ? entries.map(([k, v]) => `${k}=${String(v)}`).join(', ') : '—';
}

const COLORS = ['#5498f5', '#67d88f', '#e6b450', '#ee757b', '#b98cf0', '#4fd0e0', '#f0956a', '#9ad14b'];

type MetricKey = 'win_rate' | 'net_pnl' | 'avg_trade' | 'profit_factor' | 'max_drawdown' | 'expectancy';
// Unitless ratios remain comparable across instruments; price-term metrics do not.
const UNITLESS_METRICS = new Set<MetricKey>(['win_rate', 'profit_factor']);
const METRICS: Array<{ key: MetricKey; label: string; dir: 'max' | 'min' | null }> = [
  { key: 'win_rate', label: 'Win %', dir: 'max' },
  { key: 'net_pnl', label: 'Net PnL', dir: 'max' },
  { key: 'avg_trade', label: 'Avg trade', dir: 'max' },
  { key: 'profit_factor', label: 'Profit factor', dir: 'max' },
  { key: 'max_drawdown', label: 'Max DD', dir: 'min' },
  { key: 'expectancy', label: 'Expectancy', dir: 'max' },
];

function bestWorst(runs: ComparisonRun[], key: MetricKey, dir: 'max' | 'min' | null) {
  if (!dir) return { best: -1, worst: -1 };
  const values = runs.map((r) => r[key]);
  if (!values.every((v) => typeof v === 'number' && Number.isFinite(v))) return { best: -1, worst: -1 };
  const nums = values as number[];
  const target = dir === 'max' ? Math.max(...nums) : Math.min(...nums);
  const opposite = dir === 'max' ? Math.min(...nums) : Math.max(...nums);
  return { best: nums.indexOf(target), worst: target === opposite ? -1 : nums.indexOf(opposite) };
}

/** Best/worst over any numeric run field; never crowns an unavailable value. */
function bestWorstBy(
  runs: ComparisonRun[],
  pick: (r: ComparisonRun) => number | null,
  dir: 'max' | 'min' | null,
) {
  if (!dir) return { best: -1, worst: -1 };
  const values = runs.map(pick);
  if (!values.every((v) => typeof v === 'number' && Number.isFinite(v))) return { best: -1, worst: -1 };
  const nums = values as number[];
  const target = dir === 'max' ? Math.max(...nums) : Math.min(...nums);
  const opposite = dir === 'max' ? Math.min(...nums) : Math.max(...nums);
  return { best: nums.indexOf(target), worst: target === opposite ? -1 : nums.indexOf(opposite) };
}

/** Isolated multi-run cumulative-PnL chart (price-term, indexed). */
function PnlChart({ runs, equity }: { runs: ComparisonRun[]; equity: Record<string, number[]> }) {
  const series = runs.map((r, i) => ({ run: r, values: equity[r.run_id] ?? [], color: COLORS[i % COLORS.length] }));
  const maxLen = Math.max(1, ...series.map((s) => s.values.length));
  const all = series.flatMap((s) => s.values);
  const min = Math.min(0, ...all);
  const max = Math.max(0, ...all);
  const span = max - min || 1;
  const W = 640, H = 180, pad = 6;
  const x = (i: number) => pad + (i / Math.max(1, maxLen - 1)) * (W - 2 * pad);
  const y = (v: number) => pad + (1 - (v - min) / span) * (H - 2 * pad);
  const zeroY = y(0);
  return (
    <div className="cmp-chart">
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img" aria-label="Cumulative PnL comparison">
        <line x1={pad} y1={zeroY} x2={W - pad} y2={zeroY} stroke="#2a3340" strokeDasharray="3 3" />
        {series.map((s) => (
          s.values.length > 1 ? (
            <polyline
              key={s.run.run_id}
              fill="none"
              stroke={s.color}
              strokeWidth={1.4}
              points={s.values.map((v, i) => `${x(i)},${y(v)}`).join(' ')}
            />
          ) : null
        ))}
      </svg>
      <div className="cmp-legend">
        {series.map((s) => (
          <span key={s.run.run_id}><i style={{ background: s.color }} />{s.run.run_id}</span>
        ))}
        <span className="meta">indexed points · price-term cumulative PnL</span>
      </div>
    </div>
  );
}

/**
 * Compare 2..5 selected runs. Comparison visualization is isolated here and
 * never touches the main dashboard Equity chart. No universal score is
 * invented; best/worst emphasis only appears when a metric is available for
 * every run and the direction is mathematically meaningful.
 */
export function RunComparison({ runIds }: { runIds: string[] }) {
  const [result, setResult] = useState<ComparisonResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const compare = async () => {
    if (runIds.length < 2) return;
    setLoading(true);
    setError(null);
    try {
      setResult(await compareRuns(runIds.slice(0, 5)));
    } catch (err) {
      setError(errText(err));
      setResult(null);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="lib-panel">
      <div className="lib-toolbar">
        <span className="lib-hint">
          Selected {runIds.length ? runIds.join(', ') : '— none —'} (choose 2–5 runs in the Runs tab)
        </span>
        <button className="primary" onClick={compare} disabled={runIds.length < 2 || loading} aria-label="Compare selected runs">
          {loading ? <Loader2 size={13} className="spin" /> : <GitCompare size={13} />} Compare
        </button>
      </div>

      {error && <p className="lib-status lib-error" role="alert"><AlertTriangle size={12} /> {error}</p>}
      {result && (
        <>
          <ul className="cmp-warnings">
            {result.warnings.map((w) => <li key={w}><AlertTriangle size={11} /> {w}</li>)}
          </ul>

          <div className="cmp-table-wrap">
            <table className="cmp-table">
              <thead>
                <tr>
                  <th>Run</th><th>Strategy</th><th>Symbol</th><th>TF</th>
                  <th>Trades</th><th>Long/Short</th>
                  {METRICS.map((m) => <th key={m.key}>{m.label}</th>)}
                  <th>Data</th>
                  <th>Parameters</th>
                  <th>Stop / Take</th>
                </tr>
              </thead>
              <tbody>
                {result.runs.map((run, ri) => (
                  <tr key={run.run_id}>
                    <td className="mono">{run.run_id}</td>
                    <td>{run.strategy.name} · v{run.strategy.version}</td>
                    <td>{run.symbol ?? '—'}</td>
                    <td>{run.timeframe ?? '—'}</td>
                    <td>{run.total_trades ?? '—'}</td>
                    <td>{run.long_trades ?? '—'}/{run.short_trades ?? '—'}</td>
                    {METRICS.map((m) => {
                      const comparable = !result.differences.symbol || UNITLESS_METRICS.has(m.key);
                      const { best, worst } = comparable
                        ? bestWorst(result.runs, m.key, m.dir)
                        : { best: -1, worst: -1 };
                      const cls = ri === best ? 'good-text' : ri === worst ? 'bad-text' : '';
                      const suffix = m.key === 'win_rate' ? '%' : '';
                      return <td key={m.key} className={cls}>{fmtNum(run[m.key])}{suffix}</td>;
                    })}
                    <td>{run.data_quality_status}</td>
                    <td className="mono small">{paramsSummary(run.parameters)}</td>
                    <td>{fmtNum(run.execution.stop_distance)} / {fmtNum(run.execution.take_profit_distance)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <PnlChart runs={result.runs} equity={result.equity} />

          {result.runs.some((r) => r.account_ready) && (
            <>
              <div className="rr-group-label">
                Account terms <span className="meta">· best/worst shown only when account setups are compatible</span>
              </div>
              <div className="cmp-table-wrap">
                <table className="cmp-table">
                  <thead>
                    <tr>
                      <th>Run</th><th>Account</th><th>Acct net PnL</th><th>Return %</th>
                      <th>Commission</th><th>Realized DD</th><th>Lots</th><th>Contract</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.runs.map((run, ri) => {
                      const comparable = result.differences.account_comparable;
                      const net = comparable
                        ? bestWorstBy(result.runs, (r) => r.account_net_pnl, 'max')
                        : { best: -1, worst: -1 };
                      const ret = comparable
                        ? bestWorstBy(result.runs, (r) => r.account_return_pct, 'max')
                        : { best: -1, worst: -1 };
                      const cls = (bw: { best: number; worst: number }) =>
                        ri === bw.best ? 'good-text' : ri === bw.worst ? 'bad-text' : '';
                      return (
                        <tr key={run.run_id}>
                          <td className="mono">{run.run_id}</td>
                          <td>{run.account_ready ? run.account_currency ?? '—' : 'Not available'}</td>
                          <td className={cls(net)}>{run.account_ready ? fmtNum(run.account_net_pnl) : '—'}</td>
                          <td className={cls(ret)}>{run.account_ready && run.account_return_pct != null ? `${fmtNum(run.account_return_pct)}%` : '—'}</td>
                          <td>{run.account_ready ? fmtNum(run.account_commission) : '—'}</td>
                          <td>{run.account_ready ? fmtNum(run.account_max_drawdown) : '—'}</td>
                          <td>{run.account_ready ? fmtNum(run.account_position_size_lots) : '—'}</td>
                          <td>{run.account_ready ? fmtNum(run.account_contract_size) : '—'}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </>
      )}
    </div>
  );
}
