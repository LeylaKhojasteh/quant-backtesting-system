/**
 * Quant Backtest Lab — result loader and adapter boundary.
 *
 * `loadBacktestRun()` is the single entry point the UI uses to obtain a
 * BacktestRun. The demo adapter is the only concrete implementation today; a
 * boundary for the future Python engine lives in `./runner.python-stub`.
 *
 * The loader validates its output through `BacktestRunSchema` before returning
 * it, so a malformed payload can never reach the UI as "real" results.
 */
import { BacktestRunSchema, type BacktestRun, type EquityPoint, type Metrics, type Trade } from './schema';
import { trades as seedTrades, candles } from './demo';
import { enrich, equitySeries, drawdownSeries } from './analytics';
import { toUtcIso } from './utc';

export type LoadResult =
  | { ok: true; run: BacktestRun }
  | { ok: false; error: string; runId?: string };

/** Run identity for the seeded reference. */
const RUN_ID = '00000000-0000-4000-8000-000000000001';
const RUN_TIMESTAMP_UTC = '2025-01-31T22:00:00Z';
const STRATEGY_VERSION = 'candle-direction v1';
const PARAMETERS_SNAPSHOT = { stopDistance: 5, takeProfitDistance: 10, timeframe: 'M15', symbol: 'XAUUSD' };

/**
 * Future engine boundary. The Python backtest adapter will live here once the
 * engine is wired up; today it throws so a half-wired state fails loudly
 * instead of pretending to be data.
 */
export async function requestEngineRun(_: {
  strategyVersion: string;
  parameters: Record<string, unknown>;
  startUtc: string;
  endUtc: string;
}): Promise<BacktestRun> {
  throw new Error(
    'Python backtest engine is not wired. Use loadBacktestRun() for the seeded demo, or connect the engine adapter before calling this boundary.',
  );
}

/** Reasons every account-level metric must stay Pending for the demo run. */
const ACCOUNT_UNAVAILABLE_REASONS = [
  'No account currency configured — price-term engine only.',
  'No lot sizing, contract size, commission, swap or slippage provided.',
  'Per-trade PnL is in price points; account-currency PnL cannot be converted.',
];

/**
 * Builds the seeded BacktestRun from the existing demo fixtures, translating
 * every invariant into the canonical schema. Price-term PnL is always carried;
 * account terms are entirely omitted so the UI can render them Pending.
 */
