'use client';

import { Info } from 'lucide-react';
import { useRun } from './run-context';

/**
 * Dataset quality summary — driven by BacktestRun.dataQuality.
 * In demo mode, status is 'Demo / not measured'; no fabricated percentages.
 */
export function DataQualityPanel() {
  const state = useRun();
  if (state.status !== 'ready') {
    return (
      <section className="data-quality">
        <p className="demo-note">Loading reference run…</p>
      </section>
    );
  }
  const run = state.run;
  const q = run.dataQuality;
  const ticks = q.inRangeTicks;

  const fields: [string, string][] = [
    ['Dataset', run.dataSource.name],
    ['Total Ticks', 'Not measured'],
    ['Ticks in Range', ticks === null || ticks === undefined ? 'Not measured' : `${ticks.toLocaleString('en-US')} · seeded example`],
    ['Candles', `${q.candles ?? '—'} · seeded`],
    ['Signals', `${q.candles ?? '—'} · seeded`],
    ['Executed Trades', String(run.metrics.trades)],
  ];

  return (
    <section className="data-quality">
      <dl className="data-quality-grid">
        {fields.map(([k, v]) => (
          <div key={k}>
            <dt>{k}</dt>
            <dd>{v}</dd>
          </div>
        ))}
      </dl>

      <div className="quality quality-prominent">
        <div>
          <span>Dataset Status <Info size={12} /></span>
          <b>Demo / not measured</b>
        </div>
        <p className="demo-note">Awaiting data validation engine; no verified metrics in demo mode.</p>
      </div>

      <div className="quality quality-prominent">
        <div>
          <span>Modeling Quality</span>
          <b>Not measured</b>
        </div>
        <p className="demo-note">No percentage is shown in demo mode.</p>
      </div>

      <div className="baseline">
        <Info size={17} />
        <div>
          <strong>Seeded reference</strong>
          <span>Baseline match for the demo replay <span className="meta">· demo</span></span>
        </div>
      </div>
    </section>
  );
}