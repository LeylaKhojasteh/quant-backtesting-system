/**
 * UI verification tests - source-level assertions.
 * These check that the source code meets the demo-mode requirements
 * without needing to run a browser.
 */
import { readdir, readFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { test, describe } from 'node:test';
import { strict as assert } from 'node:assert';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const appDir = path.resolve(__dirname, '..', 'app');

async function readAllTsxFiles() {
  const files = new Map();
  async function walk(dir) {
    const entries = await readdir(dir, { withFileTypes: true });
    for (const entry of entries) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        await walk(full);
      } else if (entry.name.endsWith('.tsx') || entry.name.endsWith('.ts')) {
        const content = await readFile(full, 'utf-8');
        files.set(full, content);
      }
    }
  }
  await walk(appDir);
  return files;
}

describe('UI verification - demo mode requirements', () => {
  let allFiles;

  test('setup: read all app source files', async () => {
    allFiles = await readAllTsxFiles();
    assert.ok(allFiles.size > 0);
  });

test('DEMO badge exists in page.tsx', () => {
    const page = allFiles.get(path.join(appDir, 'page.tsx')) || '';
    assert.ok(page.includes('demo-badge'));
    assert.ok(page.includes('DEMO'));
  });

  test('No fabricated balance calculation (10000 + PnL) in app source', () => {
    let found = false;
    for (const [file, content] of allFiles) {
      if (content.includes('10000') && content.includes('enriched.reduce')) {
        if (content.includes('10000+enriched.reduce') || content.includes('10000 + enriched.reduce')) {
          console.warn('Found fabricated balance pattern in ' + file);
          found = true;
        }
      }
      if (content.includes('START_BALANCE')) {
        console.warn('Found START_BALANCE in ' + file);
        found = true;
      }
    }
    assert.ok(!found, 'Found fabricated balance patterns in source');
  });

  test('No 99.12% modeling quality displayed as real metric', () => {
    let found = false;
    for (const [file, content] of allFiles) {
      if (content.includes('99.12%') || content.includes('99.12</b>') || content.includes('value={99.12}')) {
        console.warn('Found 99.12% display in ' + file);
        found = true;
      }
    }
    assert.ok(!found, 'Found 99.12% displayed as real metric');
  });

  test('No MT5/MQL5/metatrader/OnTester/Strategy Tester Report/Field reference terminology', () => {
    const forbidden = [
      /MT5/i,
      /MQL5/i,
      /metatrader/i,
      /OnTester/i,
      /Strategy Tester Report/i,
      /Field reference/i,
    ];
    let found = false;
    for (const [file, content] of allFiles) {
      for (const pattern of forbidden) {
        if (pattern.test(content)) {
          console.warn('Found forbidden term ' + pattern + ' in ' + file);
          found = true;
        }
      }
    }
    assert.ok(!found, 'Found forbidden terminology in source');
  });

  test('Chart captions use clear labels with a price-terms marker', () => {
    const charts = allFiles.get(path.join(appDir, 'charts.tsx')) || '';
    assert.ok(charts.includes('Cumulative Trade PnL'));
    assert.ok(charts.includes('Closed-Trade Drawdown'));
    assert.ok(charts.includes('Price terms'));
  });

  test('CSV export metadata matches the run source (no seeded-demo on real runs)', () => {
    const page = allFiles.get(path.join(appDir, 'page.tsx')) || '';
    // Real (archived) exports must not carry seeded/demo metadata.
    assert.ok(page.includes("dataMode=${archived?'live':'seeded-demo'}"));
    // The old buggy line hardcoded a second seeded-demo field for every export.
    assert.ok(!page.includes('`dataMode=seeded-demo`,'));
    assert.ok(page.includes('.csv'));
  });

  test('Report data shows Pending for account-level metrics', () => {
    const reportData = allFiles.get(path.join(appDir, 'report-data.ts')) || '';
    assert.ok(reportData.includes('Pending'));
    assert.ok(reportData.includes('accountUnavailableReasons'));
  });

  test('UTC validation rejects +00:00 offset', () => {
    const utc = allFiles.get(path.join(appDir, 'utc.ts')) || '';
    assert.ok(utc.includes('UTC_ISO_RE') || utc.includes('offset: false'));
  });

  test('RunProvider is mounted in layout.tsx', () => {
    const layout = allFiles.get(path.join(appDir, 'layout.tsx')) || '';
    assert.ok(layout.includes('RunProvider'));
    assert.ok(layout.includes('{children}') || layout.includes('children}'));
  });

  test('Equity chart Balance mode is disabled/compact when account data is unavailable', () => {
    const charts = allFiles.get(path.join(appDir, 'charts.tsx')) || '';
    assert.ok(charts.includes("mode === 'Balance' && !accountReady"));
    assert.ok(charts.includes('equity-unavailable'));
    assert.ok(!charts.includes('Balance — Not available'), 'the oversized empty state must be removed');
    const page = allFiles.get(path.join(appDir, 'page.tsx')) || '';
    assert.ok(page.includes("disabledOptions={runData?.account.accountReady?undefined:['Balance']}"), 'Balance must be disabled when unavailable');
  });

  test('DataQualityPanel uses "Demo / not measured" and "Seeded example"', () => {
    const dq = allFiles.get(path.join(appDir, 'data-quality.tsx')) || '';
    assert.ok(dq.includes('Demo / not measured'));
    assert.ok(dq.includes('seeded example'));
    assert.ok(!dq.includes('99.12%'));
    assert.ok(!dq.includes('Verified reference'));
  });

  test('PerformanceQuality shows Not available with reasons for account metrics', () => {
    const as = allFiles.get(path.join(appDir, 'analytics-section.tsx')) || '';
    assert.ok(as.includes('Not available'));
    assert.ok(as.includes('pending: true') || as.includes('pending:'));
  });

  test('ResearchAnalytics uses useRun and enrichRunTrades', () => {
    const as = allFiles.get(path.join(appDir, 'analytics-section.tsx')) || '';
    assert.ok(as.includes('useRun()'));
    assert.ok(as.includes('enrichRunTrades'));
  });

  test('No magic number 5 for R - uses stopDistance from run', () => {
    const report = allFiles.get(path.join(appDir, 'report.tsx')) || '';
    assert.ok(report.includes('stopDist') || report.includes('stopDistance'));
    assert.ok(!report.includes('/ 5') || report.includes('stopDist'));
    const reportData = allFiles.get(path.join(appDir, 'report-data.ts')) || '';
    assert.ok(reportData.includes('stopDistance'));
  });

  test('DataReadiness bar is mounted and never Verified/ready-green in demo', () => {
    const readiness = allFiles.get(path.join(appDir, 'data-readiness.tsx')) || '';
    const logic = allFiles.get(path.join(appDir, 'readiness.ts')) || readiness;
    assert.ok(readiness.includes('DataReadinessBar'));
    assert.ok(readiness.includes('useRun()'));
    assert.ok(!logic.includes("'Verified'") && !logic.includes('"Verified"'));
    assert.ok(logic.includes('Seeded demo'));
    assert.ok(logic.includes('Not measured'));
    assert.ok(logic.includes('Not available'));
    assert.ok(!logic.includes('Result reliability'), 'reliability row must be removed');
    const page = allFiles.get(path.join(appDir, 'page.tsx')) || '';
    assert.ok(page.includes('<DataReadinessBar'));
  });

  test('Equity panel uses price-term / realized-balance titles', () => {
    const page = allFiles.get(path.join(appDir, 'page.tsx')) || '';
    assert.ok(page.includes('Cumulative Trade PnL'));
    assert.ok(page.includes('Realized Account Balance'));
    assert.ok(!page.includes('title="Equity Curve"'), 'Equity Curve title must be gone');
    const charts = allFiles.get(path.join(appDir, 'charts.tsx')) || '';
    assert.ok(charts.includes('Realized Account Balance'));
  });

  test('Live activity bar polls jobs without fake progress', () => {
    const bar = allFiles.get(path.join(appDir, 'run-activity-bar.tsx')) || '';
    assert.ok(bar.includes('Backtest running'));
    assert.ok(bar.includes('heartbeat'));
    // No rendered progress affordances: no width-driven bars, no ETA text.
    assert.ok(!bar.includes('progress-bar') && !bar.includes('progress_fill'));
    assert.ok(!/\bETA\b/i.test(bar), 'no fake ETA in the activity bar');
    assert.ok(!/estimated\s+time/i.test(bar), 'no fake time estimate in the activity bar');
    const ctx = allFiles.get(path.join(appDir, 'run-job-context.tsx')) || '';
    assert.ok(ctx.includes('/run-jobs/active'), 'reload reconnects to the active job');
    assert.ok(ctx.includes('1000'), 'polls at ~1 Hz');
    const panel = allFiles.get(path.join(appDir, 'real-run-panel.tsx')) || '';
    assert.ok(panel.includes('startJob') || panel.includes('startRunJob'), 'form starts async jobs');
  });

  test('Demo replay is fixture-gated, not a product UI mode', () => {
    const page = allFiles.get(path.join(appDir, 'page.tsx')) || '';
    assert.ok(!page.includes('Run Demo Replay'), 'Run Demo Replay must be removed from the product UI');
    assert.ok(!page.includes('Demo Replay'), 'Demo Replay section must be removed from the product UI');
    assert.ok(page.includes('fixtureMode&&'), 'the Demo source must be fixture-gated');
    assert.ok(page.includes('empty-state'), 'a real-use empty state must exist');
    // the seeded fixture is retained for tests, but only via runner.ts
    const runner = allFiles.get(path.join(appDir, 'runner.ts')) || '';
    assert.ok(runner.includes('buildDemoRun'));
    const ctx = allFiles.get(path.join(appDir, 'run-context.tsx')) || '';
    assert.ok(ctx.includes("get('fixture')"), 'fixture must be mounted only through the explicit test hook');
  });

  test('Pending metrics use the shared PendingMetric component, never fake numbers', () => {
    const pm = allFiles.get(path.join(appDir, 'pending-metric.tsx')) || '';
    assert.ok(pm.includes('PendingMetric'));
    assert.ok(pm.includes('Required for calculation'));
    const metricsBar = allFiles.get(path.join(appDir, 'metrics-bar.tsx')) || '';
    assert.ok(metricsBar.includes('PendingMetric'));
    assert.ok(metricsBar.includes('accountUnavailableReasons') || metricsBar.includes('reasons'));
    const analytics = allFiles.get(path.join(appDir, 'analytics-section.tsx')) || '';
    assert.ok(analytics.includes('PendingMetric'));
    const report = allFiles.get(path.join(appDir, 'report.tsx')) || '';
    assert.ok(report.includes('PendingMetric'));
  });

  test('TradeTimeline renders TP/SL/EOD labels with UTC timestamps', () => {
    const timeline = allFiles.get(path.join(appDir, 'trade-timeline.tsx')) || '';
    const events = allFiles.get(path.join(appDir, 'timeline-events.ts')) || '';
    assert.ok(timeline.includes('TradeTimeline'));
    assert.ok(events.includes('tradeTimelineEvents'));
    assert.ok(events.includes('Take profit') || events.includes('TP'));
    assert.ok(events.includes('Stop loss') || events.includes('SL'));
    assert.ok(events.includes('End of data') || events.includes('EOD'));
    assert.ok(events.includes('UTC'));
    assert.ok(events.includes('Not supplied by run'));
    const page = allFiles.get(path.join(appDir, 'page.tsx')) || '';
    assert.ok(page.includes('<TradeTimeline'));
  });
});

