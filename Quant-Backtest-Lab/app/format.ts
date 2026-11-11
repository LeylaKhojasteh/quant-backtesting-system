/** Neutral display formatters shared by run views. */
export const fmt = (n: number) =>
  n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

/** Price levels adapt to the instrument's magnitude when metadata is absent. */
export const fmtPrice = (n: number, instrumentDigits?: number | null) => {
  const abs = Math.abs(n);
  const digits = instrumentDigits != null && Number.isInteger(instrumentDigits) && instrumentDigits >= 0
    ? instrumentDigits
    : abs >= 1000 ? 2 : abs >= 100 ? 3 : abs >= 1 ? 4 : abs >= 0.01 ? 5 : 8;
  return n.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits });
};

/** Price-term PnL / excursions: metadata digits when known, else a safe heuristic. */
export const fmtPnl = (n: number, instrumentDigits?: number | null) => {
  const abs = Math.abs(n);
  const digits = instrumentDigits != null && Number.isInteger(instrumentDigits) && instrumentDigits >= 0
    ? instrumentDigits
    : abs >= 1000 ? 2 : abs >= 1 ? 2 : abs >= 0.01 ? 4 : 6;
  return n.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits });
};

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
