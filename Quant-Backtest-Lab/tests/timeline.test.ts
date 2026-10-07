/**
 * Timeline chronology regression: the UI must reflect the archive's real
 * signal availability, never a synthesized +1 candle offset.
 *
 * Contract: signal_candle_start <= signal_available_time <= entry_time <= exit_time.
 */
import { test, describe } from 'node:test';
import { strict as assert } from 'node:assert';
import { readFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { mapPythonResultToBacktestRun, type PythonResult } from '../app/python-result.js';
import { BacktestRunSchema } from '../app/schema.js';
import { tradeTimelineEvents } from '../app/timeline-events.js';

const here = dirname(fileURLToPath(import.meta.url));
// Bundled tests run from .test-build/, so the repo root is two levels up.
const repoRoot = join(here, '..', '..');
const fixture = JSON.parse(
  readFileSync(join(repoRoot, 'tests', 'fixtures', 'run_0001', 'result.json'), 'utf-8'),
) as PythonResult;
const parsed = BacktestRunSchema.safeParse(mapPythonResultToBacktestRun(fixture));
assert.ok(parsed.success, 'fixture must map to a valid BacktestRun');
const run = parsed.success ? parsed.data : null!

describe('timeline chronology (archived run_0001 mapping)', () => {
  test('signal candle <= available <= entry <= exit for every trade', () => {
    assert.ok(run.trades.length >= 1);
    for (const t of run.trades) {
      const s = Date.parse(t.signalTimeUtc);
      const a = Date.parse(t.signalAvailableUtc!);
      const e = Date.parse(t.entryTimeUtc);
      const x = Date.parse(t.exitTimeUtc);
      assert.ok(s <= a, `trade ${t.id}: signal ${t.signalTimeUtc} must be <= available ${t.signalAvailableUtc}`);
      assert.ok(a <= e, `trade ${t.id}: available ${t.signalAvailableUtc} must be <= entry ${t.entryTimeUtc}`);
      assert.ok(e <= x, `trade ${t.id}: entry ${t.entryTimeUtc} must be <= exit ${t.exitTimeUtc}`);
    }
  });

  test('timeline "available" event uses the archived availability, not signal+15min', () => {
    const t = run.trades[0];
    const trade = {
      id: t.id,
      direction: t.direction,
      signal: Date.parse(t.signalTimeUtc),
      signalAvailable: Date.parse(t.signalAvailableUtc!),
      entryTime: Date.parse(t.entryTimeUtc),
      exitTime: Date.parse(t.exitTimeUtc),
      entry: t.entry,
      exit: t.exit,
      sl: t.sl,
      tp: t.tp,
      reason: t.reason,
    };
    const events = tradeTimelineEvents(trade);
    const byKey = Object.fromEntries(events.map((e) => [e.key, e]));
    assert.equal(byKey.available.timestampMs, Date.parse(t.signalAvailableUtc!));
    assert.ok(byKey.signal.timestampMs <= byKey.available.timestampMs);
    assert.ok(byKey.available.timestampMs <= byKey.entry.timestampMs);
    // events stay non-decreasing in time
    const ordered = events.map((e) => e.timestampMs);
    assert.deepEqual([...ordered].sort((a, b) => a - b), ordered);
  });
});
