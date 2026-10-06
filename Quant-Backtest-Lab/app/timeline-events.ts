/**
 * Trade-timeline event derivation — pure logic, no React.
 *
 * Alias-free `.ts` module so the Node test runner can bundle it.
 * Every event carries a UTC timestamp, a title, a short note, the related
 * price side (BID/ASK) and price where they exist, and a semantic tone.
 * Nothing is invented: SL/TP init is marked "Not supplied by run" when the
 * trade carries no SL/TP.
 */
import { fmt } from './demo';
import { formatUtcMs } from './utc';

export type TimelineTone = 'signal' | 'entry' | 'tp' | 'sl' | 'eod' | 'neutral';

export type TimelineTrade = {
  id: number;
  direction: 'LONG' | 'SHORT';
  /** Epoch-ms timestamps (demo adapter) — converted to UTC ISO for display. */
  signal: number;
  entryTime: number;
  exitTime: number;
  entry: number;
  exit: number;
  sl?: number;
  tp?: number;
  reason: 'TP' | 'SL' | 'EOD';
};

export type TimelineEvent = {
  key: string;
  title: string;
  note: string;
  timestampMs: number;
  /** Human-readable UTC instant, always tagged UTC. */
  timestampUtc: string;
  side?: 'BID' | 'ASK';
  price?: number;
  tone: TimelineTone;
  /** True when the run did not supply the data for this event. */
  missing?: boolean;
};

const utc = (ms: number) => formatUtcMs(ms, { withDate: true, withSeconds: true });

function exitTone(reason: TimelineTrade['reason']): TimelineTone {
  return reason === 'TP' ? 'tp' : reason === 'SL' ? 'sl' : 'eod';
}

/**
 * Builds the execution lifecycle of one trade from that trade's own data:
 * Signal → Signal available → Eligible tick → Entry → SL/TP initialized →
 * Exit trigger → Exit.
 */
export function tradeTimelineEvents(trade: TimelineTrade): TimelineEvent[] {
  const long = trade.direction === 'LONG';
  const entrySide = (long ? 'ASK' : 'BID') as 'BID' | 'ASK';
  const exitSide = (long ? 'BID' : 'ASK') as 'BID' | 'ASK';
  const tone = exitTone(trade.reason);
  const reasonLabel = trade.reason === 'TP' ? 'Take profit' : trade.reason === 'SL' ? 'Stop loss' : 'End of data';

  const sltp: TimelineEvent =
    trade.sl === undefined || trade.tp === undefined
      ? {
          key: 'sltp',
          title: 'SL / TP initialized',
          note: 'Not supplied by run — the run carries no SL/TP for this trade.',
          timestampMs: trade.entryTime,
          timestampUtc: utc(trade.entryTime),
          tone: 'neutral',
          missing: true,
        }
      : {
          key: 'sltp',
          title: 'SL / TP initialized',
          note: `Stop ${fmt(trade.sl)} / Take profit ${fmt(trade.tp)} armed at entry.`,
          timestampMs: trade.entryTime,
          timestampUtc: utc(trade.entryTime),
          tone: 'neutral',
        };

  return [
    {
      key: 'signal',
      title: 'Signal',
      note: `Signal candle opened · ${trade.direction} bias not yet confirmed.`,
      timestampMs: trade.signal,
      timestampUtc: utc(trade.signal),
      tone: 'signal',
    },
    {
      key: 'available',
      title: 'Signal available',
      note: `Candle closed · ${trade.direction} direction confirmed, tradable from here.`,
      timestampMs: trade.signal + 900000,
      timestampUtc: utc(trade.signal + 900000),
      tone: 'signal',
    },
    {
      key: 'eligible',
      title: 'Eligible tick',
      note: `First eligible ${entrySide} tick observed for ${trade.direction}.`,
      timestampMs: trade.entryTime,
      timestampUtc: utc(trade.entryTime),
      side: entrySide,
      price: trade.entry,
      tone: 'neutral',
    },
    {
      key: 'entry',
      title: `Entry · ${trade.direction}`,
      note: `Filled at first eligible tick · ${entrySide} ${fmt(trade.entry)}.`,
      timestampMs: trade.entryTime,
      timestampUtc: utc(trade.entryTime),
      side: entrySide,
      price: trade.entry,
      tone: 'entry',
    },
    sltp,
    {
      key: 'trigger',
      title: `Exit trigger · ${trade.reason}`,
      note: `${reasonLabel} observed · ${exitSide} ${fmt(trade.exit)}.`,
      timestampMs: trade.exitTime,
      timestampUtc: utc(trade.exitTime),
      side: exitSide,
      price: trade.exit,
      tone,
    },
    {
      key: 'exit',
      title: `Exit · ${trade.reason}`,
      note: `Trade closed · PnL in price terms, ${exitSide} ${fmt(trade.exit)}.`,
      timestampMs: trade.exitTime,
      timestampUtc: utc(trade.exitTime),
      side: exitSide,
      price: trade.exit,
      tone,
    },
  ];
}