export function buildDemoRun(): unknown {
  const ordered = [...enrich(seedTrades)].sort((a, b) => a.exitTime - b.exitTime);
  const trades: Trade[] = ordered.map((t) => ({
    id: t.id,
    direction: t.direction,
    signalTimeUtc: toUtcIso(t.signal),
    entryTimeUtc: toUtcIso(t.entryTime),
    exitTimeUtc: toUtcIso(t.exitTime),
    entry: t.entry,
    exit: t.exit,
    sl: t.sl,
    tp: t.tp,
    pnl: t.pnl,
    spreadIn: t.spreadIn,
    spreadOut: t.spreadOut,
    // account-term fields intentionally omitted: no account model exists.
    reason: t.reason,
  }));

  const equity: EquityPoint[] = equitySeries(ordered).map((pnl, i) => ({
    timestampUtc: toUtcIso(ordered[i].exitTime),
    pnl,
    // balance / equity / pnlCurrency omitted — account terms unavailable.
  }));

  const wins = trades.filter((t) => t.pnl > 0);
  const losses = trades.filter((t) => t.pnl < 0);
  const grossProfit = sumPos(ordered);
  const grossLoss = sumNeg(ordered);
  const net = grossProfit + grossLoss;
  const dd = drawdownSeries(ordered);
  const durations = ordered.map((t) => (t.exitTime - t.entryTime) / 60000);
  const st = streaks(ordered.map((t) => t.pnl));
  const lossRuns = st.filter((s) => !s.win);
  const winRuns = st.filter((s) => s.win);
  const metrics: Metrics = {
    trades: trades.length,
    wins: wins.length,
    losses: losses.length,
    winRate: trades.length ? (wins.length / trades.length) * 100 : null,
    netPnl: net,
    grossProfit,
    grossLoss,
    avgPnl: net / trades.length,
    avgWin: mean(wins.map((t) => t.pnl)),
    avgLoss: losses.length ? mean(losses.map((t) => t.pnl)) : 0,
    payoff: losses.length ? mean(wins.map((t) => t.pnl)) / Math.abs(mean(losses.map((t) => t.pnl))) : null,
    profitFactor: grossLoss === 0 ? null : grossProfit / Math.abs(grossLoss),
    // price terms only
    maxDrawdown: dd.length ? Math.max(...dd) : 0,
    // account terms unavailable — kept null/omitted
    maxDrawdownPct: null,
    maxLosingStreak: lossRuns.length ? Math.max(...lossRuns.map((s) => s.count)) : 0,
    maxWinningStreak: winRuns.length ? Math.max(...winRuns.map((s) => s.count)) : 0,
    avgDurationMin: mean(durations),
    avgSpreadIn: mean(ordered.map((t) => t.spreadIn)),
    avgSpreadOut: mean(ordered.map((t) => t.spreadOut)),
    returnPct: null,
    annualizedReturnPct: null,
    sharpe: null,
    sortino: null,
    calmar: null,
    accountUnavailableReasons: ACCOUNT_UNAVAILABLE_REASONS,
  };

  return {
    runId: RUN_ID,
    runTimestampUtc: RUN_TIMESTAMP_UTC,
    dataMode: 'seeded-demo' as const,
    strategy: { name: 'Candle Direction Test', version: STRATEGY_VERSION, parameters: PARAMETERS_SNAPSHOT },
    instrument: { symbol: 'XAUUSD', timeframe: 'M15', timezone: 'UTC' },
    dateRange: {
      startUtc: toUtcIso(candles[0].t),
      endUtc: toUtcIso(candles[candles.length - 1].t),
    },
    dataSource: {
      name: 'Seeded example replay',
      fingerprint: 'seed:candle-direction:v1:88-candles:7-trades',
      kind: 'derived',
      version: STRATEGY_VERSION,
    },
    dataQuality: {
      status: 'demo' as const,
      // 99.12 is a demo placeholder from the seeded fixtures; the status is
      // `demo`, so the UI must render "Not measured", never a percentage.
      modelingQuality: undefined,
      candles: candles.length,
      totalTicks: null,
      inRangeTicks: 322548,
      gaps: null,
      duplicates: null,
      malformedRows: null,
      rejectedRows: null,
      timeCoveragePct: null,
      warnings: [
        'Seeded example: tick counts are illustrative, not measured from a loaded file.',
        'Modeling quality is not measured; no data-validation engine is attached.',
      ],
    },
    account: {
      currency: null,
      initialBalance: null,
      leverage: null,
      contractSize: null,
      lotSize: null,
      pipValue: null,
      note: 'Price-term engine only; no account-currency model configured.',
      accountReady: false,
    },
    trades,
    equity,
    metrics,
    warnings: ['Results are a seeded demo replay, not live or backtested-with-data output.'],
    limitations: [
      'PnL is in price terms only; no account currency, lot sizing, commission or conversion is applied.',
      'Sample span is a single 22-hour session; annualisation and return statistics are not meaningful.',
      'Modeling quality is not measured; no data validation engine is attached.',
    ],
  };
}

export async function loadBacktestRun(): Promise<LoadResult> {
  const candidate = buildDemoRun();
  const parsed = BacktestRunSchema.safeParse(candidate);
  if (!parsed.success) {
    console.error('[runner] demo run failed schema validation', parsed.error.issues);
    return { ok: false, error: parsed.error.message };
  }
  return { ok: true, run: parsed.data };
}

function sumPos(ts: { pnl: number }[]) {
  return ts.filter((t) => t.pnl > 0).reduce((a, t) => a + t.pnl, 0);
}
function sumNeg(ts: { pnl: number }[]) {
  return ts.filter((t) => t.pnl < 0).reduce((a, t) => a + t.pnl, 0);
}
function mean(v: number[]) {
  return v.length ? v.reduce((a, b) => a + b, 0) / v.length : 0;
}
function streaks(pnls: number[]) {
  const out: { win: boolean; count: number }[] = [];
  for (const p of pnls) {
    const win = p > 0;
    const last = out[out.length - 1];
    if (last && last.win === win) last.count += 1;
    else out.push({ win, count: 1 });
  }
  return out;
}
