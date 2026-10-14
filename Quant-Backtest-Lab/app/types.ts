/**
 * Shared visualization types.
 *
 * These are neutral UI types used by both the real archived-run path and the
 * explicit `?fixture=demo` path. Market/execution data is never defined here —
 * only the shape the charts and inspector consume.
 */

/** A closed trade projected for charts/inspector (real run trades + fixture). */
export type Trade = {
  id: number;
  direction: 'LONG' | 'SHORT';
  signal: number;
  /** Real signal-availability time; never recomputed from a fixed timeframe. */
  signalAvailable?: number;
  entryTime: number;
  exitTime: number;
  entry: number;
  exit: number;
  pnl: number;
  reason: 'SL' | 'TP' | 'EOD';
  spreadIn: number;
  spreadOut: number;
  sl: number;
  tp: number;
};

/** A real candle from the run's market context (BID OHLC for the chart body). */
export type Candle = { t: number; o: number; h: number; l: number; c: number };

/** A real BID/ASK tick from the run's market context. */
export type TickPoint = {
  t: number;
  bid: number;
  ask: number;
  spread: number;
  event?: string;
};
