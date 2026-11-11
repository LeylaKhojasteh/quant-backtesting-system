import { test, describe } from 'node:test';
import { strict as assert } from 'node:assert';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { readinessItems } from '../app/readiness.js';
import { tradeTimelineEvents } from '../app/timeline-events.js';
import { mapPythonResultToBacktestRun, type PythonResult } from '../app/python-result.js';
import { BacktestRunSchema } from '../app/schema.js';

const here = dirname(fileURLToPath(import.meta.url));
const payload = JSON.parse(
  readFileSync(join(here, '..', 'public', 'runs-run_0001-result.json'), 'utf-8'),
) as PythonResult;
const run = BacktestRunSchema.parse(mapPythonResultToBacktestRun(payload));

describe('readiness.ts - DataReadiness derivation', () => {
  test('archived measured price-term run is validated with Account Off', () => {
    const items = readinessItems(run);
    assert.equal(items.length, 4);
    for (const item of items) {
      assert.ok(!/verified/i.test(item.status), `${item.label} must not say Verified`);
      assert.ok(item.readyHint.length > 0, `${item.label} needs a ready hint`);
      assert.ok(item.detail.length > 0, `${item.label} needs a detail`);
    }
    const byKey = Object.fromEntries(items.map((i) => [i.key, i]));
    assert.equal(byKey.source.status, 'Archived dataset');
    assert.equal(byKey.source.tone, 'ready');
    assert.equal(byKey.validation.status, 'Measured');
    assert.equal(byKey.validation.tone, 'ready');
    assert.equal(byKey.engine.status, 'Validated');
    assert.equal(byKey.engine.tone, 'ready');
    assert.equal(byKey.account.status, 'Off');
    assert.match(byKey.account.detail, /Price-term results are available/);
    assert.ok(!items.some((i) => i.key === 'reliability'), 'no Result reliability item');
    assert.ok(!items.some((i) => /Account model missing/.test(i.status)));
  });

  test('accountReady run renders Account Ready', () => {
    const withAccount = {
      ...run,
      account: { ...run.account, accountReady: true, currency: 'USD' },
    };
    const byKey = Object.fromEntries(readinessItems(withAccount).map((i) => [i.key, i]));
    assert.equal(byKey.account.tone, 'ready');
    assert.equal(byKey.account.status, 'Ready');
  });
});

describe('timeline-events.ts - TradeTimeline derivation', () => {
  test('archived trades produce the full 7-step lifecycle with UTC timestamps', () => {
    for (const t of run.trades) {
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

  test('TP / SL / EOD exits carry the matching tone and label', () => {
    const seen = new Set<string>();
    for (const t of run.trades) {
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
      const expected = t.reason === 'TP' ? 'tp' : t.reason === 'SL' || t.reason === 'TSL' ? 'sl' : 'eod';
      assert.equal(trigger.tone, expected);
      assert.ok(trigger.title.includes(t.reason));
    }
    assert.ok(seen.has('TP') && seen.has('SL') && seen.has('EOD'));

    const tsl = tradeTimelineEvents({
      id: 9001,
      direction: 'LONG',
      signal: Date.UTC(2025, 0, 31, 0, 0, 0),
      entryTime: Date.UTC(2025, 0, 31, 0, 15, 0),
      exitTime: Date.UTC(2025, 0, 31, 2, 0, 0),
      entry: 2800,
      exit: 2795,
      sl: 2795,
      tp: 2810,
      reason: 'TSL',
    });
    const tslTrigger = tsl.find((e) => e.key === 'trigger');
    assert.ok(tslTrigger);
    assert.equal(tslTrigger.tone, 'sl');
    assert.ok(tslTrigger.title.includes('TSL'));
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

  test('null SL/TP renders Not supplied by run instead of entry-price levels', () => {
    const base = {
      id: 2,
      direction: 'SHORT' as const,
      signal: Date.UTC(2025, 0, 31, 0, 0, 0),
      entryTime: Date.UTC(2025, 0, 31, 0, 15, 0),
      exitTime: Date.UTC(2025, 0, 31, 2, 0, 0),
      entry: 2800,
      exit: 2790,
      sl: null,
      tp: null,
      reason: 'STRATEGY' as const,
    };
    const events = tradeTimelineEvents(base);
    const sltp = events.find((e) => e.key === 'sltp');
    assert.ok(sltp?.missing);
    assert.ok(!sltp?.note.includes('2800'));
  });

  test('no fabricated account balance or return is produced', () => {
    assert.equal(run.metrics.returnPct, null);
    assert.equal(run.metrics.annualizedReturnPct, null);
    for (const p of run.equity) {
      assert.equal(p.balance, undefined);
      assert.equal(p.pnlCurrency, undefined);
    }
  });
});
