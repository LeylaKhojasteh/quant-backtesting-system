import { test, describe } from 'node:test';
import { strict as assert } from 'node:assert';
import { readFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { mapPythonResultToBacktestRun, type PythonResult } from '../app/python-result.js';
import { BacktestRunSchema } from '../app/schema.js';

const here = dirname(fileURLToPath(import.meta.url));
const labRoot = join(here, '..');
const base = JSON.parse(
  readFileSync(join(labRoot, 'public', 'runs-run_0001-result.json'), 'utf-8'),
) as PythonResult;

const CONTRACT = 100;
const LOTS = 1;
const COMMISSION = 7;

/** Build an account-ready payload from the archived price-term run. */
function withAccount(): PythonResult {
  const ordered = [...base.trades].sort(
    (a, b) => Date.parse(a.exit_time.replace(' ', 'T')) - Date.parse(b.exit_time.replace(' ', 'T')) || a.trade_id - b.trade_id,
  );
  let balance = 10000;
  let grossProfit = 0;
  let grossLoss = 0;
  const trades = ordered.map((t) => {
    const gross = t.pnl_price * CONTRACT * LOTS;
    if (gross > 0) grossProfit += gross;
    else grossLoss += gross;
    balance += gross - COMMISSION;
    return { trade_id: t.trade_id, gross_account_pnl: gross, commission: COMMISSION, net_account_pnl: gross - COMMISSION, balance_after: balance };
  });
  const net = trades.reduce((a, r) => a + r.net_account_pnl, 0);
  let peak = 10000;
  let run = 10000;
  let maxDd = 0;
  for (const r of trades) {
    run += r.net_account_pnl;
    peak = Math.max(peak, run);
    maxDd = Math.max(maxDd, peak - run);
  }
  return {
    ...base,
    account: {
      available: true,
      account_currency: 'USD',
      model: { account_currency: 'USD', starting_balance: 10000, position_size_lots: LOTS, commission_per_lot_round_turn: COMMISSION },
      instrument: { symbol: 'XAUUSD', quote_currency: 'USD', contract_size: CONTRACT, tick_size: 0.01 },
      metrics: {
        starting_balance: 10000, ending_balance: 10000 + net, net_pnl: net,
        gross_profit: grossProfit, gross_loss: grossLoss, total_commission: COMMISSION * trades.length,
        total_return_pct: (net / 10000) * 100, avg_trade_pnl: net / trades.length,
        max_drawdown: maxDd, max_drawdown_pct: (maxDd / peak) * 100,
      },
      trades,
      balance_curve: trades.map((r, i) => ({ timestamp: ordered[i].exit_time, realized_balance: r.balance_after })),
      notes: ['realized balance from closed trades'],
    },
  };
}

describe('python-result.ts - account model mapping', () => {
  test('account-ready payload maps to a valid ready BacktestRun', () => {
    const payload = withAccount();
    const candidate = mapPythonResultToBacktestRun(payload);
    const parsed = BacktestRunSchema.safeParse(candidate);
    assert.ok(parsed.success, parsed.success ? '' : JSON.stringify(parsed.error.issues).slice(0, 500));
    if (!parsed.success) return;
    const run = parsed.data;
    assert.equal(run.account.accountReady, true);
    assert.equal(run.account.currency, 'USD');
    assert.equal(run.account.initialBalance, 10000);
    assert.equal(run.account.contractSize, CONTRACT);
    assert.equal(run.account.lotSize, LOTS);
    assert.ok(run.metrics.returnPct !== null && Math.abs(run.metrics.returnPct - 20.62) < 1e-6);
    assert.ok(run.metrics.maxDrawdownPct !== null);
    // commission applied exactly once per trade
    assert.equal(run.trades.length, 7);
    run.trades.forEach((t) => assert.equal(t.commission, COMMISSION));
    assert.equal(
      run.trades.reduce((a, t) => a + (t.commission ?? 0), 0),
      COMMISSION * 7,
    );
    // balance curve present, ending balance correct
    const lastBalance = run.equity[run.equity.length - 1].balance ?? 0;
    assert.ok(Math.abs(lastBalance - (10000 + 2111 - COMMISSION * 7)) < 1e-6, String(lastBalance));
    assert.equal((run.metrics.accountUnavailableReasons ?? []).length, 0);
  });

  test('unsupported account metrics stay unavailable', () => {
    const run = mapPythonResultToBacktestRun(withAccount()) as {
      metrics: { sharpe: number | null; sortino: number | null; calmar: number | null; annualizedReturnPct: number | null };
    };
    assert.equal(run.metrics.sharpe, null);
    assert.equal(run.metrics.sortino, null);
    assert.equal(run.metrics.calmar, null);
    assert.equal(run.metrics.annualizedReturnPct, null);
  });

  test('unavailable account model keeps accountReady false and explains why', () => {
    const payload: PythonResult = {
      ...base,
      account: { available: false, account_currency: 'EUR', reason_unavailable: 'FX conversion unavailable: USD != EUR.' },
    };
    const run = mapPythonResultToBacktestRun(payload) as {
      account: { accountReady: boolean; note: string | null };
      metrics: { returnPct: number | null; accountUnavailableReasons?: string[] };
      trades: { commission?: number }[];
    };
    assert.equal(run.account.accountReady, false);
    assert.match(String(run.account.note), /FX conversion/);
    assert.equal(run.metrics.returnPct, null);
    assert.ok((run.metrics.accountUnavailableReasons ?? []).some((r) => /FX conversion/.test(r)));
    assert.equal(run.trades[0].commission, undefined);
  });

  test('archived run without an account section stays backward compatible', () => {
    const run = mapPythonResultToBacktestRun(base) as {
      account: { accountReady: boolean };
      metrics: { returnPct: number | null; maxDrawdownPct: number | null };
    };
    assert.equal(run.account.accountReady, false);
    assert.equal(run.metrics.returnPct, null);
    assert.equal(run.metrics.maxDrawdownPct, null);
  });
});