// ---------------------------------------------------------------------------
// v1.0.2 — real market visualization & truthfulness (source-level guards)
// ---------------------------------------------------------------------------
describe('real market visualization (v1.0.2)', () => {
  let allFiles;
  const file = (name) => allFiles.get(path.join(appDir, name)) || '';

  test('setup: read app sources', async () => {
    allFiles = await readAllTsxFiles();
    assert.ok(allFiles.size > 0);
  });

  test('Price Chart candles come from the run API, not the demo fixture', () => {
    const charts = file('charts.tsx');
    // The real path must not import candle/tick market data from demo.ts.
    assert.ok(!/from '\.\/demo'/.test(charts), 'charts.tsx must not import demo market data');
    assert.ok(charts.includes('candles: Candle[]'), 'CandleChart takes real candles as a prop');
    assert.ok(charts.includes('Market candles unavailable'), 'explicit unavailable state, no demo fallback');
    const page = file('page.tsx');
    assert.ok(page.includes('useRunCandles'), 'page fetches real candles for the run');
    assert.ok(page.includes('chartCandles'), 'page passes real candles to the chart');
    assert.ok(page.includes('candles={chartCandles}'), 'CandleChart receives real candles');
  });

  test('Price Chart markers come from the current BacktestRun', () => {
    const page = file('page.tsx');
    assert.ok(page.includes('trades={sourceTrades}'), 'chart markers use loaded run trades');
    assert.ok(!/<CandleChart[^>]*trades=\{demoTrades\}/.test(page), 'never demo trades in the real chart');
  });

  test('inspector uses real candle context and real ticks', () => {
    const page = file('page.tsx');
    assert.ok(page.includes('tradeCandleWindow'), 'inspector candle window derived from the run timeframe');
    assert.ok(page.includes('useRunTicks'), 'inspector fetches real ticks');
    assert.ok(page.includes('candles={insCandles}'), 'inspector candle context is real');
    assert.ok(page.includes('ticks={insTicks}'), 'tick execution chart gets real ticks');
    const charts = file('charts.tsx');
    assert.ok(charts.includes('Execution ticks unavailable'), 'explicit tick unavailable state');
  });

  test('signalAvailableUtc is preserved and no hardcoded M15 arithmetic remains', () => {
    const page = file('page.tsx');
    assert.ok(page.includes('signalAvailable:t.signalAvailableUtc?Date.parse'), 'real signalAvailable mapped from archive');
    assert.ok(page.includes('trade.signalAvailable!==undefined'), 'summary uses the archived value');
    assert.ok(!page.includes('trade.signal+900000'), 'no hardcoded 900000 in the inspector');
    assert.ok(!page.includes('+900000'), 'no hardcoded 15-minute offset anywhere in page.tsx');
    const charts = file('charts.tsx');
    assert.ok(!charts.includes('M15_MS'), 'no hardcoded M15 slot constant');
    assert.ok(!charts.includes('+ M15_MS'), 'no signal + M15 arithmetic');
  });

  test('timeframe mapping supports M1..H4 (no hidden M15 assumption)', () => {
    const market = file('run-market.ts');
    for (const tf of ["'1min': 60_000", "'5min': 300_000", "'15min': 900_000", "'30min': 1_800_000", "'1h': 3_600_000", "'4h': 14_400_000"]) {
      assert.ok(market.includes(tf), `timeframeMs must map ${tf}`);
    }
  });

  test('Data Explorer raw ticks are real (no demo tick generator)', () => {
    const page = file('page.tsx');
    assert.ok(page.includes('<RawTickPreview'), 'raw tick preview mounted');
    assert.ok(/function RawTickPreview\(\{runId,anchor\}/.test(page), 'RawTickPreview is run-scoped');
    assert.ok(!page.includes('ticks(trades[0]'), 'no demo tick generator in the real path');
    assert.ok(page.includes('Raw ticks unavailable'), 'explicit unavailable state');
  });

  test('Signal Quality uses backend signals_received, never a candle count', () => {
    const analytics = file('analytics.ts');
    assert.ok(!analytics.includes('totalSignalCount'), 'candle-derived signal count removed');
    assert.ok(!/candles\.length/.test(analytics), 'no candle-count signal inference');
    const section = file('analytics-section.tsx');
    assert.ok(section.includes('run?.execution?.signalsReceived'), 'real backend signal count');
    assert.ok(!section.includes('run?.dataQuality.candles ?? rows.length'), 'no candle-count fallback');
  });

  test('fake analytics/report values are gone; N/A instead', () => {
    const reportData = file('report-data.ts');
    assert.ok(!reportData.includes('124 ms'), 'fake signal-to-entry delay removed');
    assert.ok(!reportData.includes('14 / 14'), 'fake fill-check count removed');
    assert.ok(!reportData.includes('Python not connected'), 'stale engine copy removed');
    assert.ok(reportData.includes("'N/A'") || reportData.includes('N/A'), 'N/A used for unavailable values');
    assert.ok(reportData.includes('signalsReceived'), 'execution signal count surfaced');
  });

  test('stale demo product copy is gone from the real path', () => {
    const panel = file('real-run-panel.tsx');
    assert.ok(!panel.includes('Demo mode still works'), 'stale fallback copy removed');
    assert.ok(panel.includes('Python Bridge Offline'), 'honest offline state');
    const quality = file('data-quality.tsx');
    assert.ok(quality.includes('run.execution?.signalsReceived'), 'signals from engine metadata');
  });

  test('normal real path does not source market data from demo.ts', () => {
    // Real-run components must not import market/execution generators from demo.
    for (const name of ['charts.tsx', 'analytics-section.tsx', 'report.tsx', 'report-data.ts', 'trade-explorer.tsx', 'trade-timeline.tsx', 'metrics-bar.tsx', 'excursion.tsx', 'event-ribbon.tsx']) {
      const src = file(name);
      assert.ok(!/from '\.\/demo'/.test(src), `${name} must not import demo.ts`);
    }
    // page.tsx may import the fixture only for the explicit ?fixture=demo path.
    const page = file('page.tsx');
    assert.ok(page.includes("from './strategy-definitions'"), 'neutral helpers moved out of demo');
    assert.ok(page.includes('trades as demoTrades'), 'demo trades aliased as fixture-only');
  });

  test('demo fixture stays isolated behind the explicit ?fixture=demo hook', () => {
    const runner = file('runner.ts');
    assert.ok(runner.includes('fixture') || runner.includes('seedTrades'), 'fixture builder retained');
    const ctx = file('run-context.tsx');
    assert.ok(ctx.includes("get('fixture')"), 'fixture gated by explicit test hook');
  });
});

// ---------------------------------------------------------------------------
// v1.0.2 QA fixes — blank chart, run range display, stale job, PnL labels
// ---------------------------------------------------------------------------
describe('post-QA fixes (v1.0.2)', () => {
  let allFiles;
  const file = (name) => allFiles.get(path.join(appDir, name)) || '';

  test('setup: read app sources', async () => {
    allFiles = await readAllTsxFiles();
    assert.ok(allFiles.size > 0);
  });

  test('chart size is measured by a callback ref (blank-chart fix)', () => {
    const charts = file('charts.tsx');
    // Object ref + []-dep effect captured the null loading render and never
    // observed the svg, leaving real runs blank. Guard the callback-ref form.
    assert.ok(/useCallback\(\s*\(el: SVGSVGElement \| null\)/.test(charts),
      'useElementSize must use a callback ref');
    assert.ok(!/const ref = useRef<SVGSVGElement \| null>\(null\)/.test(charts),
      'object svg ref must be gone');
    assert.ok(charts.includes('observerRef.current.disconnect()'), 'observer re-attached/detached with the node');
  });

  test('results header shows the requested range and a timeframe label', () => {
    const page = file('page.tsx');
    assert.ok(page.includes('utcDateRange(runData.dateRange.startUtc,runData.dateRange.endUtc)'),
      'header uses the requested range');
    assert.ok(page.includes('timeframeLabel(runData.instrument.timeframe)'), 'display timeframe label (H1)');
    assert.ok(page.includes('Observed coverage'), 'observed coverage shown separately');
    assert.ok(page.includes('Requested range'), 'requested range labelled distinctly');
    const fmt = file('format.ts');
    assert.ok(fmt.includes('utcDateRange') && fmt.includes('timeframeLabel'));
    assert.ok(fmt.includes("'1h': 'H1'"), 'H1 label mapping present');
  });

  test('requested range and observed coverage are separate contracts', () => {
    const py = file('python-result.ts');
    assert.ok(py.includes('requested_start || payload.config.start'), 'requested falls back to observed');
    assert.ok(py.includes('dataCoverage'), 'observed coverage mapped separately');
    const schema = file('schema.ts');
    assert.ok(schema.includes('dataCoverage'), 'dataCoverage in the run schema');
  });

  test('PnL x-axis thins labels but keeps every data point', () => {
    const charts = file('charts.tsx');
    assert.ok(charts.includes('showLabel'), 'label thinning present');
    assert.ok(charts.includes('stride'), 'stride-based label thinning');
    // The data-point circle is rendered unconditionally inside the map.
    assert.ok(/<circle cx=\{x\(i\)\} cy=\{y\(v\)\}/.test(charts), 'all points still drawn');
    assert.ok(/showLabel && \(\s*<text/.test(charts), 'only labels are gated');
  });

  test('sidebar tracking notice only shows while a job is active', () => {
    const panel = file('real-run-panel.tsx');
    assert.ok(panel.includes('{jobActive && activeJob && ('), 'tracking notice gated on active state');
    assert.ok(panel.includes('Tracking '), 'tracking text retained for active jobs');
    const ctx = file('run-job-context.tsx');
    // Terminal states are never "active".
    assert.ok(ctx.includes("new Set(['completed', 'failed', 'stopped'])"), 'terminal states set');
    assert.ok(ctx.includes('ACTIVE_STATES.has(job.state)'), 'active derived from active states only');
  });
});

// ---------------------------------------------------------------------------
// v1.0.3 — pip-based SL/TP
// ---------------------------------------------------------------------------
describe('pip-based SL/TP (v1.0.3)', () => {
  let allFiles;
  const file = (name) => allFiles.get(path.join(appDir, name)) || '';

  test('setup: read app sources', async () => {
    allFiles = await readAllTsxFiles();
    assert.ok(allFiles.size > 0);
  });

  test('SL/TP inputs are labelled and submitted in pips', () => {
    const panel = file('real-run-panel.tsx');
    assert.ok(panel.includes('Stop Loss (pips)'), 'Stop Loss labelled in pips');
    assert.ok(panel.includes('Take Profit (pips)'), 'Take Profit labelled in pips');
    assert.ok(panel.includes('stop_loss_pips: Number(stopPipsValue)'), 'sends stop_loss_pips');
    assert.ok(panel.includes('take_profit_pips: Number(takePipsValue)'), 'sends take_profit_pips');
    assert.ok(!/stop_distance: Number\(stop\)/.test(panel), 'no legacy price submit in the UI');
  });

  test('pips are converted via the instrument pip_size (no hardcoded symbol math)', () => {
    const panel = file('real-run-panel.tsx');
    assert.ok(panel.includes('instrumentSpec?.pip_size'), 'pip size read from the instrument');
    assert.ok(panel.includes('n * pipSize'), 'effective distance = pips x pip_size');
    assert.ok(panel.includes('Converted to price distance using the selected instrument'), 'tooltip present');
    // No XAUUSD-specific conversion constant in the panel.
    assert.ok(!/XAUUSD.*0\.01|0\.01.*XAUUSD/.test(panel), 'no hardcoded XAUUSD pip logic');
  });

  test('pip size is exposed on the instrument contract', () => {
    const client = file('bridge-client.ts');
    assert.ok(client.includes('pip_size: number | null'), 'InstrumentInfo.pip_size');
    assert.ok(client.includes('stop_loss_pips?: number'), 'request payload supports pip fields');
  });

  test('unknown pip size is surfaced, not silently converted', () => {
    const panel = file('real-run-panel.tsx');
    assert.ok(panel.includes('No pip size configured'), 'clear message when pip size is unknown');
  });
});

// ---------------------------------------------------------------------------
// v1.0.3 — Price Chart interaction & windowing
// ---------------------------------------------------------------------------
describe('price chart interaction & windowing (v1.0.3)', () => {
  let allFiles;
  const file = (name) => allFiles.get(path.join(appDir, name)) || '';

  test('setup: read app sources', async () => {
    allFiles = await readAllTsxFiles();
    assert.ok(allFiles.size > 0);
  });

  test('price axis uses visible candles and visible trades only', () => {
    const charts = file('charts.tsx');
    // The axis must never mix every run trade into the scale.
    assert.ok(!/trades\.flatMap\(\(t\) => \[t\.entry, t\.exit\]\)/.test(charts),
      'no axis built from ALL trades');
    assert.ok(charts.includes('visibleTrades'), 'axis/markers use the visible-window trades');
    assert.ok(charts.includes('const levels ='), 'levels computed from visible data');
  });

  test('only visible-window trade markers are rendered', () => {
    const charts = file('charts.tsx');
    assert.ok(charts.includes('(context && trade ? [trade] : visibleTrades)'),
      'marker overlay limited to visible trades');
  });

  test('a single visible-count drives zoom (wheel and buttons cannot cancel)', () => {
    const charts = file('charts.tsx');
    assert.ok(charts.includes('visibleCount'), 'single visible-count state');
    assert.ok(!/setZoom\(/.test(charts), 'no separate zoom state to cancel the count');
    assert.ok(charts.includes('setVisibleCount'), 'zoom in/out and wheel update the count');
  });

  test('drag pans from a synchronous ref and survives capture failure', () => {
    const charts = file('charts.tsx');
    assert.ok(charts.includes('dragStartRef.current === null'),
      'drag state comes from the ref, not render state');
    assert.ok(charts.includes('setPointerCapture'), 'pointer capture is used');
    assert.ok(/try \{\s*\n?\s*e\.currentTarget\.setPointerCapture/.test(charts),
      'pointer capture failure must not abort the drag');
    assert.ok(charts.includes('setPanOffset(next - centeredStart)'), 'drag updates the viewport');
  });

  test('trade selection centers the viewport through an explicit request', () => {
    const charts = file('charts.tsx');
    assert.ok(charts.includes('centerTarget'), 'chart accepts a centering request');
    assert.ok(charts.includes('onCenterTrade'), 'chart reports a centering request');
    const page = file('page.tsx');
    assert.ok(page.includes('centerTarget={centerTarget}'), 'page passes the centering request');
    assert.ok(page.includes('onCenterTrade={centerChartOnTrade}'), 'page handles centering');
    assert.ok(!page.includes('windowStartRef'), 'no ref-based external window control');
  });

  test('candle buffer is bounded and merged by timestamp', () => {
    const market = file('run-market.ts');
    assert.ok(market.includes('MAX_CANDLE_BUFFER = 1500'), 'buffer capped at 1500 candles');
    assert.ok(market.includes('INITIAL_CANDLE_WINDOW = 250'), 'initial window of 250 candles');
    assert.ok(market.includes('export function mergeCandles'), 'merge helper exists');
    assert.ok(market.includes('byTime.set(c.t, c)'), 'merge de-duplicates by timestamp');
    assert.ok(market.includes('loadMore'), 'edge loading exposed');
    assert.ok(market.includes('getRunCandles'), 'uses the existing run-scoped API');
  });

  test('no demo candles reach the real chart path', () => {
    const charts = file('charts.tsx');
    assert.ok(!/from '\.\/demo'/.test(charts), 'chart does not import demo market data');
    const market = file('run-market.ts');
    assert.ok(!/from '\.\/demo'/.test(market), 'market hook does not import demo data');
  });
});