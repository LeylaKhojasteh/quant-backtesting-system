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

  test('Cumulative PnL caption mentions "price terms — not account currency"', () => {
    const charts = allFiles.get(path.join(appDir, 'charts.tsx')) || '';
    assert.ok(charts.includes('price terms — not account currency'));
  });

  test('CSV export includes dataMode=seeded-demo and demo in filename', () => {
    const page = allFiles.get(path.join(appDir, 'page.tsx')) || '';
    assert.ok(page.includes('dataMode=seeded-demo'));
    assert.ok(page.includes('demo') && page.includes('.csv'));
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

  test('Equity chart Balance mode has empty state for demo', () => {
    const charts = allFiles.get(path.join(appDir, 'charts.tsx')) || '';
    assert.ok(charts.includes('Balance — Not available') || charts.includes('Balance — Not available'));
    assert.ok(charts.includes('accountUnavailableReasons'));
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
    assert.ok(logic.includes('Not connected'));
    assert.ok(logic.includes('Visual preview only'));
    const page = allFiles.get(path.join(appDir, 'page.tsx')) || '';
    assert.ok(page.includes('<DataReadinessBar'));
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