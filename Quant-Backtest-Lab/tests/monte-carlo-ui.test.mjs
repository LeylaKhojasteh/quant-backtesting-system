/**
 * Monte Carlo UI - source-level assertions (no browser).
 * Verifies the workflow is wired into the Research Workspace and keeps the
 * shuffle/bootstrap semantics and price/account labeling honest.
 */
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { test, describe } from 'node:test';
import { strict as assert } from 'node:assert';

const here = path.dirname(fileURLToPath(import.meta.url));
const app = (name) => readFileSync(path.join(here, '..', 'app', name), 'utf-8');

describe('monte carlo UI wiring', () => {
  test('workflow is a tab in the existing Research Workspace', () => {
    const lib = app('run-library.tsx');
    assert.ok(lib.includes("value=\"montecarlo\""));
    assert.ok(lib.includes('MonteCarloPanel'));
    assert.ok(lib.includes('Monte Carlo'));
  });

  test('panel exposes source/method/simulations/seed and basis preview', () => {
    const src = app('monte-carlo-panel.tsx');
    assert.ok(src.includes('Monte Carlo source run'));
    assert.ok(src.includes('Monte Carlo method'));
    assert.ok(src.includes('Monte Carlo simulations'));
    assert.ok(src.includes('Monte Carlo seed'));
    assert.ok(src.includes('Monte Carlo basis'));
    assert.ok(src.includes('smallSampleWarning'));
    assert.ok(src.includes('Run Monte Carlo'));
  });

  test('shuffle terminal-constant semantics are explicit', () => {
    const src = app('monte-carlo-panel.tsx');
    assert.ok(src.includes("study.method === 'shuffle'"));
    assert.ok(src.includes('identical in every path'));
    assert.ok(src.includes('terminal variation is not shown'));
  });

  test('bootstrap terminal distribution and negative probability are shown', () => {
    const src = app('monte-carlo-panel.tsx');
    assert.ok(src.includes("study.method === 'bootstrap'"));
    assert.ok(src.includes('Terminal PnL'));
    assert.ok(src.includes('probability_terminal_negative'));
    assert.ok(src.includes('not a guaranteed'));
  });

  test('price vs account labels stay truthful', () => {
    const src = app('monte-carlo-panel.tsx');
    assert.ok(src.includes('price terms'));
    assert.ok(src.includes('Realized balance drawdown %') || src.includes('account currency'));
    assert.ok(src.includes('study.labels.path'));
  });

  test('missing source run is marked and does not crash', () => {
    const src = app('monte-carlo-panel.tsx');
    assert.ok(src.includes('SOURCE RUN MISSING'));
    assert.ok(src.includes('sourceMissing'));
    assert.ok(src.includes('Open source run'));
  });

  test('study history reloads archived studies without rerun', () => {
    const src = app('monte-carlo-panel.tsx');
    assert.ok(src.includes('listMonteCarlo'));
    assert.ok(src.includes('getMonteCarlo'));
    assert.ok(src.includes('Monte Carlo History'));
  });

  test('bridge client exposes structured monte carlo calls', () => {
    const src = app('bridge-client.ts');
    assert.ok(src.includes('startMonteCarlo'));
    assert.ok(src.includes('listMonteCarlo'));
    assert.ok(src.includes('getMonteCarlo'));
    assert.ok(src.includes('/montecarlo'));
  });
});
