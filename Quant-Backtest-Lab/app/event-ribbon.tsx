'use client';

import { fmt, time, type Trade } from './demo';
import type { Excursion } from './analytics';

type Phase = { key: string; label: string; at: number; note: string };

/**
 * Compact Signal -> Available -> Entry -> Exit ribbon shown above the
 * candle-context chart so the ordering of events is obvious at a glance.
 */
export function EventRibbon({ trade, excursion }: { trade: Trade; excursion?: Excursion }) {
  const phases: Phase[] = [
    { key: 'signal', label: 'Signal', at: trade.signal, note: 'Candle opened · direction not yet known' },
    { key: 'available', label: 'Available', at: trade.signal + 900000, note: 'Candle closed · signal becomes tradable' },
    { key: 'entry', label: 'Entry', at: trade.entryTime, note: `First eligible tick · ${trade.direction === 'LONG' ? 'ASK' : 'BID'} ${fmt(trade.entry)}` },
    { key: 'exit', label: 'Exit', at: trade.exitTime, note: `${trade.reason} · ${trade.direction === 'LONG' ? 'BID' : 'ASK'} ${fmt(trade.exit)}` },
  ];

  return (
    <div className="event-ribbon" aria-label="Trade event sequence">
      {phases.map((p, i) => (
        <div key={p.key} className="ribbon-step">
          <div className="ribbon-node">
            <span className="ribbon-dot" aria-hidden />
            <div className="ribbon-text">
              <span className="ribbon-label">{p.label}</span>
              <strong>{time(p.at, true).slice(11)}</strong>
              <small>{p.note}</small>
            </div>
          </div>
          {i < phases.length - 1 && <span className="ribbon-arrow" aria-hidden>→</span>}
        </div>
      ))}
      {excursion && (
        <div className="ribbon-excursions">
          <span className="ribbon-excursion bad">
            MAE <strong>{fmt(excursion.mae)}</strong>
            <small>{time(excursion.maeTime).slice(0, 8)}</small>
          </span>
          <span className="ribbon-excursion good">
            MFE <strong>{fmt(excursion.mfe)}</strong>
            <small>{time(excursion.mfeTime).slice(0, 8)}</small>
          </span>
        </div>
      )}
    </div>
  );
}