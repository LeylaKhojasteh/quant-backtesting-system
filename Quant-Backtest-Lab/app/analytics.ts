// Derived analytics over the seeded trade set.
// Every value here is computed from `trades`; nothing is hard-coded.
// MAE/MFE come from the seeded replay path (see demo.tradePath) and are labelled
// as seeded in the UI until the Python engine supplies observed excursions.
import { candles, trades, tradePath, type Trade } from './demo';

export type Excursion = { mae: number; mfe: number; maeTime: number; mfeTime: number };

export type EnrichedTrade = Trade & Excursion & { durationMin: number };

/** Worst adverse and best favorable unrealized move while the trade was open. */
export function excursions(t: Trade): Excursion {
  const path = tradePath(t);
  const long = t.direction === 'LONG';
  let worst = 0;
  let best = 0;
  let worstAt = t.entryTime;
  let bestAt = t.entryTime;
  for (const p of path) {
    // adverse side is the exit side: BID for LONG, ASK for SHORT
    const adverseMove = long ? t.entry - p.bid : p.ask - t.entry;
    const favorableMove = long ? p.bid - t.entry : t.entry - p.ask;
    if (adverseMove > worst) {
      worst = adverseMove;
      worstAt = p.t;
    }
    if (favorableMove > best) {
      best = favorableMove;
      bestAt = p.t;
    }
  }
  return { mae: round4(worst), mfe: round4(best), maeTime: worstAt, mfeTime: bestAt };
}

export function enrich(list: readonly Trade[]): EnrichedTrade[] {
  return list.map((t) => ({
    ...t,
    ...excursions(t),
    durationMin: (t.exitTime - t.entryTime) / 60000,
  }));
}

function round4(n: number) {
  return Math.round(n * 1e4) / 1e4;
}
function sum(v: readonly number[]) {
  return v.reduce((a, b) => a + b, 0);
}
function mean(v: readonly number[]) {
  return v.length ? sum(v) / v.length : 0;
}
/** Sample standard deviation; used for trade-level dispersion only. */
function stdev(v: readonly number[]) {
  if (v.length < 2) return 0;
  const m = mean(v);
  return Math.sqrt(sum(v.map((x) => (x - m) ** 2)) / (v.length - 1));
}

/** Demo account base the balance preview is measured against. */
export const START_BALANCE = 10000;

// ---------------------------------------------------------------------------
// Performance metrics
// ---------------------------------------------------------------------------

export type PerformanceMetrics = {
  trades: number;
  wins: number;
  losses: number;
  breakeven: number;
  winRate: number;
  netPnl: number;
  avgPnl: number;
  expectancy: number;
  avgWin: number;
  avgLoss: number;
  payoff: number;
  grossProfit: number;
  grossLoss: number;
  profitFactor: number;
  /** Net PnL against the demo account base, in percent. */
  returnPct: number;
};

export function performance(list: readonly EnrichedTrade[]): PerformanceMetrics {
  const pnls = list.map((t) => t.pnl);
  const wins = list.filter((t) => t.pnl > 0);
  const losses = list.filter((t) => t.pnl < 0);
  const breakeven = list.length - wins.length - losses.length;
  const grossProfit = sum(wins.map((t) => t.pnl));
  const grossLoss = sum(losses.map((t) => t.pnl));
  const avgWin = mean(wins.map((t) => t.pnl));
  const avgLoss = mean(losses.map((t) => t.pnl));
  const netPnl = sum(pnls);
  return {
    trades: list.length,
    wins: wins.length,
    losses: losses.length,
    breakeven,
    winRate: list.length ? (wins.length / list.length) * 100 : 0,
    netPnl,
    avgPnl: mean(pnls),
    expectancy: mean(pnls),
    avgWin,
    avgLoss,
    payoff: avgLoss === 0 ? 0 : avgWin / Math.abs(avgLoss),
    grossProfit,
    grossLoss,
    profitFactor: grossLoss === 0 ? 0 : grossProfit / Math.abs(grossLoss),
    returnPct: (netPnl / START_BALANCE) * 100,
  };
}

/** Reference stop distance in price terms; one R unit. */
export const R_UNIT = 5;

/** Total wall-clock span covered by the closed trades, in milliseconds. */
export function tradingSpanMs(list: readonly EnrichedTrade[]) {
  if (!list.length) return 0;
  return Math.max(...list.map((t) => t.exitTime)) - Math.min(...list.map((t) => t.entryTime));
}

