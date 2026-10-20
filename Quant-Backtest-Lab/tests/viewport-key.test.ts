import { test } from 'node:test';
import assert from 'node:assert/strict';
import { viewportKey } from '../app/viewport-key.js';

test('archived runs are identified by run id, never by candle data', () => {
  // Two different runs can share the same last candle timestamp; the key must
  // still distinguish them, so it is built from the archived run id.
  assert.equal(viewportKey('run_a'), 'run:run_a');
  assert.equal(viewportKey('run_b'), 'run:run_b');
  assert.notEqual(viewportKey('run_a'), viewportKey('run_b'));
});

test('fixture/demo gets a stable, separate identity', () => {
  assert.equal(viewportKey(null), 'fixture:demo');
  assert.equal(viewportKey(undefined), 'fixture:demo');
  assert.notEqual(viewportKey(null), viewportKey('run_a'));
});

test('an optional scope (trade) re-initialises the viewport', () => {
  assert.equal(viewportKey('run_a', 7), viewportKey('run_a', 7));
  assert.notEqual(viewportKey('run_a', 7), viewportKey('run_a', 8));
  assert.equal(viewportKey(null, 7), 'fixture:demo:7');
});
