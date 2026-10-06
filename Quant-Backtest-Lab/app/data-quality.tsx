'use client';

import { Info } from 'lucide-react';
import { useRun } from './run-context';
import { PendingMetric } from './pending-metric';

/**
 * Dataset quality summary — driven by BacktestRun.dataQuality.
 * Demo runs render 'Demo / not measured' with no fabricated percentages.
 * Measured runs (archived Python engine results) render the archived counts.
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
  const measured = q.status === 'measured';
  const ticks = q.inRangeTicks;
  const count = (n: number | null | undefined) =>
    n === null || n === undefined ? 'Not measured' : n.toLocaleString('en-US');

  const fields: [string, string][] = measured
    ? [
        ['Dataset', run.dataSource.name],
        ['Total Ticks', count(q.totalTicks)],
        ['Ticks in Range', `${count(ticks)} · measured`],
        ['Candles', `${q.candles ?? '—'} · measured`],
        ['Signals', `${q.candles ?? '—'} · measured`],
        ['Executed Trades', String(run.metrics.trades)],
      ]
    : [
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
          <b>{measured ? 'Measured' : 'Demo / not measured'}</b>
        </div>
        <p className="demo-note">
          {measured
            ? 'Counts reported by the archived engine result.'
            : 'Awaiting data validation engine; no verified metrics in demo mode.'}
        </p>
      </div>

      <PendingMetric
        label="Modeling Quality"
        reason={measured
          ? 'Not supplied by the archived engine result. No percentage is synthesized.'
          : 'Not measured — the production definition comes from the real validation engine. No percentage is shown in demo mode.'}
        required={['One-minute data', 'Gap validation from the data-validation engine']}
        status="Not available"
        note={measured ? 'Not supplied by the run.' : 'No percentage is shown in demo mode.'}
      />

      <div className="baseline">
        <Info size={17} />
        <div>
          <strong>{measured ? 'Measured run' : 'Seeded reference'}</strong>
          <span>
            {measured
              ? 'Counts from the archived engine result'
              : 'Baseline match for the demo replay'}{' '}
            <span className="meta">· {measured ? 'measured' : 'demo'}</span>
          </span>
        </div>
      </div>
    </section>
  );
}
