/**
 * Account / economics UI - source-level assertions (no browser).
 * Verifies the optional account model is wired through the existing visual
 * system and that account comparison stays honest.
 */
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { test, describe } from 'node:test';
import { strict as assert } from 'node:assert';

const here = path.dirname(fileURLToPath(import.meta.url));
const app = (name) => readFileSync(path.join(here, '..', 'app', name), 'utf-8');

describe('account UI wiring', () => {
  test('real-run form exposes an optional, disabled-by-default account model', () => {
    const src = app('real-run-panel.tsx');
    assert.ok(src.includes('Enable account model'));
    assert.ok(src.includes('Account currency'));
    assert.ok(src.includes('Account starting balance'));
    assert.ok(src.includes('Account position size lots'));
    assert.ok(src.includes('Account commission per lot'));
    assert.ok(src.includes('Instrument contract size'));
    assert.ok(src.includes('Instrument quote currency'));
    // disabled by default and only sent when enabled
    assert.ok(/useState\(false\)/.test(src));
    assert.ok(src.includes('accountEnabled ?'));
    assert.ok(src.includes('commission_per_lot_round_turn'));
  });

  test('metrics bar surfaces account values only when accountReady', () => {
    const src = app('metrics-bar.tsx');
    assert.ok(src.includes('Net Account PnL'));
    assert.ok(src.includes('Realized DD'));
    assert.ok(src.includes('accountReady'));
    assert.ok(src.includes('accountDrawdown'));
    assert.ok(src.includes('returnPct'));
  });

  test('comparison gates account best/worst on compatibility and shows warnings', () => {
    const src = app('run-comparison.tsx');
    assert.ok(src.includes('account_comparable'));
    assert.ok(src.includes('bestWorstBy'));
    assert.ok(src.includes('Account terms'));
    assert.ok(src.includes('result.warnings.map'));
    assert.ok(src.includes('Not available'));
  });

  test('equity chart distinguishes price PnL from realized account balance', () => {
    const src = app('charts.tsx');
    assert.ok(src.includes("mode === 'Balance' && !accountReady"));
    assert.ok(src.includes('realized') || src.includes('Balance · account currency'));
  });

  test('readiness gates the account step on accountReady', () => {
    const src = app('readiness.ts');
    assert.ok(src.includes('run.account.accountReady === true'));
    assert.ok(src.includes('Not available'));
  });
});
