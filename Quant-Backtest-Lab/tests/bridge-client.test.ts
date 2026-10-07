import { test, describe } from 'node:test';
import { strict as assert } from 'node:assert';
import {
  BridgeError,
  bridgeBaseUrl,
  compareRuns,
  deleteRun,
  deleteStrategy,
  getHealth,
  getRun,
  getStudy,
  importStrategy,
  listDatasets,
  listRuns,
  listStrategies,
  listStudies,
  publishRun,
  runReportUrl,
  startOptimization,
  submitRun,
  type BacktestRunRequestPayload,
} from '../app/bridge-client.js';

function jsonResponse(payload: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    text: async () => JSON.stringify(payload),
  } as unknown as Response;
}

describe('bridge-client.ts - local bridge HTTP boundary', () => {
  test('default base URL targets the local bridge', () => {
    assert.match(bridgeBaseUrl(), /^https?:\/\/127\.0\.0\.1:8765$/);
  });

  test('getHealth parses the health payload', async () => {
    const fetchImpl = (async () =>
      jsonResponse({ status: 'ok', service: 'quant-backtest-bridge', version: '0.1.0' })) as unknown as typeof fetch;
    const health = await getHealth(fetchImpl);
    assert.equal(health.status, 'ok');
    assert.equal(health.service, 'quant-backtest-bridge');
  });

  test('listStrategies returns the catalog array', async () => {
    let calledUrl = '';
    const fetchImpl = (async (url: string | URL | Request) => {
      calledUrl = String(url);
      return jsonResponse({
        strategies: [
          {
            id: 'candle_direction',
            name: 'Candle Direction',
            version: '1.0',
            description: 'd',
            source: 'builtin',
            file: null,
            parameters: {},
          },
        ],
      });
    }) as unknown as typeof fetch;
    const strategies = await listStrategies(fetchImpl);
    assert.equal(strategies.length, 1);
    assert.equal(strategies[0].name, 'Candle Direction');
    assert.ok(calledUrl.endsWith('/strategies'));
  });

  test('listStrategies tolerates a missing strategies field', async () => {
    const fetchImpl = (async () => jsonResponse({})) as unknown as typeof fetch;
    assert.deepEqual(await listStrategies(fetchImpl), []);
  });

  test('listDatasets returns the dataset catalog with ranges', async () => {
    let calledUrl = '';
    const fetchImpl = (async (url: string | URL | Request) => {
      calledUrl = String(url);
      return jsonResponse({
        datasets: [
          {
            dataset_id: 'xauusd_dukas_ticks',
            symbol: 'XAUUSD',
            provider: 'DUKAS',
            provider_label: 'Dukascopy',
            data_type: 'ticks',
            timezone: 'UTC',
            label: 'XAUUSD · Dukascopy',
            subtitle: 'Tick data · 2025',
            coverage_start: '2025-01-01 00:00:00+00:00',
            coverage_end: '2025-12-31 23:59:59+00:00',
            years: [2025],
            complete_years: [2025],
            partial_months: [],
            supported_timeframes: ['15min'],
            prepared_timeframes: ['15min'],
            tick_ready: true,
            default_start: '2025-01-01',
            default_end: '2025-12-31',
          },
        ],
      });
    }) as unknown as typeof fetch;
    const datasets = await listDatasets(fetchImpl);
    assert.equal(datasets.length, 1);
    assert.equal(datasets[0].symbol, 'XAUUSD');
    assert.equal(datasets[0].dataset_id, 'xauusd_dukas_ticks');
    assert.equal(datasets[0].default_start, '2025-01-01');
    assert.ok(calledUrl.endsWith('/datasets'));
  });

  test('listDatasets tolerates a missing datasets field', async () => {
    const fetchImpl = (async () => jsonResponse({})) as unknown as typeof fetch;
    assert.deepEqual(await listDatasets(fetchImpl), []);
  });

  test('submitRun POSTs the request payload and returns the response', async () => {
    const payload: BacktestRunRequestPayload = {
      strategy: { selector: 'candle_direction', parameters: {} },
      market: { symbol: 'XAUUSD', timeframe: '15min', data_source: 'ticks.csv' },
      execution: { stop_distance: 5, take_profit_distance: 10 },
      publish: false,
    };
    let method = '';
    let body = '';
    const fetchImpl = (async (_url: string | URL | Request, init?: RequestInit) => {
      method = init?.method ?? 'GET';
      body = String(init?.body ?? '');
      return jsonResponse({
        run_id: 'run_x',
        status: 'completed',
        strategy: { name: 'Candle Direction', version: '1.0', description: 'd' },
        parameters: {},
        market: { symbol: 'XAUUSD', timeframe: '15min', start: 'a', end: 'b' },
        execution: { stop_distance: 5, take_profit_distance: 10 },
        summary: { total_trades: 3, win_rate: 33.3, net_pnl: -1.0, signals_received: 9 },
        archive: { directory: 'runs/run_x', result: 'runs/run_x/result.json', report: 'runs/run_x/report.html' },
        published: false,
        published_file: null,
      });
    }) as unknown as typeof fetch;
    const result = await submitRun(payload, fetchImpl);
    assert.equal(method, 'POST');
    assert.deepEqual(JSON.parse(body), payload);
    assert.equal(result.run_id, 'run_x');
    assert.equal(result.summary.total_trades, 3);
  });

  test('importStrategy POSTs the file and returns the catalog entry', async () => {
    let method = '';
    let body = '';
    const fetchImpl = (async (_url: string | URL | Request, init?: RequestInit) => {
      method = init?.method ?? 'GET';
      body = String(init?.body ?? '');
      return jsonResponse({
        strategy: {
          id: 'strategies/ema.py',
          name: 'EMA Cross',
          version: 'import-1.0',
          description: 'd',
          source: 'file',
          file: 'strategies/ema.py',
          parameters: { fast_period: { type: 'int', default: 3 } },
        },
      });
    }) as unknown as typeof fetch;
    const entry = await importStrategy({ filename: 'ema.py', source: 'x = 1\n' }, fetchImpl);
    assert.equal(method, 'POST');
    assert.deepEqual(JSON.parse(body), { filename: 'ema.py', source: 'x = 1\n' });
    assert.equal(entry.name, 'EMA Cross');
    assert.equal(entry.parameters.fast_period.default, 3);
  });

  test('run library: listRuns / getRun / publish / delete / report url', async () => {
    const summary = {
      run_id: 'run_x', created_at: '2025-01-02T00:00:00Z',
      strategy: { name: 'Candle Direction', version: '1.0' },
      strategy_selector: 'candle_direction', strategy_file: null,
      symbol: 'XAUUSD', timeframe: '15min', start: '2025-01-02T00:00:00Z', end: '2025-01-02T03:00:00Z',
      total_trades: 7, win_rate: 57.14, net_pnl: 21.11, profit_factor: 2.38,
      report: true, published: false,
    };
    const calls: Array<{ url: string; method: string }> = [];
    const fetchImpl = (async (url: string | URL | Request, init?: RequestInit) => {
      const method = init?.method ?? 'GET';
      calls.push({ url: String(url), method });
      if (String(url).includes('/publish')) return jsonResponse({ run_id: 'run_x', published: 'p', wrote_manifest: true });
      if (method === 'DELETE') return jsonResponse({ run_id: 'run_x', deleted: true, removed_artifact: null, removed_manifest_entry: false });
      if (String(url).endsWith('/runs/run_x')) {
        return jsonResponse({ run: { ...summary, parameters: { a: 1 }, overrides: {}, description: 'd', execution: { stop_distance: 5, take_profit_distance: 10 }, market: { symbol: 'XAUUSD', timeframe: '15min', start: 's', end: 'e', data_source: 'd/x.csv', dataset: 'x.csv' }, selector: 'candle_direction', file: null } });
      }
      return jsonResponse({ runs: [summary] });
    }) as unknown as typeof fetch;

    const runs = await listRuns(fetchImpl);
    assert.equal(runs.length, 1);
    assert.equal(runs[0].published, false);
    const detail = await getRun('run_x', fetchImpl);
    assert.equal((detail.parameters as Record<string, number>).a, 1);
    assert.equal(detail.market.dataset, 'x.csv');
    const pub = await publishRun('run_x', fetchImpl);
    assert.equal(pub.wrote_manifest, true);
    const del = await deleteRun('run_x', fetchImpl);
    assert.equal(del.deleted, true);
    assert.ok(calls.some((c) => c.method === 'POST' && c.url.includes('/runs/run_x/publish')));
    assert.ok(calls.some((c) => c.method === 'DELETE' && c.url.endsWith('/runs/run_x')));
    assert.equal(runReportUrl('run_x'), `${bridgeBaseUrl()}/runs/run_x/report`);
  });

  test('deleteStrategy surfaces structured errors and encodes the selector', async () => {
    let calledUrl = '';
    const fetchImpl = (async (url: string | URL | Request) => {
      calledUrl = String(url);
      return jsonResponse({ error: { code: 'builtin_strategy_protected', message: 'nope' } }, 400);
    }) as unknown as typeof fetch;
    await assert.rejects(() => deleteStrategy('candle_direction', fetchImpl), (err: unknown) => {
      assert.ok(err instanceof BridgeError);
      assert.equal((err as BridgeError).code, 'builtin_strategy_protected');
      return true;
    });
    assert.ok(calledUrl.endsWith('/strategies/candle_direction'));
  });

  test('comparison + optimization client operations', async () => {
    const calls: string[] = [];
    const fetchImpl = (async (url: string | URL | Request, init?: RequestInit) => {
      const u = String(url);
      calls.push(`${init?.method ?? 'GET'} ${u}`);
      if (u.endsWith('/comparisons')) {
        return jsonResponse({
          runs: [{ run_id: 'run_a' }, { run_id: 'run_b' }],
          warnings: ['Symbols differ'],
          differences: { symbol: true, timeframe: false, date_range: false, strategy: false },
          equity: { run_a: [0, 1], run_b: [0, 2] },
          units: 'price-terms',
        });
      }
      if (u.endsWith('/optimizations') && (init?.method ?? 'GET') === 'POST') {
        return jsonResponse({ study: { study_id: 'study_abc', status: 'completed', candidates: [], ranking: [], best_run_id: null } });
      }
      if (u.endsWith('/optimizations')) {
        return jsonResponse({ studies: [{ study_id: 'study_abc', candidate_count: 4 }] });
      }
      return jsonResponse({ study: { study_id: 'study_abc', status: 'completed', candidates: [], ranking: [], best_run_id: null } });
    }) as unknown as typeof fetch;

    const comparison = await compareRuns(['run_a', 'run_b'], fetchImpl);
    assert.equal(comparison.runs.length, 2);
    assert.equal(comparison.warnings.length, 1);

    const study = await startOptimization({
      strategy: 'x',
      market: { symbol: 'XAUUSD', data_source: 'd.csv' },
      execution: { stop_distance: 5, take_profit_distance: 10 },
      strategy_grid: { a: [1, 2] },
      execution_grid: { stop_distance: [3, 5] },
      objective: { metric: 'net_pnl', direction: 'maximize' },
    }, fetchImpl);
    assert.equal(study.study_id, 'study_abc');

    const studies = await listStudies(fetchImpl);
    assert.equal(studies[0].candidate_count, 4);
    const reloaded = await getStudy('study_abc', fetchImpl);
    assert.equal(reloaded.study_id, 'study_abc');
    assert.ok(calls.some((c) => c.startsWith('POST') && c.includes('/optimizations')));
    assert.ok(calls.some((c) => c.includes('/optimizations/study_abc')));
  });

  test('structured bridge errors surface code and status', async () => {
    const fetchImpl = (async () =>
      jsonResponse(
        { error: { code: 'invalid_parameters', message: 'unknown strategy parameter' } },
        400,
      )) as unknown as typeof fetch;
    await assert.rejects(
      () =>
        submitRun(
          {
            strategy: { selector: 'x', parameters: { nope: 1 } },
            market: { symbol: 'XAUUSD', data_source: 'ticks.csv' },
          },
          fetchImpl,
        ),
      (err: unknown) => {
        assert.ok(err instanceof BridgeError);
        assert.equal((err as BridgeError).code, 'invalid_parameters');
        assert.equal((err as BridgeError).status, 400);
        return true;
      },
    );
  });
});
