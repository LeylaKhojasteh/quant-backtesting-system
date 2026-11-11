import { test, describe } from 'node:test';
import { strict as assert } from 'node:assert';
import {
  canAnnualize,
  accountDrawdown,
  advancedPerformance,
  tradingSpanMs,
  risk,
  enrichRunTrades,
  tradeQuality,
} from '../app/analytics.js';
import type { EnrichedTrade } from '../app/analytics.js';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { mapPythonResultToBacktestRun, type PythonResult } from '../app/python-result.js';
import { BacktestRunSchema } from '../app/schema.js';

const here = dirname(fileURLToPath(import.meta.url));
const archivedPayload = JSON.parse(
  readFileSync(join(here, '..', 'public', 'runs-run_0001-result.json'), 'utf-8'),
) as PythonResult;

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

describe('analytics.ts - excursion availability', () => {
  test('legacy archives without MAE/MFE stay null and never synthesize them', () => {
    const run = BacktestRunSchema.parse(mapPythonResultToBacktestRun(archivedPayload));
    const rows = enrichRunTrades(run);
    assert.ok(rows.length > 0);
    for (const t of rows) {
      assert.equal(t.mae, null, `trade ${t.id} MAE must stay null`);
      assert.equal(t.mfe, null, `trade ${t.id} MFE must stay null`);
      assert.equal(typeof t.durationMin, 'number');
    }
    const q = tradeQuality(rows);
    assert.equal(q.avgMAE, null);
    assert.equal(q.avgMFE, null);
    assert.equal(q.mfeCapturePct, null);
  });

  test('engine-supplied MAE/MFE flow into enriched trades and quality metrics', () => {
    const payload: PythonResult = {
      ...archivedPayload,
      trades: archivedPayload.trades.map((t) => ({ ...t, mae: -5, mfe: 10 })),
    };
    const rows = enrichRunTrades(BacktestRunSchema.parse(mapPythonResultToBacktestRun(payload)));
    assert.ok(rows.length > 0);
    for (const t of rows) {
      assert.equal(t.mae, -5);
      assert.equal(t.mfe, 10);
    }
    const q = tradeQuality(rows);
    assert.equal(q.avgMAE, -5);
    assert.equal(q.avgMFE, 10);
    assert.equal(typeof q.mfeCapturePct, 'number');

    const q10 = tradeQuality(rows, 10);
    assert.ok(Math.abs(q10.avgR - q.avgR / 2) < 1e-12);
  });
});