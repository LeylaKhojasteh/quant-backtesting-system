/**
 * Full Report content, built from a single BacktestRun.
 *
 * Price-term statistics (PnL in instrument price points) are real values and
 * shown as numbers. Account-level statistics (balance, currency return,
 * annualisation, Sharpe/Sortino/Calmar, percent drawdown) are rendered
 * Pending with the exact reason from the run, never synthesised from a
 * fictional account.
 */
import { fmt } from './format';
import type { BacktestRun } from './schema';
import { formatUtcMs } from './utc';

export type ReportMetric = { label: string; value: string; note?: string; pending?: boolean; group?: string };

const sum = (v: number[]) => v.reduce((a, b) => a + b, 0);
const avg = (v: number[]) => (v.length ? sum(v) / v.length : 0);
const PENDING = 'Pending';

const metric = (label: string, value: string, note = 'Price terms — not account currency', group?: string): ReportMetric => ({ label, value, note, group });
const pending = (label: string, note: string, group?: string): ReportMetric => ({ label, value: PENDING, pending: true, note, group });

/** Continuous win/loss runs over the trade sequence. */
function runs(pnls: number[]) {
  const out: { win: boolean; count: number; pnl: number }[] = [];
  for (const p of pnls) {
    const win = p > 0;
    const last = out[out.length - 1];
    if (last && last.win === win) {
      last.count += 1;
      last.pnl += p;
    } else out.push({ win, count: 1, pnl: p });
  }
  return out;
}

const dur = (v: number) => `${Math.floor(v / 60)}h ${Math.floor(v % 60)}m ${Math.round(v % 1 * 60)}s`;
const count = (items: { pnl: number }[]) => `${items.length} (${items.length ? (items.filter((t) => t.pnl > 0).length / items.length * 100).toFixed(2) : '0.00'}% won)`;

type TradeRow = { id: number; direction: 'LONG' | 'SHORT'; pnl: number; reason: 'TP' | 'SL' | 'EOD'; entryMs: number; exitMs: number; spreadIn: number; spreadOut: number };

function rows(run: BacktestRun): TradeRow[] {
  return run.trades.map((t) => ({
    id: t.id,
    direction: t.direction,
    pnl: t.pnl,
    reason: t.reason,
    entryMs: Date.parse(t.entryTimeUtc),
    exitMs: Date.parse(t.exitTimeUtc),
    spreadIn: t.spreadIn,
    spreadOut: t.spreadOut,
  }));
}

export function reportContext(run: BacktestRun) {
  const ticks = run.dataQuality.inRangeTicks;
  const bars = run.dataQuality.candles;
  const tickText = ticks === null || ticks === undefined ? 'ticks not measured' : `${ticks} ticks`;
  return `${run.instrument.symbol} · ${run.instrument.timeframe} · ${formatUtcMs(Date.parse(run.dateRange.startUtc), { withDate: true })} → ${formatUtcMs(Date.parse(run.dateRange.endUtc))} · ${bars ?? '—'} bars · ${tickText} · ${run.trades.length} trades`;
}

/** Median real delay from signal availability to the first eligible entry. */
function signalEntryDelayMs(run: BacktestRun): number | null {
  const delays = run.trades
    .map((t) => (t.signalAvailableUtc ? Date.parse(t.entryTimeUtc) - Date.parse(t.signalAvailableUtc) : null))
    .filter((v): v is number => v !== null && Number.isFinite(v) && v >= 0);
  if (!delays.length) return null;
  delays.sort((a, b) => a - b);
  const mid = delays.length >> 1;
  return delays.length % 2 ? delays[mid] : Math.round((delays[mid - 1] + delays[mid]) / 2);
}

/** Headline labels that must read as Pending for a price-term run. */
const PENDING_HEADLINES = new Set(['Return %', 'Balance']);
export function headlineIsPending(label: string): boolean {
  return PENDING_HEADLINES.has(label);
}

