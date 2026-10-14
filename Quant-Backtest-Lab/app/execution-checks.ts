/**
 * Execution validation from REAL run data.
 *
 * A check may only return PASS when real archived/execution data proves it.
 * When the available data cannot prove a check, `pass` is null and the UI must
 * render "N/A" — never a fabricated PASS and never a fixed "14 / 14".
 */
import type { TickPoint, Trade } from './types';

export type ExecCheck = {
  label: string;
  /** true = proven, false = proven false, null = not evaluable from available data. */
  pass: boolean | null;
  detail?: string;
};

/** Tolerance for comparing an archived fill price to a cached tick price. */
const PRICE_EPS = 1e-6;

function lastTickAtOrBefore(ticks: readonly TickPoint[], t: number): TickPoint | null {
  let found: TickPoint | null = null;
  for (const tick of ticks) {
    if (tick.t <= t) found = tick;
    else break;
  }
  return found;
}

export function executionChecks(trade: Trade, ticks: readonly TickPoint[] | null): ExecCheck[] {
  const long = trade.direction === 'LONG';
  const checks: ExecCheck[] = [];

  checks.push({
    label: 'Signal available ≤ entry',
    pass:
      trade.signalAvailable === undefined
        ? null
        : trade.signalAvailable <= trade.entryTime,
    detail:
      trade.signalAvailable === undefined
        ? 'Signal availability not archived for this run.'
        : undefined,
  });

  checks.push({
    label: 'Entry ≤ exit chronology',
    pass: trade.entryTime <= trade.exitTime,
  });

  const entryTick = ticks ? lastTickAtOrBefore(ticks, trade.entryTime) : null;
  checks.push({
    label: `${long ? 'Long' : 'Short'} entry uses ${long ? 'ASK' : 'BID'}`,
    pass: entryTick ? Math.abs(trade.entry - (long ? entryTick.ask : entryTick.bid)) < PRICE_EPS : null,
    detail: entryTick ? undefined : 'No cached tick at entry in the loaded window.',
  });

  const exitTick = ticks ? lastTickAtOrBefore(ticks, trade.exitTime) : null;
  checks.push({
    label: `${long ? 'Long' : 'Short'} exit uses ${long ? 'BID' : 'ASK'}`,
    pass: exitTick ? Math.abs(trade.exit - (long ? exitTick.bid : exitTick.ask)) < PRICE_EPS : null,
    detail: exitTick ? undefined : 'No cached tick at exit in the loaded window.',
  });

  const rulePass =
    trade.reason === 'TP'
      ? long
        ? trade.exit >= trade.tp
        : trade.exit <= trade.tp
      : trade.reason === 'SL'
        ? long
          ? trade.exit <= trade.sl
          : trade.exit >= trade.sl
        : true;
  checks.push({
    label:
      trade.reason === 'EOD'
        ? 'End-of-data close rule'
        : `${trade.reason} trigger rule (${long ? 'BID' : 'ASK'})`,
    pass: rulePass,
  });

  return checks;
}
