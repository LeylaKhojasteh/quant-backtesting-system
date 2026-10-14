// Derived analytics over the seeded trade set.
// Every value here is computed from `trades`; nothing is hard-coded.
// MAE/MFE come from the seeded replay path (see demo.tradePath) and are labelled
// as seeded in the UI until the Python engine supplies observed excursions.
import { tradePath, trades } from './demo';
import type { Trade } from './types';
import type { BacktestRun } from './schema';

/** Seeded excursion values (demo replay path). Always concrete numbers. */
export type Excursion = { mae: number; mfe: number; maeTime: number; mfeTime: number };

/**
 * Excursion fields on an enriched trade row. Archived engine runs that do not
 * supply observed excursions carry null here — never a synthesized value and
 * never a stand-in zero. Demo runs keep their seeded replay-path excursions.
 */
export type TradeExcursion = {
  mae: number | null;
  mfe: number | null;
  maeTime: number | null;
  mfeTime: number | null;
};

export type EnrichedTrade = Trade & TradeExcursion & { durationMin: number };

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

/**
 * Rebuilds demo Trade records from a canonical BacktestRun's trades, so the
 * seeded replay path (MAE/MFE) and every derived statistic are computed from
 * the single run source instead of scattered fixtures.
 *
 * Excursion availability follows the run's data mode:
 * - seeded demo: the seeded replay path supplies excursions (unchanged).
 * - archived / live: the engine does not yet supply observed excursions, so
 *   MAE/MFE stay null and are rendered N/A. They are never synthesized.
 */