export type AdvancedPerformance = {
  /** Net PnL against the demo account base, in percent. */
  totalReturnPct: number;
  /**
   * Naive annualization of `totalReturnPct` over the observed span. The demo
   * covers a single 22h session, so this is arithmetic only, not a forecast.
   */
  annualizedReturnPct: number;
  /** Downside-deviation ratio: mean PnL / stdev of losing PnL only. */
  sortino: number;
  /** Total return divided by maximum drawdown. */
  calmar: number;
};

/**
 * Annualization is only meaningful over a long sample. The seeded run spans
 * hours, so callers must label these values as indicative, not expected.
 */
export function advancedPerformance(list: readonly EnrichedTrade[]): AdvancedPerformance {
  const p = performance(list);
  const r = risk(list);
  const span = tradingSpanMs(list);
  const years = span > 0 ? span / (365.25 * 24 * 3600 * 1000) : 0;
  const downside = list.filter((t) => t.pnl < 0).map((t) => t.pnl);
  const dev = downside.length > 1 ? Math.sqrt(sum(downside.map((x) => x * x)) / (downside.length - 1)) : 0;
  const growth = years > 0 ? Math.pow(1 + p.returnPct / 100, 1 / years) - 1 : 0;
  return {
    totalReturnPct: p.returnPct,
    annualizedReturnPct: growth * 100,
    sortino: dev === 0 ? 0 : p.expectancy / dev,
    calmar: r.maxDrawdown === 0 ? 0 : p.returnPct / r.maxDrawdown,
  };
}

export type DrawdownPeriod = {
  startTime: number;
  troughTime: number;
  recoveredTime: number | null;
  depth: number;
  lengthMs: number;
  recoveryMs: number | null;
};

export type DrawdownAnalysis = {
  periods: DrawdownPeriod[];
  /** Longest time spent below a previous equity peak, in minutes. */
  maxDurationMin: number;
  /** Minutes from the deepest trough back to the prior peak. */
  recoveryTimeMin: number | null;
  /** Timestamp of the equity peak preceding the deepest drawdown. */
  peakEquityTime: number | null;
  deepest: DrawdownPeriod | null;
};

/** Contiguous underwater stretches of the closed-trade equity curve. */
export function drawdownPeriods(list: readonly EnrichedTrade[]): DrawdownAnalysis {
  const periods: DrawdownPeriod[] = [];
  if (!list.length) {
    return { periods, maxDurationMin: 0, recoveryTimeMin: null, peakEquityTime: null, deepest: null };
  }
  let peakPnl = 0;
  let peakTime = list[0].exitTime;
  let open: DrawdownPeriod | null = null;

  for (const t of list) {
    const equity = sum(list.filter((x) => x.exitTime <= t.exitTime).map((x) => x.pnl));
    if (equity >= peakPnl) {
      if (open) {
        open.recoveredTime = t.exitTime;
        open.recoveryMs = t.exitTime - open.troughTime;
        open = null;
      }
      peakPnl = equity;
      peakTime = t.exitTime;
      continue;
    }
    const depth = peakPnl - equity;
    if (!open) {
      open = {
        startTime: peakTime,
        troughTime: t.exitTime,
        recoveredTime: null,
        depth,
        lengthMs: t.exitTime - peakTime,
        recoveryMs: null,
      };
      periods.push(open);
    } else {
      open.depth = Math.max(open.depth, depth);
      open.troughTime = t.exitTime;
      open.lengthMs = t.exitTime - open.startTime;
    }
  }

  const deepest = periods.reduce<DrawdownPeriod | null>(
    (worst, p) => (!worst || p.depth > worst.depth ? p : worst),
    null,
  );
  const maxDurationMin = periods.length ? Math.max(...periods.map((p) => p.lengthMs)) / 60000 : 0;
  const recoveryTimeMin = deepest
    ? deepest.recoveredTime !== null
      ? (deepest.recoveredTime - deepest.troughTime) / 60000
      : (list[list.length - 1].exitTime - deepest.troughTime) / 60000
    : null;

  return {
    periods,
    maxDurationMin,
    recoveryTimeMin,
    peakEquityTime: deepest ? deepest.startTime : null,
    deepest,
  };
}

export type TradeQuality = {
  /** Expectancy expressed in R units (PnL divided by the reference stop). */
  expectancyR: number;
  avgR: number;
  avgMAE: number;
  avgMFE: number;
  /**
   * Realized PnL as a share of the best favorable excursion. Only meaningful
   * for trades that reached a positive MFE.
   */
  mfeCapturePct: number;
};

