import { test, describe } from 'node:test';
import { strict as assert } from 'node:assert';
import { readinessItems } from '../app/readiness.js';
import { tradeTimelineEvents } from '../app/timeline-events.js';
import { loadBacktestRun } from '../app/runner.js';

describe('readiness.ts - DataReadiness derivation', () => {
  test('demo run never shows Verified or ready-green status', async () => {
    const result = await loadBacktestRun();
    assert.ok(result.ok);
    if (!result.ok) return;
    const items = readinessItems(result.run);
    assert.equal(items.length, 5);
    for (const item of items) {
      assert.ok(!/verified/i.test(item.status), `${item.label} must not say Verified`);
      assert.notEqual(item.tone, 'ready', `${item.label} must not be ready-green in demo`);
    }
    const byKey = Object.fromEntries(items.map((i) => [i.key, i]));
    assert.equal(byKey.source.status, 'Seeded demo');
    assert.equal(byKey.validation.status, 'Not measured');
    assert.equal(byKey.engine.status, 'Not connected');
    assert.equal(byKey.account.status, 'Not available');
    assert.equal(byKey.reliability.status, 'Visual preview only');
  });

  test('every readiness item carries a ready-hint for tooltips', async () => {
    const result = await loadBacktestRun();
    assert.ok(result.ok);
    if (!result.ok) return;
    for (const item of readinessItems(result.run)) {
      assert.ok(item.readyHint.length > 0, `${item.label} needs a ready hint`);
      assert.ok(item.detail.length > 0, `${item.label} needs a detail`);
    }
  });

  test('live measured run with accountReady renders ready tones', async () => {
    const result = await loadBacktestRun();
    assert.ok(result.ok);
    if (!result.ok) return;
    const live = {
      ...result.run,
      dataMode: 'live' as const,
      dataQuality: { ...result.run.dataQuality, status: 'measured' as const },
      account: { ...result.run.account, accountReady: true, currency: 'USD' },
    };
    const items = readinessItems(live);
    const byKey = Object.fromEntries(items.map((i) => [i.key, i]));
    assert.equal(byKey.validation.tone, 'ready');
    assert.equal(byKey.account.tone, 'ready');
    assert.equal(byKey.engine.tone, 'ready');
    assert.equal(byKey.source.tone, 'ready');
    assert.equal(byKey.reliability.tone, 'ready');
  });
});

describe('timeline-events.ts - TradeTimeline derivation', () => {
  test('demo trades produce the full 7-step lifecycle with UTC timestamps', async () => {
    const result = await loadBacktestRun();
    assert.ok(result.ok);
    if (!result.ok) return;
    for (const t of result.run.trades) {
      const trade = {
        id: t.id,
        direction: t.direction,
        signal: Date.parse(t.signalTimeUtc),
        entryTime: Date.parse(t.entryTimeUtc),
        exitTime: Date.parse(t.exitTimeUtc),
        entry: t.entry,
        exit: t.exit,
        sl: t.sl,
        tp: t.tp,
        reason: t.reason,
      };
      const events = tradeTimelineEvents(trade);
      assert.equal(events.length, 7);
      assert.deepEqual(
        events.map((e) => e.key),
        ['signal', 'available', 'eligible', 'entry', 'sltp', 'trigger', 'exit'],
      );
      for (const e of events) {
        assert.ok(e.timestampUtc.includes('UTC'), `${e.key} must display a UTC timestamp`);
        assert.ok(e.title.length > 0);
        assert.ok(e.note.length > 0);
      }
    }
  });

  test('TP / SL / EOD exits carry the matching tone and label', async () => {
    const result = await loadBacktestRun();
    assert.ok(result.ok);
    if (!result.ok) return;
    const seen = new Set<string>();
    for (const t of result.run.trades) {
      seen.add(t.reason);
      const trade = {
        id: t.id,
        direction: t.direction,
        signal: Date.parse(t.signalTimeUtc),
        entryTime: Date.parse(t.entryTimeUtc),
        exitTime: Date.parse(t.exitTimeUtc),
        entry: t.entry,
        exit: t.exit,
        sl: t.sl,
        tp: t.tp,
        reason: t.reason,
      };
      const events = tradeTimelineEvents(trade);
      const trigger = events.find((e) => e.key === 'trigger');
      assert.ok(trigger);
      const expected = t.reason === 'TP' ? 'tp' : t.reason === 'SL' ? 'sl' : 'eod';
      assert.equal(trigger.tone, expected);
      assert.ok(trigger.title.includes(t.reason));
    }
    assert.ok(seen.has('TP') && seen.has('SL') && seen.has('EOD'));
  });

  test('missing SL/TP renders Not supplied by run instead of invented levels', () => {
    const base = {
      id: 1,
      direction: 'LONG' as const,
      signal: Date.UTC(2025, 0, 31, 0, 0, 0),
      entryTime: Date.UTC(2025, 0, 31, 0, 15, 0),
      exitTime: Date.UTC(2025, 0, 31, 2, 0, 0),
      entry: 2800,
      exit: 2805,
      reason: 'EOD' as const,
    };
    const events = tradeTimelineEvents(base);
    const sltp = events.find((e) => e.key === 'sltp');
    assert.ok(sltp?.missing);
  });

  test('no fabricated account balance or return is produced', async () => {
    const result = await loadBacktestRun();
    assert.ok(result.ok);
    if (!result.ok) return;
    assert.equal(result.run.metrics.returnPct, null);
    assert.equal(result.run.metrics.annualizedReturnPct, null);
    for (const p of result.run.equity) {
      assert.equal(p.balance, undefined);
      assert.equal(p.pnlCurrency, undefined);
    }
  });
});