export function headline(run: BacktestRun): [string, string][] {
  const t = run.trades;
  const m = run.metrics;
  const longs = t.filter((x) => x.direction === 'LONG').length;
  const shorts = t.length - longs;
  return [
    ['Total Trades', String(m.trades)],
    ['Win Rate', m.winRate !== null && m.winRate !== undefined ? `${m.winRate.toFixed(2)}%` : PENDING],
    ['Total PnL', fmt(m.netPnl)],
    ['Average PnL', fmt(m.avgPnl)],
    ['Profit Factor', m.profitFactor !== null && m.profitFactor !== undefined ? fmt(m.profitFactor) : PENDING],
    ['Expectancy', fmt(m.avgPnl)],
    ['Return %', PENDING],
    ['Max Drawdown', m.maxDrawdown !== null && m.maxDrawdown !== undefined ? fmt(m.maxDrawdown) : PENDING],
    ['Recovery Factor', m.maxDrawdown !== null && m.maxDrawdown !== undefined ? fmt(m.netPnl / m.maxDrawdown) : PENDING],
    ['Risk Reward', m.payoff !== null && m.payoff !== undefined ? fmt(m.payoff) : PENDING],
    ['Max Losing Streak', m.maxLosingStreak !== null && m.maxLosingStreak !== undefined ? String(m.maxLosingStreak) : PENDING],
    ['Long Trades', String(longs)],
    ['Short Trades', String(shorts)],
    ['Average Win', m.avgWin !== null && m.avgWin !== undefined ? fmt(m.avgWin) : PENDING],
    ['Average Loss', m.avgLoss !== null && m.avgLoss !== undefined ? fmt(m.avgLoss) : PENDING],
    ['Best Trade', t.length ? fmt(Math.max(...t.map((x) => x.pnl))) : PENDING],
    ['Worst Trade', t.length ? fmt(Math.min(...t.map((x) => x.pnl))) : PENDING],
    ['Avg Duration', m.avgDurationMin !== null && m.avgDurationMin !== undefined ? `${m.avgDurationMin.toFixed(0)} min` : PENDING],
    ['Balance', PENDING],
  ];
}

/** Account-level metrics unavailable for a price-term run share one honest reason. */
function accountReason(run: BacktestRun, extra: string): string {
  const base = run.metrics.accountUnavailableReasons ?? ['No account model configured.'];
  return `${extra} ${base[0]}`.trim();
}

