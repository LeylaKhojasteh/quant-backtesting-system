'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { AreaChart, BarChart3, LineChart, PieChart, TrendingUp } from 'lucide-react';
import { fmt } from './format';
import {
  advancedPerformance,
  directionStats,
  drawdownPeriods,
  drawdownSeries,
  entriesByHour,
  enrichRunTrades,
  equitySeries,
  exitQuality,
  exitStats,
  pnlHistogram,
  performance,
  risk,
  signalAnalysis,
  tradeQuality,
  type EnrichedTrade,
} from './analytics';
import { useRun } from './run-context';
import { formatUtcMs } from './utc';
import { PendingMetric } from './pending-metric';

const GREEN = 'var(--success)';
const RED = 'var(--danger)';
const BLUE = 'var(--primary)';
const WARNING = 'var(--warning)';

/**
 * Measures an SVG host so charts render in real pixels.
 * Returns a tuple so the ref stays a plain binding rather than a property access.
 * Call once per chart.
 */
function useChartSize(): [React.RefObject<SVGSVGElement | null>, boolean, number, number] {
  const ref = useRef<SVGSVGElement | null>(null);
  const [size, setSize] = useState({ w: 0, h: 0 });

  useEffect(() => {
    const node = ref.current;
    if (!node) return;
    const apply = () => {
      const box = node.getBoundingClientRect();
      const w = Math.round(box.width);
      const h = Math.round(box.height);
      setSize((prev) => (prev.w === w && prev.h === h ? prev : { w, h }));
    };
    apply();
    const observer = new ResizeObserver(apply);
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  return [ref, size.w > 40 && size.h > 40, size.w, size.h];
}

function Section({ icon, title, note, children }: { icon: React.ReactNode; title: string; note?: string; children: React.ReactNode }) {
  return (
    <section className="analytics-block panel">
      <div className="panel-head">
        <h2>{icon}{title}</h2>
        {note && <span className="meta">{note}</span>}
      </div>
      {children}
    </section>
  );
}

/** Closed-trade equity with an optional underwater drawdown overlay. */
export function EquityAnalysis({ rows }: { rows: readonly EnrichedTrade[] }) {
  const eq = useMemo(() => equitySeries(rows), [rows]);
  const dd = useMemo(() => drawdownSeries(rows), [rows]);
  const [eqRef, eqReady, eqW, eqH] = useChartSize();
  const [ddRef, ddReady, ddW, ddH] = useChartSize();
  const pad = { l: 48, r: 12, t: 14, b: 22 };
  const plotW = Math.max(30, eqW - pad.l - pad.r);
  const plotH = Math.max(30, eqH - pad.t - pad.b);
  const lo = Math.min(0, ...eq) - 2;
  const hi = Math.max(0, ...eq) + 2;
  const x = (i: number) => (eq.length === 1 ? pad.l + plotW / 2 : pad.l + (i / (eq.length - 1)) * plotW);
  const y = (v: number) => pad.t + ((hi - v) / (hi - lo || 1)) * plotH;
  const line = eq.map((v, i) => `${i ? 'L' : 'M'}${x(i)} ${y(v)}`).join(' ');
  const area = `${line} L${x(eq.length - 1)} ${y(lo)} L${x(0)} ${y(lo)} Z`;
  const maxDd = dd.length ? Math.max(...dd) : 0;
  // Drawdown needs its own scale: 0 at the baseline, maxDd at the top.
  const ddY = (v: number) => pad.t + plotH - (maxDd === 0 ? 0 : (v / maxDd) * plotH);

  return (
    <div className="equity-analysis">
      <div className="equity-analysis-chart">
        <svg ref={eqRef} role="img" aria-label="Cumulative equity curve, price terms">
          {eqReady && (
            <g>
              {[0, 1, 2, 3].map((i) => {
                const gy = pad.t + (plotH / 3) * i;
                return (
                  <g key={i}>
                    <line x1={pad.l} x2={pad.l + plotW} y1={gy} y2={gy} className="gridline" />
                    <text x={4} y={gy + 4} style={{ fontSize: 10 }}>{(hi - (i * (hi - lo)) / 3).toFixed(1)}</text>
                  </g>
                );
              })}
              <line x1={pad.l} x2={pad.l + plotW} y1={y(0)} y2={y(0)} stroke="var(--muted-foreground)" strokeDasharray="3 3" />
              <path d={area} fill={BLUE} opacity=".08" />
              <path d={line} stroke={BLUE} strokeWidth="1.8" fill="none" />
              {eq.map((v, i) => <circle key={i} cx={x(i)} cy={y(v)} r="2.4" fill={BLUE} />)}
            </g>
          )}
        </svg>
        <span className="chart-caption">Cumulative PnL · price terms — not account currency</span>
      </div>
      <div className="equity-analysis-chart">
        <svg ref={ddRef} role="img" aria-label="Drawdown curve, closed trades">
          {ddReady && (
            <g>
              {[0, 1, 2, 3].map((i) => {
                const gy = pad.t + (plotH / 3) * i;
                return (
                  <g key={i}>
                    <line x1={pad.l} x2={pad.l + ddW} y1={gy} y2={gy} className="gridline" />
                    <text x={4} y={gy + 4} style={{ fontSize: 10 }}>{(maxDd - (maxDd * i) / 3).toFixed(1)}</text>
                  </g>
                );
              })}
              <path
                d={dd.map((v, i) => `${i ? 'L' : 'M'}${x(i)} ${ddY(v)}`).join(' ')}
                stroke={WARNING}
                strokeWidth="1.8"
                fill="none"
              />
              <line x1={pad.l} x2={pad.l + ddW} y1={ddY(0)} y2={ddY(0)} stroke="var(--muted-foreground)" strokeDasharray="3 3" />
              <path
                d={`${dd.map((v, i) => `${i ? 'L' : 'M'}${x(i)} ${ddY(v)}`).join(' ')} L${x(dd.length - 1)} ${ddY(0)} L${x(0)} ${ddY(0)} Z`}
                fill={WARNING}
                opacity=".14"
              />
              {ddH > 0 && <text x={pad.l + ddW} y={ddY(maxDd) - 4} textAnchor="end" style={{ fontSize: 9 }}>max {maxDd.toFixed(2)}</text>}
            </g>
          )}
        </svg>
        <span className="chart-caption">Drawdown · price terms — not account currency</span>
      </div>
    </div>
  );
}

/** PnL histogram split into winning and losing buckets. */
export function DistributionAnalysis({ rows }: { rows: readonly EnrichedTrade[] }) {
  const bins = useMemo(() => pnlHistogram(rows, 9), [rows]);
  const [histRef, histReady, histW, histH] = useChartSize();
  const pad = { l: 34, r: 10, t: 14, b: 30 };
  const plotW = Math.max(30, histW - pad.l - pad.r);
  const plotH = Math.max(30, histH - pad.t - pad.b);
  const max = Math.max(1, ...bins.map((b) => b.count));
  const slot = plotW / Math.max(1, bins.length);
  const y = (v: number) => pad.t + plotH - (v / max) * plotH;

  return (
    <div className="distribution-analysis">
      <div className="distribution-chart">
        <svg ref={histRef} role="img" aria-label="PnL distribution histogram">
          {histReady && (
            <g>
              {[0, 1, 2].map((i) => {
                const gy = pad.t + (plotH / 2) * i;
                return (
                  <g key={i}>
                    <line x1={pad.l} x2={pad.l + plotW} y1={gy} y2={gy} className="gridline" />
                    <text x={4} y={gy + 4} style={{ fontSize: 10 }}>{max - (i * max) / 2}</text>
                  </g>
                );
              })}
              {bins.map((b, i) => {
                const h = plotH - (y(b.count) - pad.t);
                const w = Math.max(3, slot * 0.62);
                return (
                  <g key={i}>
                    <rect
                      x={pad.l + i * slot + (slot - w) / 2}
                      y={y(b.count)}
                      width={w}
                      height={Math.max(1, h)}
                      fill={b.wins ? GREEN : RED}
                      opacity={b.wins || b.losses ? 0.85 : 0.25}
                    />
                    <text x={pad.l + i * slot + slot / 2} y={pad.t + plotH + 13} textAnchor="middle" style={{ fontSize: 9 }}>
                      {b.from.toFixed(1)}
                    </text>
                    {b.count > 0 && (
                      <text x={pad.l + i * slot + slot / 2} y={y(b.count) - 4} textAnchor="middle" style={{ fontSize: 9 }}>
                        {b.count}
                      </text>
                    )}
                  </g>
                );
              })}
              <text x={pad.l} y={pad.t + plotH + 26} style={{ fontSize: 9 }}>PnL bucket lower bound</text>
            </g>
          )}
        </svg>
        <span className="chart-caption">PnL distribution · green = winning, red = losing</span>
      </div>
      <div className="winloss-split">
        {(['wins', 'losses'] as const).map((key) => {
          const rowsFor = rows.filter((t) => (key === 'wins' ? t.pnl > 0 : t.pnl < 0));
          const total = rowsFor.reduce((a, t) => a + t.pnl, 0);
          const share = rows.length ? (rowsFor.length / rows.length) * 100 : 0;
          return (
            <div key={key} className="winloss-card">
              <span>{key === 'wins' ? 'Winning trades' : 'Losing trades'}</span>
              <strong className={key === 'wins' ? 'good' : 'bad'}>{rowsFor.length}</strong>
              <small>{share.toFixed(2)}% of trades · total {fmt(total)}</small>
              <div className="split-bar">
                <i className={key === 'wins' ? 'good' : 'bad'} style={{ width: `${share}%` }} />
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/** Side-by-side LONG vs SHORT comparison. */
export function DirectionAnalysis({ rows }: { rows: readonly EnrichedTrade[] }) {
  const stats = useMemo(() => directionStats(rows), [rows]);
  const maxAbs = Math.max(1, ...stats.map((s) => Math.abs(s.totalPnl)));
  return (
    <div className="direction-analysis">
      <table className="direction-table">
        <thead>
          <tr>
            <th>Direction</th><th>Trades</th><th>Win rate</th><th>Avg PnL</th><th>Total PnL</th><th>Expectancy</th><th>Profit factor</th><th>Max loss streak</th>
          </tr>
        </thead>
        <tbody>
          {stats.map((s) => (
            <tr key={s.direction}>
              <td><span className={'tag ' + (s.direction === 'LONG' ? 'good' : 'bad')}>{s.direction}</span></td>
              <td>{s.trades}</td>
              <td>{s.winRate.toFixed(2)}%</td>
              <td className={s.avgPnl >= 0 ? 'good' : 'bad'}>{fmt(s.avgPnl)}</td>
              <td className={s.totalPnl >= 0 ? 'good' : 'bad'}>{fmt(s.totalPnl)}</td>
              <td className={s.expectancy >= 0 ? 'good' : 'bad'}>{fmt(s.expectancy)}</td>
              <td>{s.profitFactor.toFixed(2)}</td>
              <td>{s.maxLosingStreak}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="direction-bars">
        {stats.map((s) => (
          <div key={s.direction} className="direction-bar-row">
            <span>{s.direction}</span>
            <div className="direction-bar">
              <i
                className={s.totalPnl >= 0 ? 'good' : 'bad'}
                style={{ width: `${(Math.abs(s.totalPnl) / maxAbs) * 100}%` }}
              />
            </div>
            <strong className={s.totalPnl >= 0 ? 'good' : 'bad'}>{fmt(s.totalPnl)}</strong>
          </div>
        ))}
      </div>
    </div>
  );
}

/** Exit-reason mix. */
export function ExitAnalysis({ rows }: { rows: readonly EnrichedTrade[] }) {
  const stats = useMemo(() => exitStats(rows), [rows]);
  const color = (reason: string) => (reason === 'TP' ? GREEN : reason === 'SL' ? RED : BLUE);
  return (
    <div className="exit-analysis">
      {stats.map((s) => (
        <div key={s.reason} className="exit-row">
          <span className="exit-label">
            <i style={{ background: color(s.reason) }} />
            {s.reason === 'EOD' ? 'END_OF_DATA' : s.reason}
          </span>
          <div className="exit-bar">
            <i style={{ width: `${s.share}%`, background: color(s.reason) }} />
          </div>
          <strong>{s.share.toFixed(2)}%</strong>
          <small>{s.count} trades · {fmt(s.totalPnl)}</small>
        </div>
      ))}
    </div>
  );
}

/** Run Identity — shows the parameter snapshot of the displayed run. */
export function RunIdentity() {
  const state = useRun();
  if (state.status !== 'ready') return null;
  const run = state.run;
  return (
    <section className="run-identity panel">
      <div className="panel-head">
        <h2><TrendingUp size={16} />Run Identity</h2>
        <span className="tag neutral">{run.dataMode === 'seeded-demo' ? 'Seeded demo' : 'Live'}</span>
      </div>
      <dl className="run-identity-grid">
        <div><dt>Strategy</dt><dd>{run.strategy.name} · {run.strategy.version}</dd></div>
        <div><dt>Run ID</dt><dd>{run.runId}</dd></div>
        <div><dt>Run timestamp</dt><dd>{formatUtcMs(Date.parse(run.runTimestampUtc), { withSeconds: true })}</dd></div>
        <div className="run-identity-params">
          <dt>Parameters snapshot</dt>
          <dd>{Object.entries(run.strategy.parameters).map(([k, v]) => <span key={k}>{k}: {String(v)}</span>)}</dd>
        </div>
      </dl>
      <p className="run-identity-note">
        Parameter snapshot of the displayed run. Placeholder for multi-strategy comparison; no comparison logic is implemented yet.
      </p>
    </section>
  );
}

export type Metric = { label: string; value: string; note: string; tone?: 'good' | 'bad'; pending?: boolean; required?: string[] };

/** Compact labelled block used by the new research sections. Pending cells use the shared PendingMetric card. */
function Stat({ label, value, note, tone, pending, required }: Metric & { required?: string[] }) {
  if (pending) {
    return (
      <PendingMetric
        label={label}
        reason={note}
        required={required}
        compact
        status="Pending"
        showLabel={false}
        className="stat-pending"
      />
    );
  }
  return (
    <div className="stat-cell">
      <span>{label}</span>
      <strong className={tone ? `${tone}-text` : undefined}>{value}</strong>
      <small>{note}</small>
    </div>
  );
}

/** Stat grid with a caption; sections stay bounded instead of stretching. */
function StatGrid({ title, note, items }: { title: string; note?: string; items: Metric[] }) {
  return (
    <div className="stat-block">
      <div className="stat-block-head">
        <h3>{title}</h3>
        {note && <span className="meta">{note}</span>}
      </div>
      <div className="stat-cells">
        {items.map((m) => (
          <Stat key={m.label} {...m} />
        ))}
      </div>
    </div>
  );
}

const HOUR = (h: number) => `${String(h).padStart(2, '0')}:00`;
const MINS = (m: number | null) => (m === null ? '—' : m >= 60 ? `${Math.floor(m / 60)}h ${Math.round(m % 60)}m` : `${m.toFixed(0)}m`);

/** Return quality, downside risk, and capital efficiency in R units. */
export function PerformanceQuality({ rows }: { rows: readonly EnrichedTrade[] }) {
  const a = useMemo(() => advancedPerformance(rows), [rows]);
  const q = useMemo(() => tradeQuality(rows), [rows]);
  return (
    <div className="quality-sections">
      <StatGrid
        title="Return & Risk-Adjusted"
        note="account terms · not available for seeded demo"
        items={[
          { label: 'Total Return', value: 'Not available', note: a.reasons.totalReturnPct ?? 'No account balance series.', pending: true, required: ['Account currency', 'Balance series in account currency'] },
          { label: 'Annualized Return', value: 'Not available', note: a.reasons.annualizedReturnPct ?? 'Sample span too short for annualisation.', pending: true, required: ['Account return series', '>= 90 days and >= 30 observations'] },
          { label: 'Sortino Ratio', value: 'Not available', note: a.reasons.sortino ?? 'Standard Sortino requires periodic account returns.', pending: true, required: ['Periodic account returns', 'Target return'] },
          { label: 'Calmar Ratio', value: 'Not available', note: a.reasons.calmar ?? 'Calmar requires account-currency total return and percent drawdown.', pending: true, required: ['Account-currency total return', 'Account percent drawdown'] },
        ]}
      />
      <StatGrid
        title="Trade Quality"
        note="R = PnL / 5.0 reference stop · sizing not validated"
        items={[
          { label: 'Expectancy (R)', value: `${q.expectancyR.toFixed(2)}R`, note: 'Average PnL per trade in R units (price terms)', tone: q.expectancyR < 0 ? 'bad' : 'good' },
          { label: 'Average R Multiple', value: `${q.avgR.toFixed(2)}R`, note: 'Mean realized PnL divided by one R (price terms)', tone: q.avgR < 0 ? 'bad' : 'good' },
          {
            label: 'Average MAE',
            value: q.avgMAE === null ? 'N/A' : fmt(q.avgMAE),
            note: q.avgMAE === null
              ? 'Not available — the engine run does not supply observed adverse excursions.'
              : 'Mean worst adverse excursion while open · seeded replay path',
            pending: q.avgMAE === null,
            required: ['Observed MAE per trade from the engine'],
          },
          {
            label: 'Average MFE',
            value: q.avgMFE === null ? 'N/A' : fmt(q.avgMFE),
            note: q.avgMFE === null
              ? 'Not available — the engine run does not supply observed favorable excursions.'
              : 'Mean best favorable excursion while open · seeded replay path',
            pending: q.avgMFE === null,
            required: ['Observed MFE per trade from the engine'],
          },
          {
            label: 'MFE Capture',
            value: q.mfeCapturePct === null ? 'N/A' : `${q.mfeCapturePct.toFixed(2)}%`,
            note: q.mfeCapturePct === null
              ? 'Not available — requires observed favorable excursions for every trade.'
              : 'Realized PnL as a share of total favorable excursion',
            pending: q.mfeCapturePct === null,
            required: ['Observed MFE per trade from the engine'],
            tone: (q.mfeCapturePct ?? 0) >= 0 ? 'good' : 'bad',
          },
        ]}
      />
    </div>
  );
}

/** Drawdown depth, duration, and recovery across the equity curve. */
export function DrawdownQuality({ rows }: { rows: readonly EnrichedTrade[] }) {
  const d = useMemo(() => drawdownPeriods(rows), [rows]);
  return (
    <div className="quality-sections">
      <StatGrid
        title="Drawdown Profile"
        note={`${d.periods.length} underwater period${d.periods.length === 1 ? '' : 's'}`}
        items={[
          { label: 'Max Drawdown Duration', value: MINS(d.maxDurationMin), note: 'Longest stretch below a prior equity peak' },
          { label: 'Recovery Time', value: MINS(d.recoveryTimeMin), note: 'Deepest trough back to its prior peak' },
          { label: 'Peak Equity Date', value: d.peakEquityTime ? formatUtcMs(d.peakEquityTime) : '—', note: 'Equity peak preceding the deepest drawdown' },
          { label: 'Deepest Trough', value: d.deepest ? formatUtcMs(d.deepest.troughTime) : '—', note: 'Lowest equity point of the deepest period' },
        ]}
      />
      <div className="stat-block">
        <div className="stat-block-head">
          <h3>Drawdown Periods</h3>
          <span className="meta">closed-trade equity</span>
        </div>
        {d.periods.length === 0 ? (
          <p className="stat-empty">No underwater period: equity never closed below a prior peak.</p>
        ) : (
          <table className="period-table">
            <thead>
              <tr><th>Peak</th><th>Trough</th><th>Recovered</th><th>Depth</th><th>Length</th><th>Recovery</th></tr>
            </thead>
            <tbody>
              {d.periods.map((p, i) => (
                <tr key={i}>
                  <td>{formatUtcMs(p.startTime)}</td>
                  <td>{formatUtcMs(p.troughTime)}</td>
                  <td>{p.recoveredTime ? formatUtcMs(p.recoveredTime) : 'Open'}</td>
                  <td className="bad-text">{fmt(p.depth)}</td>
                  <td>{MINS(p.lengthMs / 60000)}</td>
                  <td>{MINS(p.recoveryMs === null ? null : p.recoveryMs / 60000)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

/** How many strategy signals converted into executed trades. */
export function SignalQuality({ rows, totalSignals }: { rows: readonly EnrichedTrade[]; totalSignals: number | null }) {
  const s = useMemo(() => signalAnalysis(rows, totalSignals), [rows, totalSignals]);
  const pct = (v: number) => (s.totalSignals ? ((v / s.totalSignals) * 100).toFixed(2) : 'N/A');
  const na = 'N/A';
  return (
    <StatGrid
      title="Signal Execution"
      note="from the run's archived execution metadata"
      items={[
        { label: 'Total Signals', value: s.totalSignals === null ? na : String(s.totalSignals), note: s.totalSignals === null ? 'Signal count not archived for this run' : '100.00% · signals received by the engine' },
        { label: 'Executed Signals', value: String(s.executed), note: s.totalSignals === null ? 'Trades executed by the engine' : `${pct(s.executed)}% of signals became trades`, tone: 'good' },
        { label: 'Ignored Signals', value: s.ignored === null ? na : String(s.ignored), note: s.ignored === null ? 'Not derivable without a signal count' : `${pct(s.ignored)}% had no qualifying entry` },
        { label: 'Execution Rate', value: s.executionRatePct === null ? na : `${s.executionRatePct.toFixed(2)}%`, note: 'Executed signals divided by total signals', tone: 'good' },
      ]}
    />
  );
}

/** Entries per UTC hour plus win rate for each bucket. */
export function EntryQuality({ rows }: { rows: readonly EnrichedTrade[] }) {
  const hours = useMemo(() => entriesByHour(rows), [rows]);
  const active = hours.filter((h) => h.trades > 0);
  const max = Math.max(1, ...active.map((h) => h.trades));
  return (
    <div className="stat-block">
      <div className="stat-block-head">
        <h3>Entries by Hour</h3>
        <span className="meta">UTC · {active.length} active hour{active.length === 1 ? '' : 's'}</span>
      </div>
      {active.length === 0 ? (
        <p className="stat-empty">No entries in this run.</p>
      ) : (
        <>
          <div className="hour-bars">
            {hours.map((h) => (
              <div className="hour-col" key={h.hour} title={`${HOUR(h.hour)} · ${h.trades} entries · ${h.wins} wins · ${fmt(h.totalPnl)}`}>
                <div className="hour-track">
                  <i className={h.totalPnl >= 0 ? 'good' : 'bad'} style={{ height: `${(h.trades / max) * 100}%` }} />
                </div>
                <span>{String(h.hour).padStart(2, '0')}</span>
              </div>
            ))}
          </div>
          <table className="period-table">
            <thead>
              <tr><th>Hour (UTC)</th><th>Entries</th><th>Wins</th><th>Win rate</th><th>Total PnL</th></tr>
            </thead>
            <tbody>
              {active.map((h) => (
                <tr key={h.hour}>
                  <td>{HOUR(h.hour)}</td>
                  <td>{h.trades}</td>
                  <td>{h.wins}</td>
                  <td>{((h.wins / h.trades) * 100).toFixed(2)}%</td>
                  <td className={h.totalPnl >= 0 ? 'good-text' : 'bad-text'}>{fmt(h.totalPnl)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}

/** Holding time and outcome quality per exit reason. */
export function ExitQualityTable({ rows }: { rows: readonly EnrichedTrade[] }) {
  const stats = useMemo(() => exitQuality(rows), [rows]);
  const total = rows.length || 1;
  return (
    <div className="stat-block">
      <div className="stat-block-head">
        <h3>Holding Time & Exit Efficiency</h3>
        <span className="meta">TP / SL / END_OF_DATA</span>
      </div>
      <table className="period-table">
        <thead>
          <tr><th>Exit</th><th>Count</th><th>Share</th><th>Avg hold</th><th>Total PnL</th><th>Efficiency</th></tr>
        </thead>
        <tbody>
          {stats.map((s) => (
            <tr key={s.reason}>
              <td><span className={'tag ' + (s.reason === 'TP' ? 'good' : s.reason === 'SL' ? 'bad' : 'neutral')}>{s.reason === 'EOD' ? 'END_OF_DATA' : s.reason}</span></td>
              <td>{s.count}</td>
              <td>{((s.count / total) * 100).toFixed(2)}%</td>
              <td>{MINS(s.avgHoldMin)}</td>
              <td className={s.totalPnl >= 0 ? 'good-text' : 'bad-text'}>{fmt(s.totalPnl)}</td>
              <td className={s.efficient ? 'good-text' : 'bad-text'}>
                {s.reason === 'TP' ? (s.efficient ? 'Target met' : 'Below target') : s.reason === 'SL' ? (s.efficient ? 'Capped at stop' : 'Beyond stop') : 'Data ended'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function ResearchAnalytics({ group = 'all' }: { group?: string }) {
  const state = useRun();
  const run = state.status === 'ready' ? state.run : null;
  const rows = useMemo(() => run ? enrichRunTrades(run) : [], [run]);
  const totalSignals = run?.execution?.signalsReceived ?? null;
  const p = useMemo(() => rows.length ? performance(rows) : { expectancy: 0, payoff: 0 }, [rows]);
  const r = useMemo(() => rows.length ? risk(rows) : { maxLosingStreak: 0, recoveryFactor: 0 }, [rows]);
  const show = (g: string) => group === 'all' || group === g;

  if (state.status !== 'ready') {
    return (
      <div className="panel">
        <div className="panel-head">
          <h2><BarChart3 size={16} />Research Analytics</h2>
        </div>
        <p className="report-note">
          {state.status === 'loading'
            ? 'Loading reference run…'
            : state.status === 'error'
              ? state.error
              : state.status === 'empty'
                ? 'No run selected — configure and run a real backtest.'
                : state.message}
        </p>
      </div>
    );
  }

  return (
    <>
      {show('Performance') && (
        <div className="research-kpis">
          {[
            ['Expectancy', fmt(p.expectancy), 'Average expected PnL per trade · price terms'],
            ['Payoff ratio', p.payoff.toFixed(2), 'Average win divided by average loss · price terms'],
            ['Max losing streak', String(r.maxLosingStreak), 'Longest run of consecutive losing trades'],
            ['Recovery factor', r.recoveryFactor.toFixed(2), 'Net PnL divided by maximum closed-trade drawdown · price terms'],
          ].map(([label, value, note]) => (
            <div key={label}>
              <span>{label}</span>
              <strong>{value}</strong>
              <small>{note}</small>
            </div>
          ))}
        </div>
      )}
      {show('Performance') && (
        <Section icon={<AreaChart size={16} />} title="Equity Analysis" note="closed-trade series · price terms">
          <EquityAnalysis rows={rows} />
        </Section>
      )}
      {show('Performance') && (
        <Section icon={<BarChart3 size={16} />} title="Trade Distribution" note="seeded trade set">
          <DistributionAnalysis rows={rows} />
        </Section>
      )}
      {show('Trades') && (
        <Section icon={<LineChart size={16} />} title="Direction Analysis" note="LONG vs SHORT">
          <DirectionAnalysis rows={rows} />
        </Section>
      )}
      {show('Trades') && (
        <Section icon={<PieChart size={16} />} title="Exit Analysis" note="TP / SL / END_OF_DATA mix">
          <ExitAnalysis rows={rows} />
        </Section>
      )}
      {show('Risk & Drawdown') && (
        <Section icon={<AreaChart size={16} />} title="Performance & Trade Quality" note="risk-adjusted returns · R multiples · excursions">
          <PerformanceQuality rows={rows} />
        </Section>
      )}
      {show('Risk & Drawdown') && (
        <Section icon={<TrendingUp size={16} />} title="Drawdown Analysis" note="depth · duration · recovery">
          <DrawdownQuality rows={rows} />
        </Section>
      )}
      {show('Execution') && (
        <Section icon={<LineChart size={16} />} title="Signal Analysis" note="generated vs executed">
          <SignalQuality rows={rows} totalSignals={totalSignals} />
        </Section>
      )}
      {show('Execution') && (
        <Section icon={<BarChart3 size={16} />} title="Entry Analysis" note="entries by hour · UTC">
          <EntryQuality rows={rows} />
        </Section>
      )}
      {show('Execution') && (
        <Section icon={<PieChart size={16} />} title="Exit Efficiency" note="holding time by exit reason">
          <ExitQualityTable rows={rows} />
        </Section>
      )}
    </>
  );
}