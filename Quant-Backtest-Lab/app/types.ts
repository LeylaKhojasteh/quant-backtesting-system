/**
 * Shared visualization types.
 *
 * These are neutral UI types used by the archived-run path. Market/execution
 * data is never defined here — only the shape the charts and inspector consume.
 */

/** A closed trade projected for charts/inspector from an archived run. */
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
  reason: 'SL' | 'TP' | 'TSL' | 'BE' | 'TIME' | 'EOD' | 'STRATEGY';
  spreadIn: number;
  spreadOut: number;
  /** Absent levels stay null; views must never fall back to entry price. */
  sl: number | null;
  tp: number | null;
  /** Engine-measured excursions (price terms): MAE <= 0, MFE >= 0. */
  mae?: number | null;
  mfe?: number | null;
  /** Instrument decimal precision, when archived. */
  digits?: number | null;
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
