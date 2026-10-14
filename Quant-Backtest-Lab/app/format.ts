/** Neutral display formatters shared by real-run and fixture views. */
export const fmt = (n: number) =>
  n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export const time = (n: number, full = false) =>
  new Date(n).toISOString().slice(full ? 0 : 11, 23).replace('T', ' ');

/** Short timeframe label: data-engine form ('1h') -> display form ('H1'). */
export const timeframeLabel = (tf: string) =>
  ({
    '1min': 'M1',
    '5min': 'M5',
    '15min': 'M15',
    '30min': 'M30',
    '1h': 'H1',
    '4h': 'H4',
  })[tf] ?? tf;

/** Date-only UTC range for the results header: `2025-01-01 → 2025-03-31 UTC`. */
export const utcDateRange = (startIso: string, endIso: string) =>
  `${String(startIso).slice(0, 10)} → ${String(endIso).slice(0, 10)} UTC`;