export function tradeQuality(list: readonly EnrichedTrade[]): TradeQuality {
  const withMfe = list.filter((t) => t.mfe > 0);
  return {
    expectancyR: mean(list.map((t) => t.pnl / R_UNIT)),
    avgR: mean(list.map((t) => t.pnl / R_UNIT)),
    avgMAE: mean(list.map((t) => t.mae)),
    avgMFE: mean(list.map((t) => t.mfe)),
    mfeCapturePct: withMfe.length ? (sum(withMfe.map((t) => t.pnl)) / sum(withMfe.map((t) => t.mfe))) * 100 : 0,
  };
}

export type SignalAnalysis = {
  totalSignals: number;
  executed: number;
  ignored: number;
  /** Share of strategy signals that became an executed trade. */
  executionRatePct: number;
};

/**
 * Signal accounting. Every strategy candle produces a directional signal; a
 * signal becomes a trade only when entry conditions and price are satisfied.
 */
export function signalAnalysis(
  list: readonly EnrichedTrade[],
  totalSignals = totalSignalCount(),
): SignalAnalysis {
  const executed = list.length;
  return {
    totalSignals,
    executed,
    ignored: Math.max(0, totalSignals - executed),
    executionRatePct: totalSignals > 0 ? (executed / totalSignals) * 100 : 0,
  };
}

/** Strategy signals available in the seeded window: one per M15 candle. */
export function totalSignalCount() {
  return candles.length;
}

export type HourBucket = { hour: number; trades: number; wins: number; totalPnl: number };

/** Entries per UTC hour of day. */
export function entriesByHour(list: readonly EnrichedTrade[]): HourBucket[] {
  return Array.from({ length: 24 }, (_, hour) => {
    const rows = list.filter((t) => new Date(t.entryTime).getUTCHours() === hour);
    return {
      hour,
      trades: rows.length,
      wins: rows.filter((t) => t.pnl > 0).length,
      totalPnl: sum(rows.map((t) => t.pnl)),
    };
  });
}

export type ExitQuality = {
  reason: string;
  count: number;
  avgHoldMin: number;
  totalPnl: number;
  /** True when the exit closed the trade in profit. */
  efficient: boolean;
};

/** Holding time and outcome per exit reason, used for TP / SL efficiency. */
export function exitQuality(list: readonly EnrichedTrade[]): ExitQuality[] {
  return (['TP', 'SL', 'EOD'] as const)
    .map((reason) => {
      const rows = list.filter((t) => t.reason === reason);
      const totalPnl = sum(rows.map((t) => t.pnl));
      return {
        reason,
        count: rows.length,
        avgHoldMin: rows.length ? mean(rows.map((t) => t.durationMin)) : 0,
        totalPnl,
        // A take profit that closed negative is not efficient; a stop loss
        // capped near the reference stop is.
        efficient: reason === 'TP' ? totalPnl > 0 : reason === 'SL' ? totalPnl < 0 : true,
      };
    })
    .filter((r) => r.count > 0);
}

export type Streak = { win: boolean; count: number; pnl: number };

export function streaks(list: readonly EnrichedTrade[]): Streak[] {
  const out: Streak[] = [];
  for (const t of list) {
    const win = t.pnl > 0;
    const last = out[out.length - 1];
    if (last && last.win === win) {
      last.count += 1;
      last.pnl += t.pnl;
    } else {
      out.push({ win, count: 1, pnl: t.pnl });
    }
  }
  return out;
}

export type RiskMetrics = {
  maxDrawdown: number;
  maxDrawdownPct: number;
  maxLosingStreak: number;
  maxWinningStreak: number;
  recoveryFactor: number;
  worstTrade: number;
  bestTrade: number;
  avgDurationMin: number;
  maxDurationMin: number;
  avgSpreadIn: number;
  avgSpreadOut: number;
  /** Sample standard deviation of per-trade PnL, in price terms. */
  volatility: number;
  /** Mean per-trade PnL divided by its standard deviation. Not annualized. */
  sharpe: number;
};

export function risk(list: readonly EnrichedTrade[]): RiskMetrics {
  const dd = drawdownSeries(list);
  const maxDrawdown = dd.length ? Math.max(...dd) : 0;
  const st = streaks(list);
  const lossRuns = st.filter((s) => !s.win);
  const winRuns = st.filter((s) => s.win);
  const pnls = list.map((t) => t.pnl);
  const volatility = stdev(pnls);
  return {
    maxDrawdown,
    maxDrawdownPct: maxDrawdown,
    maxLosingStreak: lossRuns.length ? Math.max(...lossRuns.map((s) => s.count)) : 0,
    maxWinningStreak: winRuns.length ? Math.max(...winRuns.map((s) => s.count)) : 0,
    recoveryFactor: maxDrawdown === 0 ? 0 : sum(pnls) / maxDrawdown,
    worstTrade: pnls.length ? Math.min(...pnls) : 0,
    bestTrade: pnls.length ? Math.max(...pnls) : 0,
    avgDurationMin: mean(list.map((t) => t.durationMin)),
    maxDurationMin: list.length ? Math.max(...list.map((t) => t.durationMin)) : 0,
    avgSpreadIn: mean(list.map((t) => t.spreadIn)),
    avgSpreadOut: mean(list.map((t) => t.spreadOut)),
    volatility,
    sharpe: volatility === 0 ? 0 : mean(pnls) / volatility,
  };
}