export function reportGroups(run: BacktestRun): Record<string, ReportMetric[]> {
  const t = run.trades;
  const m = run.metrics;
  const wins = t.filter((x) => x.pnl > 0);
  const losses = t.filter((x) => x.pnl < 0);
  const longs = t.filter((x) => x.direction === 'LONG');
  const shorts = t.filter((x) => x.direction === 'SHORT');
  const list = rows(run);
  const durations = list.map((x) => (x.exitMs - x.entryMs) / 60000);
  const streaks = runs(list.map((x) => x.pnl));
  const winRuns = streaks.filter((s) => s.win);
  const lossRuns = streaks.filter((s) => !s.win);
  const q = run.dataQuality;
  const ticks = q.inRangeTicks === null || q.inRangeTicks === undefined ? 'Not measured' : q.inRangeTicks.toLocaleString('en-US');
  const delayMs = signalEntryDelayMs(run);
  const delayText = delayMs === null ? 'N/A' : `${delayMs.toLocaleString('en-US')} ms`;
  const signalsReceived = run.execution?.signalsReceived ?? null;

  const acc = (extra: string) => accountReason(run, extra);

  return {
    'Run & Data': [
      metric('Strategy', run.strategy.name, `Version ${run.strategy.version} · ${run.dataMode}`, 'Run Scope'),
      metric('Symbol / Period', `${run.instrument.symbol} / ${run.instrument.timeframe}`, `${formatUtcMs(Date.parse(run.dateRange.startUtc), { withDate: true })} → ${formatUtcMs(Date.parse(run.dateRange.endUtc))}`, 'Run Scope'),
      metric('Inputs', `Stop ${String(run.strategy.parameters.stopDistance)} · TP ${String(run.strategy.parameters.takeProfitDistance)}`, 'Reference result inputs; current configured inputs appear in the run strip', 'Run Scope'),
      metric('Bars', String(run.dataQuality.candles ?? '—'), `${run.instrument.timeframe} candles in range`, 'Dataset'),
      metric('Ticks', ticks, 'Ticks read from the canonical cache in range', 'Dataset'),
      metric('Symbols', '1', 'Single-symbol run', 'Dataset'),
      pending('History Quality', 'Not measured; requires one-minute data and gap validation from the data-validation engine.', 'Dataset'),
      pending('Modeling Quality', 'Not measured — the production definition comes from the real validation engine.', 'Dataset'),
      pending('Initial Deposit', acc('No account currency or account balance is configured.'), 'Account'),
      pending('Withdrawal', acc('No withdrawals are modelled without an account model.'), 'Account'),
      pending('Balance', acc('Balance = initial account balance + account-currency PnL, which does not exist here.'), 'Account'),
      pending('Currency / Leverage', 'No account configuration in the existing price-term engine.', 'Account'),
    ],
    'Profit & Risk': [
      metric('Total Net Profit', fmt(m.netPnl), 'Closed-trade PnL · price terms', 'Net Result'),
      metric('Gross Profit', fmt(m.grossProfit), 'Price terms', 'Net Result'),
      metric('Gross Loss', fmt(m.grossLoss), 'Price terms', 'Net Result'),
      metric('Profit Factor', m.profitFactor !== null && m.profitFactor !== undefined ? fmt(m.profitFactor) : PENDING, 'Gross profit / absolute gross loss · dimensionless · price terms', 'Net Result'),
      metric('Expected Payoff', fmt(m.avgPnl), 'Price terms', 'Net Result'),
      pending('Return %', acc('Return % requires an account-currency balance series.'), 'Net Result'),
      metric('Balance Drawdown Absolute', m.maxDrawdown !== null && m.maxDrawdown !== undefined ? fmt(m.maxDrawdown) : PENDING, 'Peak-to-trough decline of the closed-trade PnL curve · price terms', 'Drawdown'),
      metric('Balance Drawdown Maximal', m.maxDrawdown !== null && m.maxDrawdown !== undefined ? fmt(m.maxDrawdown) : PENDING, 'Peak-to-trough decline of the closed-trade PnL curve · price terms', 'Drawdown'),
      pending('Balance Drawdown Relative', acc('Percent drawdown needs peak account balance, lot sizing and initial deposit.'), 'Drawdown'),
      pending('Equity Drawdown Absolute', 'Requires intratrade marked-to-market account equity; not derivable from closed-trade price PnL.', 'Drawdown'),
      pending('Equity Drawdown Maximal', 'Requires intratrade marked-to-market account equity; not derivable from closed-trade price PnL.', 'Drawdown'),
      pending('Equity Drawdown Relative', 'Requires account-currency marked-to-market equity and peak balance.', 'Drawdown'),
      metric('Recovery Factor', m.maxDrawdown !== null && m.maxDrawdown !== undefined ? fmt(m.netPnl / m.maxDrawdown) : PENDING, 'Net price PnL / maximal closed-trade drawdown · price terms', 'Drawdown'),
      pending('Margin Level', 'Requires a real margin model. No simulated margin value is shown.', 'Drawdown'),
    ],
    'Trade Statistics': [
      metric('Total Trades', String(m.trades), 'Closed trades in the archived run', 'Sample'),
      pending('Total Deals', 'Trade log has no deal-level record; do not assume two deals per trade.', 'Sample'),
      metric('Short Trades (won %)', count(shorts), `${shorts.length} shorts; ${shorts.filter((s) => s.pnl > 0).length} profitable`, 'Sample'),
      metric('Long Trades (won %)', count(longs), `${longs.length} longs; ${longs.filter((s) => s.pnl > 0).length} profitable`, 'Sample'),
      metric('Profit Trades (% of total)', `${wins.length} (${(m.winRate ?? 0).toFixed(2)}%)`, 'Profitable closed trades', 'Outcome'),
      metric('Loss Trades (% of total)', `${losses.length} (${(losses.length / (m.trades || 1) * 100).toFixed(2)}%)`, 'Losing closed trades', 'Outcome'),
      metric('Largest Profit Trade', m.avgWin !== null && m.avgWin !== undefined ? fmt(Math.max(...wins.map((x) => x.pnl))) : PENDING, 'Price terms', 'Outcome'),
      metric('Largest Loss Trade', fmt(Math.min(...losses.map((x) => x.pnl))), 'Price terms', 'Outcome'),
      metric('Average Profit Trade', m.avgWin !== null && m.avgWin !== undefined ? fmt(m.avgWin) : PENDING, 'Price terms', 'Outcome'),
      metric('Average Loss Trade', m.avgLoss !== null && m.avgLoss !== undefined ? fmt(m.avgLoss) : PENDING, 'Price terms', 'Outcome'),
      metric('Minimal Position Holding Time', dur(Math.min(...durations)), 'Entry to exit · UTC', 'Holding Time'),
      metric('Maximal Position Holding Time', dur(Math.max(...durations)), 'Entry to exit · UTC', 'Holding Time'),
      metric('Average Position Holding Time', dur(m.avgDurationMin ?? 0), 'Entry to exit · UTC', 'Holding Time'),
    ],
    'Sequences & Ratios': [
      metric('Maximum Consecutive Wins', winRuns.length ? `${Math.max(...winRuns.map((s) => s.count))} (${fmt(winRuns.slice().sort((a, b) => b.count - a.count)[0].pnl)})` : PENDING, 'Count (price-term total)', 'Streaks'),
      metric('Maximum Consecutive Losses', lossRuns.length ? `${Math.max(...lossRuns.map((s) => s.count))} (${fmt(lossRuns.slice().sort((a, b) => b.count - a.count)[0].pnl)})` : PENDING, 'Count (price-term total)', 'Streaks'),
      metric('Maximal Consecutive Profit', winRuns.length ? `${fmt(Math.max(...winRuns.map((s) => s.pnl)))} (${winRuns.slice().sort((a, b) => b.pnl - a.pnl)[0].count})` : PENDING, 'Price-term total (count)', 'Streaks'),
      metric('Maximal Consecutive Loss', lossRuns.length ? `${fmt(Math.min(...lossRuns.map((s) => s.pnl)))} (${lossRuns.slice().sort((a, b) => a.pnl - b.pnl)[0].count})` : PENDING, 'Price-term total (count)', 'Streaks'),
      metric('Average Consecutive Wins', winRuns.length ? fmt(avg(winRuns.map((s) => s.count))) : PENDING, 'Average winning run length', 'Streaks'),
      metric('Average Consecutive Losses', lossRuns.length ? fmt(avg(lossRuns.map((s) => s.count))) : PENDING, 'Average losing run length', 'Streaks'),
      pending('Sharpe Ratio', 'Standard Sharpe requires a valid account return series (returns per period). Seven price-term trades are not a return series.', 'Statistical Ratios'),
      pending('Sortino Ratio', 'Standard Sortino requires periodic account returns and a target return; it is not computed from losing-trade PnL dispersion.', 'Statistical Ratios'),
      pending('Calmar Ratio', 'Calmar requires account-currency total return and account percent drawdown; neither is available.', 'Statistical Ratios'),
      pending('Annualized Return', `Sample spans ${Math.round((Date.parse(run.dateRange.endUtc) - Date.parse(run.dateRange.startUtc)) / (24 * 3600 * 1000))} days over ${m.trades} trades; annualisation requires >= 90 days and >= 30 observations.`, 'Statistical Ratios'),
      pending('AHPR', 'Requires account equity changes per trade.', 'Statistical Ratios'),
      pending('GHPR', 'Requires account equity changes per trade.', 'Statistical Ratios'),
      pending('Z-Score', 'Trade dependence statistic needs a defined statistical implementation and adequate sample.', 'Statistical Ratios'),
      pending('Balance Regression Correlation', 'Requires an account balance series in deposit currency.', 'Statistical Ratios'),
      pending('Balance Regression Error', 'Requires an account balance series in deposit currency.', 'Statistical Ratios'),
      pending('Custom Tester Output', 'The Python engine exposes no per-run tester callback yet.', 'Statistical Ratios'),
    ],
    'Excursions & Execution': [
      pending('Correlation (Profits, MFE)', 'Requires observed favorable excursions for every trade from the engine.', 'Excursion Correlation'),
      pending('Correlation (Profits, MAE)', 'Requires observed adverse excursions for every trade from the engine.', 'Excursion Correlation'),
      pending('Correlation (MFE, MAE)', 'Requires both observed excursion series from the engine.', 'Excursion Correlation'),
      metric('TP Exits', `${t.filter((x) => x.reason === 'TP').length} (${(t.filter((x) => x.reason === 'TP').length / (m.trades || 1) * 100).toFixed(2)}%)`, 'Take-profit exits', 'Execution'),
      metric('SL Exits', `${t.filter((x) => x.reason === 'SL').length} (${(t.filter((x) => x.reason === 'SL').length / (m.trades || 1) * 100).toFixed(2)}%)`, 'Stop-loss exits', 'Execution'),
      metric('End of Data', `${t.filter((x) => x.reason === 'EOD').length} (${(t.filter((x) => x.reason === 'EOD').length / (m.trades || 1) * 100).toFixed(2)}%)`, 'End-of-data exits', 'Execution'),
      metric('Average Entry Spread', m.avgSpreadIn !== null && m.avgSpreadIn !== undefined ? fmt(m.avgSpreadIn) : PENDING, 'BID/ASK spread observed at executed entries · price terms', 'Execution'),
      metric('Average Exit Spread', m.avgSpreadOut !== null && m.avgSpreadOut !== undefined ? fmt(m.avgSpreadOut) : PENDING, 'BID/ASK spread observed at executed exits · price terms', 'Execution'),
      metric('Signals Received', signalsReceived === null ? 'N/A' : String(signalsReceived), signalsReceived === null ? 'Signal count not archived for this run' : 'Signals produced by the strategy for this run', 'Execution'),
      metric('Signal → Entry Delay', delayText, delayMs === null ? 'Not derivable — signal availability or entry time missing' : 'Median delay from signal availability to first eligible entry', 'Execution'),
      metric('Fill-side Checks', `${t.length} entries + ${t.length} exits`, 'Per-fill PASS/FAIL against cached ticks is shown in the Trade Inspector', 'Execution'),
      metric('Execution Model', 'Observed BID / ASK', 'Real tick-level BID/ASK execution (src/backtest.py)', 'Execution'),
    ],
  };
}

