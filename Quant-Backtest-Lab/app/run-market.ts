'use client';
/**
 * Run-scoped REAL market context for the UI (v1.0.2).
 *
 * Fetches real candles/ticks from the bridge for the CURRENTLY LOADED archived
 * run. The run is authoritative: the frontend never invents dataset/symbol/
 * timeframe. On failure the caller renders an explicit unavailable state — there
 * is no demo fallback in this module.
 */
import { useEffect, useRef, useState } from 'react';
import { getRunCandles, getRunTicks, type RunCandle, type RunTick } from './bridge-client';
import type { Candle, TickPoint, Trade } from './types';

export type MarketStatus = 'idle' | 'loading' | 'ready' | 'unavailable';

/** Timeframe label (bridge/data-engine form) → milliseconds per bar. */
export function timeframeMs(timeframe: string): number {
  const map: Record<string, number> = {
    '1min': 60_000,
    '5min': 300_000,
    '15min': 900_000,
    '30min': 1_800_000,
    '1h': 3_600_000,
    '4h': 14_400_000,
  };
  return map[timeframe] ?? 900_000;
}

export function toCandles(rows: readonly RunCandle[]): Candle[] {
  return rows.map((c) => ({
    t: Date.parse(c.timestamp),
    o: c.bid_open,
    h: c.bid_high,
    l: c.bid_low,
    c: c.bid_close,
  }));
}

export function toTicks(rows: readonly RunTick[]): TickPoint[] {
  return rows.map((t) => ({ t: Date.parse(t.timestamp), bid: t.bid, ask: t.ask, spread: t.spread }));
}

/** Small in-memory cache so re-selecting the same window does not refetch. */
const candleCache = new Map<string, Candle[]>();
const tickCache = new Map<string, TickPoint[]>();

/**
 * Real candles for the price chart.
 *
 * The window is primitive (start/end/limit) so the fetch keys stay stable and
 * no ref is written during render. A cache hit is derived during render (no
 * cascading effect); only the network callback sets state.
 */
export function useRunCandles(
  runId: string | null,
  start: string | null,
  end: string | null,
  limit: number | null,
): { candles: Candle[]; status: MarketStatus } {
  const key = runId ? `${runId}|${start ?? ''}|${end ?? ''}|${limit ?? ''}` : '';
  const cached = key ? candleCache.get(key) : undefined;
  const [fetched, setFetched] = useState<{ key: string; data: Candle[] } | null>(null);
  const reqRef = useRef(0);

  useEffect(() => {
    if (!runId || cached !== undefined) return;
    let alive = true;
    const req = ++reqRef.current;
    const opts: { start?: string; end?: string; limit?: number } = {};
    if (start) opts.start = start;
    if (end) opts.end = end;
    if (limit) opts.limit = limit;
    getRunCandles(runId, opts)
      .then((res) => {
        if (!alive || req !== reqRef.current) return;
        const mapped = toCandles(res.candles);
        candleCache.set(key, mapped);
        setFetched({ key, data: mapped });
      })
      .catch(() => {
        if (!alive || req !== reqRef.current) return;
        candleCache.set(key, []);
        setFetched({ key, data: [] });
      });
    return () => {
      alive = false;
    };
  }, [runId, key, start, end, limit, cached]);

  const candles = fetched && fetched.key === key ? fetched.data : (cached ?? []);
  let status: MarketStatus = 'idle';
  if (runId) {
    if (candles.length) status = 'ready';
    else if (fetched?.key === key || cached !== undefined) status = 'unavailable';
    else status = 'loading';
  }
  return { candles, status };
}

/** Real BID/ASK ticks for the selected trade window (`around` a fill or a range). */
export function useRunTicks(
  runId: string | null,
  around: string | null,
  start: string | null,
  end: string | null,
  beforeMs?: number,
  afterMs?: number,
  limit = 400,
): { ticks: TickPoint[]; status: MarketStatus } {
  const key = runId
    ? `${runId}|${around ?? ''}|${start ?? ''}|${end ?? ''}|${beforeMs ?? ''}|${afterMs ?? ''}|${limit}`
    : '';
  const cached = key ? tickCache.get(key) : undefined;
  const [fetched, setFetched] = useState<{ key: string; data: TickPoint[] } | null>(null);
  const reqRef = useRef(0);

  useEffect(() => {
    if (!runId || cached !== undefined) return;
    if (!around && !start && !end) return;
    let alive = true;
    const req = ++reqRef.current;
    const opts: {
      around?: string;
      start?: string;
      end?: string;
      beforeMs?: number;
      afterMs?: number;
      limit?: number;
    } = { limit };
    if (around) opts.around = around;
    if (start) opts.start = start;
    if (end) opts.end = end;
    if (around && beforeMs) opts.beforeMs = beforeMs;
    if (around && afterMs) opts.afterMs = afterMs;
    getRunTicks(runId, opts)
      .then((res) => {
        if (!alive || req !== reqRef.current) return;
        const mapped = toTicks(res.ticks);
        tickCache.set(key, mapped);
        setFetched({ key, data: mapped });
      })
      .catch(() => {
        if (!alive || req !== reqRef.current) return;
        tickCache.set(key, []);
        setFetched({ key, data: [] });
      });
    return () => {
      alive = false;
    };
  }, [runId, key, around, start, end, beforeMs, afterMs, limit, cached]);

  const ticks = fetched && fetched.key === key ? fetched.data : (cached ?? []);
  let status: MarketStatus = 'idle';
  if (runId && (around || start || end)) {
    if (ticks.length) status = 'ready';
    else if (fetched?.key === key || cached !== undefined) status = 'unavailable';
    else status = 'loading';
  }
  return { ticks, status };
}

/**
 * Window of real candles that brackets one trade: `bars` bars before the signal
 * candle through `bars` bars after the exit, using the run's own timeframe step.
 */
export function tradeCandleWindow(trade: Trade, timeframe: string, bars = 12): { start: string; end: string; limit: number } {
  const step = timeframeMs(timeframe);
  const start = new Date(trade.signal - bars * step).toISOString();
  const end = new Date(trade.exitTime + bars * step).toISOString();
  return { start, end, limit: bars * 2 + Math.ceil((trade.exitTime - trade.signal) / step) + 4 };
}