export function enrichRunTrades(run: BacktestRun): EnrichedTrade[] {
  const ordered = run.trades
    .map((t) => ({
      id: t.id,
      direction: t.direction,
      signal: Date.parse(t.signalTimeUtc),
      signalAvailable: t.signalAvailableUtc
        ? Date.parse(t.signalAvailableUtc)
        : Date.parse(t.signalTimeUtc) + 900000,
      entryTime: Date.parse(t.entryTimeUtc),
      exitTime: Date.parse(t.exitTimeUtc),
      entry: t.entry,
      exit: t.exit,
      pnl: t.pnl,
      reason: t.reason,
      spreadIn: t.spreadIn,
      spreadOut: t.spreadOut,
      sl: t.sl ?? t.entry,
      tp: t.tp ?? t.entry,
    }))
    .sort((a, b) => a.exitTime - b.exitTime);

  if (run.dataMode === 'seeded-demo') return enrich(ordered);

  return ordered.map((t) => ({
    ...t,
    mae: null,
    mfe: null,
    maeTime: null,
    mfeTime: null,
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

// ---------------------------------------------------------------------------
// Performance metrics — PRICE TERMS ONLY
// ---------------------------------------------------------------------------
// Nothing in this module is an account-currency result. PnL is expressed in
// instrument price points; any metric that requires an account (balance,
// currency return, annualisation) is either null or explicitly gated, never
// synthesised from an arbitrary base balance.

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
  };
}

/** Reference stop distance in price terms; one R unit. */
export const R_UNIT = 5;

/** Total wall-clock span covered by the closed trades, in milliseconds. */
export function tradingSpanMs(list: readonly EnrichedTrade[]) {
  if (!list.length) return 0;
  return Math.max(...list.map((t) => t.exitTime)) - Math.min(...list.map((t) => t.entryTime));
}

/** Annualisation acceptance threshold: a sample must span at least 90 days… */
export const MIN_ANNUALIZE_SPAN_DAYS = 90;
/** …and contain at least 30 observations. */
export const MIN_ANNUALIZE_OBSERVATIONS = 30;

export function canAnnualize(spanMs: number, observations: number): boolean {
  const days = spanMs / (24 * 3600 * 1000);
  return days >= MIN_ANNUALIZE_SPAN_DAYS && observations >= MIN_ANNUALIZE_OBSERVATIONS;
}

export type AccountDrawdown = {
  /** Peak-to-trough decline of the account balance series, in account currency. */
  maxDrawdown: number | null;
  /** maxDrawdown / peakBalance * 100; null when no valid peak balance exists. */
  maxDrawdownPct: number | null;
  peakBalance: number | null;
  reason: string | null;
};

/**
 * Correct account drawdown from an account-currency balance series.
 * This is the ONLY place a percentage drawdown may be produced.
 */
export function accountDrawdown(balances: readonly number[]): AccountDrawdown {
  if (!balances.length) {
    return { maxDrawdown: null, maxDrawdownPct: null, peakBalance: null, reason: 'No account balance series available.' };
  }
  let peak = -Infinity;
  let peakBalance = 0;
  let maxDecline = 0;
  for (const b of balances) {
    if (b > peak) {
      peak = b;
      peakBalance = b;
    }
    const decline = peak - b;
    if (decline > maxDecline) maxDecline = decline;
  }
  if (!(peakBalance > 0)) {
    return { maxDrawdown: null, maxDrawdownPct: null, peakBalance: null, reason: 'Peak account balance is not positive; percentage drawdown is undefined.' };
  }
  return { maxDrawdown: maxDecline, maxDrawdownPct: (maxDecline / peakBalance) * 100, peakBalance: peakBalance, reason: null };
}

export type AdvancedPerformance = {
  /**
   * Account-currency total return, percent. Null until an account balance
   * series exists; price-term PnL must never be divided into a percentage
   * against an arbitrary base.
   */
  totalReturnPct: number | null;
  /** Null unless canAnnualize(spanMs, observations) holds. */
  annualizedReturnPct: number | null;
  /** Standard Sortino needs periodic account returns + target return; null until then. */
  sortino: number | null;
  calmar: number | null;
  annualizable: boolean;
  /** Exact reasons each metric is unavailable; rendered verbatim. */
  reasons: {
    totalReturnPct: string | null;
    annualizedReturnPct: string | null;
    sortino: string | null;
    calmar: string | null;
  };
};

/**
 * Account-level performance. For the seeded price-term run every value is
 * null with an explicit reason: these statistics are not synthesised from a
 * fictional account.
 */
export function advancedPerformance(list: readonly EnrichedTrade[], balances: readonly number[] | null = null): AdvancedPerformance {
  const span = tradingSpanMs(list);
  const annualizable = canAnnualize(span, list.length);
  const days = span / (24 * 3600 * 1000);
  const sortinoReason =
    'Standard Sortino requires a periodic account-return series (returns per period and a target return); it is not computed from seven price-term trades.';
  let totalReturnPct: number | null = null;
  let totalReturnReason: string | null =
    'No account-currency balance series (account currency, lot sizing, commission, conversion). Price-term PnL is not converted.';
  let annualized: number | null = null;
  let annualizedReason: string | null =
    `Sample spans ${days.toFixed(1)} days over ${list.length} trades; annualisation requires >= ${MIN_ANNUALIZE_SPAN_DAYS} days and >= ${MIN_ANNUALIZE_OBSERVATIONS} observations.`;
  let calmar: number | null = null;
  let calmarReason: string | null = totalReturnReason;
  if (balances && balances.length > 1) {
    const start = balances[0];
    const end = balances[balances.length - 1];
    if (start > 0) {
      totalReturnPct = ((end - start) / start) * 100;
      totalReturnReason = null;
      if (annualizable && start > 0) {
        const years = days / 365.25;
        annualized = (Math.pow(1 + totalReturnPct / 100, 1 / years) - 1) * 100;
        annualizedReason = null;
        const dd = accountDrawdown(balances);
        if (dd.maxDrawdownPct !== null && dd.maxDrawdownPct > 0) {
          calmar = totalReturnPct / dd.maxDrawdownPct;
          calmarReason = null;
        } else {
          calmarReason = 'Maximum account drawdown is not positive; Calmar is undefined.';
        }
      }
    }
  }
  return {
    totalReturnPct,
    annualizedReturnPct: annualized,
    sortino: null,
    calmar,
    annualizable,
    reasons: {
      totalReturnPct: totalReturnReason,
      annualizedReturnPct: annualizedReason,
      sortino: sortinoReason,
      calmar: calmarReason,
    },
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
  /** Null when no trade carries an observed adverse excursion. */
  avgMAE: number | null;
  /** Null when no trade carries an observed favorable excursion. */
  avgMFE: number | null;
  /**
   * Realized PnL as a share of the best favorable excursion. Only meaningful
   * for trades that reached a positive MFE. Null when no observed MFE exists.
   */
  mfeCapturePct: number | null;
};

export function tradeQuality(list: readonly EnrichedTrade[]): TradeQuality {
  const maes = list.map((t) => t.mae).filter((v): v is number => v !== null);
  const mfes = list.map((t) => t.mfe).filter((v): v is number => v !== null);
  const withMfe = list.filter(
    (t): t is EnrichedTrade & { mfe: number } => t.mfe !== null && t.mfe > 0,
  );
  return {
    expectancyR: mean(list.map((t) => t.pnl / R_UNIT)),
    avgR: mean(list.map((t) => t.pnl / R_UNIT)),
    avgMAE: maes.length ? mean(maes) : null,
    avgMFE: mfes.length ? mean(mfes) : null,
    mfeCapturePct: withMfe.length
      ? (sum(withMfe.map((t) => t.pnl)) / sum(withMfe.map((t) => t.mfe))) * 100
      : null,
  };
}

export type SignalAnalysis = {
  /** Real backend signal count (execution.signals_received), or null if unknown. */
  totalSignals: number | null;
  executed: number;
  ignored: number | null;
  /** Share of strategy signals that became an executed trade; null if unknown. */
  executionRatePct: number | null;
};

/**
 * Signal accounting from REAL backend metadata. `totalSignals` must come from
 * the run's archived `execution.signals_received`; it is never inferred from a
 * candle count, a trade count, or a fixture array. When it is unknown (null),
 * the derived rates stay null so the UI renders N/A instead of a fabricated
 * percentage.
 */
export function signalAnalysis(
  list: readonly EnrichedTrade[],
  totalSignals: number | null,
): SignalAnalysis {
  const executed = list.length;
  if (totalSignals === null || totalSignals === undefined || totalSignals <= 0) {
    return { totalSignals: null, executed, ignored: null, executionRatePct: null };
  }
  return {
    totalSignals,
    executed,
    ignored: Math.max(0, totalSignals - executed),
    executionRatePct: (executed / totalSignals) * 100,
  };
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
  /** Percent drawdown needs an account balance peak; null until an account model exists. */
  maxDrawdownPct: number | null;
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
  /**
   * A Sharpe ratio requires a valid account return series; per-trade PnL
   * dispersion is not a Sharpe. Null until an account return series exists.
   */
  sharpe: number | null;
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
    // A percentage drawdown needs an account balance peak; the price-term PnL
    // curve cannot be labelled as a percent. Kept null until an account model
    // exists (see accountDrawdown, the only place a pct may be produced).
    maxDrawdownPct: null,
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
    // Per-trade PnL dispersion is not a Sharpe ratio. Null until an account
    // return series exists (the type above documents this).
    sharpe: null,
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

/**
 * NOTE: no account balance/equity series is derived here. Adding price-term
 * PnL to an arbitrary base balance (the old balanceSeries / equitySummary)
 * fabricated account results and has been removed. Account balance/equity
 * series come from the BacktestRun model (run.equity) when
 * account.accountReady is true.
 */

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

export const enriched: EnrichedTrade[] = enrich(trades);