// ---------------------------------------------------------------------------
// Series
// ---------------------------------------------------------------------------

/** Cumulative closed-trade PnL, one point per trade. */
export function equitySeries(list: readonly EnrichedTrade[]) {
  let run = 0;
  return list.map((t) => (run += t.pnl));
}

/** Peak-to-trough decline of the closed-trade equity curve. */
export function drawdownSeries(list: readonly EnrichedTrade[]) {
  let peak = 0;
  return equitySeries(list).map((eq) => {
    peak = Math.max(peak, eq);
    return peak - eq;
  });
}

/** Demo account equity: the start balance plus the closed-trade PnL series. */
export function balanceSeries(list: readonly EnrichedTrade[], start = START_BALANCE) {
  return equitySeries(list).map((v) => start + v);
}

/** Account-level summary for the equity panel footer. */
export function equitySummary(list: readonly EnrichedTrade[], start = START_BALANCE) {
  const balances = balanceSeries(list, start);
  const final = balances.length ? balances[balances.length - 1] : start;
  const peak = balances.length ? Math.max(...balances, start) : start;
  const lowest = balances.length ? Math.min(...balances, start) : start;
  return {
    start,
    final,
    peak,
    lowest,
    netChange: final - start,
    totalReturn: ((final - start) / start) * 100,
  };
}

export function pnlHistogram(list: readonly EnrichedTrade[], binCount = 9) {
  const pnls = list.map((t) => t.pnl);
  if (!pnls.length) return [];
  const lo = Math.min(...pnls);
  const hi = Math.max(...pnls);
  const span = hi - lo || 1;
  const width = span / binCount;
  const bins = Array.from({ length: binCount }, (_, i) => ({
    from: lo + i * width,
    to: lo + (i + 1) * width,
    count: 0,
    wins: 0,
    losses: 0,
  }));
  for (const pnl of pnls) {
    const idx = Math.min(binCount - 1, Math.max(0, Math.floor((pnl - lo) / width)));
    bins[idx].count += 1;
    if (pnl > 0) bins[idx].wins += 1;
    else bins[idx].losses += 1;
  }
  return bins;
}

export type DirectionStat = {
  direction: 'LONG' | 'SHORT';
  trades: number;
  wins: number;
  winRate: number;
  avgPnl: number;
  totalPnl: number;
  avgWin: number;
  avgLoss: number;
  expectancy: number;
  profitFactor: number;
  maxLosingStreak: number;
};

export function directionStats(list: readonly EnrichedTrade[]): DirectionStat[] {
  return (['LONG', 'SHORT'] as const).map((direction) => {
    const rows = list.filter((t) => t.direction === direction);
    const p = performance(rows);
    const r = risk(rows);
    return {
      direction,
      trades: p.trades,
      wins: p.wins,
      winRate: p.winRate,
      avgPnl: p.avgPnl,
      totalPnl: p.netPnl,
      avgWin: p.avgWin,
      avgLoss: p.avgLoss,
      expectancy: p.expectancy,
      profitFactor: p.profitFactor,
      maxLosingStreak: r.maxLosingStreak,
    };
  });
}

export type ExitStat = { reason: string; count: number; share: number; totalPnl: number };

export function exitStats(list: readonly EnrichedTrade[]): ExitStat[] {
  const total = list.length || 1;
  return (['TP', 'SL', 'EOD'] as const)
    .map((reason) => {
      const rows = list.filter((t) => t.reason === reason);
      return {
        reason,
        count: rows.length,
        share: (rows.length / total) * 100,
        totalPnl: sum(rows.map((t) => t.pnl)),
      };
    })
    .filter((r) => r.count > 0);
}

/** Seeded run identity used by the research placeholders. */
export const runMeta = {
  strategyVersion: 'candle-direction v1',
  parametersSnapshot: { stopDistance: 5, takeProfitDistance: 10, timeframe: 'M15', symbol: 'XAUUSD' },
  backtestId: 'demo-run-0001',
  runTimestamp: '2025-01-31T22:00:00Z',
  dataSource: 'Seeded demo replay',
};

export const enriched: EnrichedTrade[] = enrich(trades);