/* ------------------------------------------------------------------------ */
/* Charts (Analytics view) — price terms, from the archived run             */
/* ------------------------------------------------------------------------ */

export const diagramNames = ['Entries by Hour', 'Entries by Weekday', 'Entries by Month', 'Profit / Loss by Hour', 'Profit / Loss by Weekday', 'Profit / Loss by Month', 'MFE vs Profit', 'MAE vs Profit', 'Profit vs Holding Time', 'PnL by Direction', 'Exit Reasons', 'R-Multiple Distribution'];

export function diagramData(name: string, run: BacktestRun) {
  const t = rows(run);
  if (name === 'MFE vs Profit' || name === 'MAE vs Profit') return [];
  if (name === 'Profit vs Holding Time') return t.map((x) => ({ label: `#${x.id}`, x: (x.exitMs - x.entryMs) / 60000, v: x.pnl, n: 1 }));
  if (name === 'PnL by Direction') return ['LONG', 'SHORT'].map((d) => ({ label: d, x: 0, v: sum(t.filter((x) => x.direction === d).map((x) => x.pnl)), n: t.filter((x) => x.direction === d).length }));
  if (name === 'Exit Reasons') return ['TP', 'SL', 'EOD'].map((d) => ({ label: d, x: 0, v: t.filter((x) => x.reason === d).length, n: t.filter((x) => x.reason === d).length }));
  if (name === 'R-Multiple Distribution') {
    // R is only meaningful when per-trade risk (stop distance x sizing) is
    // available. Without a validated sizing model this is a reference multiple,
    // not a measured R.
    const R = Number(run.strategy.parameters.stopDistance) || 5;
    return t.map((x) => ({ label: `#${x.id}`, x: x.id, v: x.pnl / R, n: 1 }));
  }
  const entry = name.startsWith('Entries');
  const hour = name.endsWith('Hour');
  const weekday = name.endsWith('Weekday');
  const labels = hour
    ? Array.from({ length: 24 }, (_, i) => `${String(i).padStart(2, '0')}:00`)
    : weekday
      ? ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
      : ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  return labels.map((label, x) => {
    const rows = t.filter((tr) => {
      const d = new Date((entry ? tr.entryMs : tr.exitMs));
      return (hour ? d.getUTCHours() : weekday ? d.getUTCDay() : d.getUTCMonth()) === x;
    });
    return { label, x, v: entry ? rows.length : sum(rows.map((x) => x.pnl)), positive: entry ? rows.length : sum(rows.filter((x) => x.pnl > 0).map((x) => x.pnl)), negative: entry ? 0 : sum(rows.filter((x) => x.pnl < 0).map((x) => x.pnl)), n: rows.length };
  });
}