'use client';

import { fmtPnl, fmtPrice, time } from './format';
import type { Trade } from './types';

type Phase = { key: string; label: string; at: number; note: string };

/**
 * Compact Signal -> Available -> Entry -> Exit ribbon shown above the
 * candle-context chart so the ordering of events is obvious at a glance.
 */
export function EventRibbon({ trade }: { trade: Trade }) {
  const phases: Phase[] = [
    { key: 'signal', label: 'Signal', at: trade.signal, note: 'Candle opened · direction not yet known' },
    { key: 'available', label: 'Available', at: trade.signal + 900000, note: 'Candle closed · signal becomes tradable' },
    { key: 'entry', label: 'Entry', at: trade.entryTime, note: `First eligible tick · ${trade.direction === 'LONG' ? 'ASK' : 'BID'} ${fmtPrice(trade.entry,trade.digits)}` },
    { key: 'exit', label: 'Exit', at: trade.exitTime, note: `${trade.reason} · ${trade.direction === 'LONG' ? 'BID' : 'ASK'} ${fmtPrice(trade.exit,trade.digits)}` },
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
      {trade.mae != null && trade.mfe != null && (
        <div className="ribbon-excursions">
          <span className="ribbon-excursion">
            MAE <strong>{fmtPnl(trade.mae,trade.digits)}</strong>
          </span>
          <span className="ribbon-excursion">
            MFE <strong>{fmtPnl(trade.mfe,trade.digits)}</strong>
          </span>
        </div>
      )}
    </div>
  );
}