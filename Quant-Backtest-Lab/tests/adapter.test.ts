import { test, describe } from 'node:test';
import { strict as assert } from 'node:assert';
import { readFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  mapPythonResultToBacktestRun,
  pythonRunIdToUuid,
  validatePythonResult,
  type PythonResult,
} from '../app/python-result.js';
import { BacktestRunSchema } from '../app/schema.js';
import { listArchivedRuns, loadArchivedRun, resolveRunFile } from '../adapters/python-result-adapter.js';

const here = dirname(fileURLToPath(import.meta.url));
const labRoot = join(here, '..');
const archivedPayload = JSON.parse(
  readFileSync(join(labRoot, 'public', 'runs-run_0001-result.json'), 'utf-8'),
) as PythonResult;

describe('python-result.ts - archived result mapping', () => {
  test('run_0001 maps to a schema-valid BacktestRun in live/measured mode', () => {
    assert.deepEqual(validatePythonResult(archivedPayload), []);
    const candidate = mapPythonResultToBacktestRun(archivedPayload);
    const parsed = BacktestRunSchema.safeParse(candidate);
    assert.ok(parsed.success, parsed.success ? '' : JSON.stringify(parsed.error.issues).slice(0, 500));
    if (!parsed.success) return;
    assert.equal(parsed.data.dataMode, 'live');
    assert.equal(parsed.data.dataQuality.status, 'measured');
    assert.equal(parsed.data.account.accountReady, false);
    assert.equal(parsed.data.trades.length, 7);
    assert.equal(parsed.data.equity.length, 7);
    assert.equal(parsed.data.metrics.trades, 7);
  });

  test('mapped metrics and trades match the archive files exactly', () => {
    const candidate = mapPythonResultToBacktestRun(archivedPayload) as {
      metrics: { netPnl: number; winRate: number | null };
      trades: { id: number; direction: string; reason: string; entry: number; exit: number; pnl: number }[];
      dataQuality: { inRangeTicks: number | null; candles: number | null };
    };
    assert.ok(Math.abs(candidate.metrics.netPnl - archivedPayload.metrics.net_pnl) < 1e-9);
    assert.equal(candidate.metrics.winRate, archivedPayload.metrics.win_rate);
    assert.equal(candidate.dataQuality.inRangeTicks, archivedPayload.data_quality.tick_count);
    assert.equal(candidate.dataQuality.candles, archivedPayload.data_quality.candle_count);
    candidate.trades.forEach((t, i) => {
      const src = archivedPayload.trades[i];
      assert.equal(t.direction, src.direction);
      assert.equal(t.reason, src.exit_reason);
      assert.equal(t.entry, src.entry_price);
      assert.equal(t.exit, src.exit_price);
      assert.ok(Math.abs(t.pnl - src.pnl_price) < 1e-9);
    });
  });

  test('python run id maps deterministically to a UUID, timestamps to UTC ISO', () => {
    assert.equal(pythonRunIdToUuid('run_0001'), pythonRunIdToUuid('run_0001'));
    assert.match(pythonRunIdToUuid('run_0001'), /^[0-9a-f]{8}-[0-9a-f]{4}-5[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
    const candidate = mapPythonResultToBacktestRun(archivedPayload) as {
      runTimestampUtc: string;
      trades: { signalTimeUtc: string; entryTimeUtc: string; exitTimeUtc: string }[];
    };
    assert.ok(candidate.runTimestampUtc.endsWith('Z') && candidate.runTimestampUtc.includes('T'));
    for (const t of candidate.trades) {
      for (const k of [t.signalTimeUtc, t.entryTimeUtc, t.exitTimeUtc]) {
        assert.ok(k.endsWith('Z') && k.includes('T'), k);
      }
    }
  });

  test('invalid payloads fail loudly with reasons, never partial data', () => {
    assert.ok(validatePythonResult({}).length > 0);
    assert.ok(validatePythonResult({ ...archivedPayload, trades: [{ direction: 'UP' }] }).length > 0);
    assert.throws(() => mapPythonResultToBacktestRun({} as PythonResult), /adapter validation/);
  });

  test('no account balance or return is synthesized for archived runs', () => {
    const candidate = mapPythonResultToBacktestRun(archivedPayload) as {
      metrics: { returnPct: null; maxDrawdownPct: null };
      equity: { balance?: number; pnlCurrency?: number }[];
    };
    assert.equal(candidate.metrics.returnPct, null);
    assert.equal(candidate.metrics.maxDrawdownPct, null);
    for (const p of candidate.equity) {
      assert.equal(p.balance, undefined);
      assert.equal(p.pnlCurrency, undefined);
    }
  });
});

describe('python-result-adapter.ts - archive boundary', () => {
  const byUrl: Record<string, unknown> = {
    '/adapters-run-manifest.json': {
      runs: [{ runId: 'run_0001', file: 'runs/run_0001/result.json', label: 'run_0001' }],
    },
    '/runs-run_0001-result.json': archivedPayload,
  };
  const stubFetch = (async (url: string | URL | Request) => {
    const key = String(url);
    if (key in byUrl) {
      return { ok: true, status: 200, json: async () => byUrl[key] } as Response;
    }
    return { ok: false, status: 404, json: async () => ({}) } as Response;
  }) as unknown as typeof fetch;

  test('resolveRunFile maps archive paths to served public URLs', () => {
    assert.equal(resolveRunFile('runs/run_0001/result.json'), '/runs-run_0001-result.json');
    assert.equal(resolveRunFile('/x.json'), '/x.json');
  });

  test('loadArchivedRun returns a validated BacktestRun for run_0001', async () => {
    const result = await loadArchivedRun('run_0001', stubFetch);
    assert.ok(result.ok, result.ok ? '' : result.error);
    if (!result.ok) return;
    assert.equal(result.run.dataMode, 'live');
    assert.equal(result.run.trades.length, 7);
  });

  test('listArchivedRuns keeps newest-first manifest order (createdAt desc)', async () => {
    const manifestFetch = (async () => ({
      ok: true,
      status: 200,
      json: async () => ({
        runs: [
          { runId: 'run_0001', file: 'runs/run_0001/result.json', label: 'old' },
          { runId: 'run_b', file: 'runs/run_b/result.json', label: 'new', createdAt: '2026-10-12T00:00:02Z' },
          { runId: 'run_a', file: 'runs/run_a/result.json', label: 'newer', createdAt: '2026-10-12T00:00:01Z' },
        ],
      }),
    })) as unknown as typeof fetch;
    const runs = await listArchivedRuns(manifestFetch);
    assert.deepEqual(runs.map((r) => r.runId), ['run_b', 'run_a', 'run_0001']);
  });

  test('boot selection: manifest head wins over any stale stored run id', async () => {
    // Regression for the v1.0.1 startup contract:
    //   1. active job  2. newest successful valid run  3. empty.
    // localStorage is never authoritative: with run_old persisted, run_new
    // (manifest head) must still be selected.
    const manifestFetch = (async () => ({
      ok: true,
      status: 200,
      json: async () => ({
        runs: [
          { runId: 'run_new', file: 'runs/run_new/result.json', label: 'new', createdAt: '2026-10-12T00:00:02Z' },
          { runId: 'run_old', file: 'runs/run_old/result.json', label: 'old', createdAt: '2026-10-12T00:00:01Z' },
          { runId: 'run_0001', file: 'runs/run_0001/result.json', label: 'baseline' },
        ],
      }),
    })) as unknown as typeof fetch;
    const runs = await listArchivedRuns(manifestFetch);
    const storedId = 'run_old'; // stale persisted selection
    // Mirrors the boot selector in app/run-context.tsx: head wins, storage
    // is not consulted.
    const pick = runs.length ? runs[0].runId : null;
    assert.equal(pick, 'run_new');
    assert.notEqual(pick, storedId);
    assert.ok(!runs.some((r) => /stopped|failed|incomplete/i.test(r.runId)),
      'stopped/failed/incomplete jobs never enter the manifest list');
  });

  test('boot selection: deleted newest falls back to next valid run', async () => {
    const manifestFetch = (async () => ({
      ok: true,
      status: 200,
      json: async () => ({
        runs: [
          { runId: 'run_old', file: 'runs/run_old/result.json', label: 'old', createdAt: '2026-10-12T00:00:01Z' },
          { runId: 'run_0001', file: 'runs/run_0001/result.json', label: 'baseline' },
        ],
      }),
    })) as unknown as typeof fetch;
    const runs = await listArchivedRuns(manifestFetch);
    const pick = runs.length ? runs[0].runId : null;
    assert.equal(pick, 'run_old');
  });

  test('boot selector never reads the legacy stored-run key', () => {
    const src = readFileSync(join(labRoot, 'app', 'run-context.tsx'), 'utf-8');
    assert.ok(!src.includes('qbl:lastRunId'), 'legacy authoritative key must be gone');
    // The boot effect (up to its dependency close) must not consult storage;
    // the later hint-writer effect may persist a non-authoritative view hint.
    const bootStart = src.indexOf('const booted = useRef(false)');
    const bootEnd = src.indexOf('}, [refreshArchived])');
    assert.ok(bootStart >= 0 && bootEnd > bootStart, 'boot effect must exist');
    const bootBlock = src.slice(bootStart, bootEnd);
    assert.ok(!/localStorage\s*\.\s*(getItem|setItem|removeItem)/.test(bootBlock),
      'boot pick must not consult localStorage');
    assert.ok(bootBlock.includes('const pick = runs.length'),
      'boot selects the manifest head');
  });

  test('unknown runs and missing files return errors, never throw', async () => {
    const unknown = await loadArchivedRun('run_nope', stubFetch);
    assert.ok(!unknown.ok);
    const missing = await loadArchivedRun(
      'run_0001',
      (async () => ({ ok: false, status: 404, json: async () => ({}) })) as unknown as typeof fetch,
    );
    assert.ok(!missing.ok);
  });
});
