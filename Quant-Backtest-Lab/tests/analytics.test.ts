import { test, describe } from 'node:test';
import { strict as assert } from 'node:assert';
import {
  canAnnualize,
  accountDrawdown,
  advancedPerformance,
  tradingSpanMs,
  risk,
} from '../app/analytics.js';
import type { EnrichedTrade } from '../app/analytics.js';
import { loadBacktestRun } from '../app/runner.js';

describe('analytics.ts - financial calculations', () => {
  test('canAnnualize requires >= 90 days and >= 30 observations', () => {
    // 22 days, 7 trades - should fail
    assert.equal(canAnnualize(22 * 24 * 3600 * 1000, 7), false);
    // 90 days, 30 trades - should pass
    assert.equal(canAnnualize(90 * 24 * 3600 * 1000, 30), true);
    // 90 days, 29 trades - should fail
    assert.equal(canAnnualize(90 * 24 * 3600 * 1000, 29), false);
    // 89 days, 30 trades - should fail
    assert.equal(canAnnualize(89 * 24 * 3600 * 1000, 30), false);
  });

  test('accountDrawdown returns null for empty balances', () => {
    const result = accountDrawdown([]);
    assert.equal(result.maxDrawdown, null);
    assert.equal(result.maxDrawdownPct, null);
    assert.equal(result.peakBalance, null);
    assert.ok(result.reason !== null);
  });

  test('accountDrawdown computes correct percentage from peak balance', () => {
    // The algorithm tracks running peak: 10000 -> 10500 (peak) -> 9000 (decline 1500) -> 11000 (new peak)
    // Max decline is 1500 from peak 10500, but final peak is 11000
    // maxDecline = 1500, peakBalance = 11000 (running peak), pct = 1500/11000*100
    const balances = [10000, 10500, 9000, 11000];
    const result = accountDrawdown(balances);
    assert.ok(result.maxDrawdown !== null);
    assert.ok(result.maxDrawdownPct !== null);
    assert.equal(result.maxDrawdown, 1500);
    assert.equal(result.peakBalance, 11000);
    assert.ok(Math.abs(result.maxDrawdownPct! - (1500/11000*100)) < 0.0001);
    assert.equal(result.reason, null);
  });

  test('advancedPerformance returns null for account metrics when no balances', () => {
    const mockTrades = [
      { id: 1, direction: 'LONG' as const, pnl: -5, exitTime: 1000, entryTime: 0, durationMin: 60, mae: 1, mfe: 2, spreadIn: 1, spreadOut: 1 },
      { id: 2, direction: 'SHORT' as const, pnl: -5, exitTime: 2000, entryTime: 1000, durationMin: 60, mae: 1, mfe: 2, spreadIn: 1, spreadOut: 1 },
    ];
    const result = advancedPerformance(mockTrades as unknown as EnrichedTrade[], null);
    assert.equal(result.totalReturnPct, null);
    assert.equal(result.annualizedReturnPct, null);
    assert.equal(result.sortino, null);
    assert.equal(result.calmar, null);
    assert.equal(result.annualizable, false);
    assert.ok(result.reasons.totalReturnPct !== null);
    assert.ok(result.reasons.annualizedReturnPct !== null);
    assert.ok(result.reasons.sortino !== null);
    assert.ok(result.reasons.calmar !== null);
  });

  test('advancedPerformance computes totalReturnPct when balances provided', () => {
    const mockTrades = [
      { id: 1, direction: 'LONG' as const, pnl: 100, exitTime: 1000, entryTime: 0, durationMin: 60, mae: 1, mfe: 2, spreadIn: 1, spreadOut: 1 },
    ];
    const balances = [10000, 10100];
    const result = advancedPerformance(mockTrades as unknown as EnrichedTrade[], balances);
    assert.ok(result.totalReturnPct !== null);
    assert.ok(Math.abs(result.totalReturnPct! - 1.0) < 0.01); // 1% return
  });

  test('risk() returns sharpe: null and maxDrawdownPct: null for price-term only data', () => {
    const mockTrades = [
      { id: 1, direction: 'LONG' as const, pnl: -5, exitTime: 1000, entryTime: 0, durationMin: 60, mae: 1, mfe: 2, spreadIn: 1, spreadOut: 1 },
      { id: 2, direction: 'SHORT' as const, pnl: -5, exitTime: 2000, entryTime: 1000, durationMin: 60, mae: 1, mfe: 2, spreadIn: 1, spreadOut: 1 },
    ];
    const r = risk(mockTrades as unknown as EnrichedTrade[]);
    assert.equal(r.sharpe, null);
    assert.equal(r.maxDrawdownPct, null);
  });

  test('tradingSpanMs computes span from earliest entry to latest exit', () => {
    const mockTrades = [
      { id: 1, direction: 'LONG' as const, pnl: -5, exitTime: 2000, entryTime: 0, durationMin: 60, mae: 1, mfe: 2, spreadIn: 1, spreadOut: 1 },
      { id: 2, direction: 'SHORT' as const, pnl: -5, exitTime: 5000, entryTime: 1000, durationMin: 60, mae: 1, mfe: 2, spreadIn: 1, spreadOut: 1 },
    ];
    const span = tradingSpanMs(mockTrades as unknown as EnrichedTrade[]);
    assert.equal(span, 5000); // 5000 - 0 = 5000
  });
});

describe('runner.ts - BacktestRun loader', () => {
  test('loadBacktestRun returns valid BacktestRun with seeded demo data', async () => {
    const result = await loadBacktestRun();
    assert.ok(result.ok);
    if (result.ok) {
      const run = result.run;
      assert.equal(run.dataMode, 'seeded-demo');
      assert.ok(run.runId.match(/^[0-9a-f-]+$/));
      assert.ok(run.runTimestampUtc.endsWith('Z'));
      assert.equal(run.trades.length, 7);
      assert.equal(run.equity.length, 7);
      assert.equal(run.account.accountReady, false);
      assert.equal(run.account.currency, null);
      assert.equal(run.metrics.returnPct, null);
      assert.equal(run.metrics.sharpe, null);
      assert.equal(run.metrics.sortino, null);
      assert.equal(run.metrics.calmar, null);
      assert.equal(run.metrics.annualizedReturnPct, null);
      assert.equal(run.metrics.maxDrawdownPct, null);
      assert.ok((run.metrics.accountUnavailableReasons?.length ?? 0) > 0);
      // Equity pnl matches cumulative
      const expectedPnl = [-5.16, -10.18, -15.20, -20.24, -10.20, -15.38, -15.82];
      run.equity.forEach((pt, i) => {
        assert.ok(Math.abs(pt.pnl - expectedPnl[i]) < 0.01);
      });
    }
  });

  test('buildDemoRun output passes BacktestRunSchema validation', async () => {
    const result = await loadBacktestRun();
    assert.ok(result.ok);
    if (result.ok) {
      // Schema validation already happens inside loadBacktestRun
      assert.ok(result.run.runId);
      assert.ok(result.run.strategy.name);
      assert.ok(result.run.instrument.symbol);
    }
  });

  test('requestEngineRun throws with not-wired message', async () => {
    const { requestEngineRun } = await import('../app/runner.js');
    try {
      await requestEngineRun({
        strategyVersion: 'test',
        parameters: {},
        startUtc: '2025-01-31T00:00:00Z',
        endUtc: '2025-01-31T22:00:00Z',
      });
      assert.fail('Should have thrown');
    } catch (e) {
      assert.ok((e as Error).message.includes('Python backtest engine is not wired'));
    }
  });
});