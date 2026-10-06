import { test, describe } from 'node:test';
import { strict as assert } from 'node:assert';
import {
  MC_HARD_MAX_SIMULATIONS,
  basisLabel,
  methodLabel,
  smallSampleWarning,
  validateSeed,
  validateSimulations,
} from '../app/monte-carlo-utils.js';

describe('monte-carlo-utils - validation, basis and warnings', () => {
  test('simulations validation enforces whole numbers and the hard maximum', () => {
    assert.deepEqual(validateSimulations('1000'), { ok: true, value: 1000 });
    assert.equal(validateSimulations('0').ok, false);
    assert.equal(validateSimulations('x').ok, false);
    assert.equal(validateSimulations('1.5').ok, false);
    const over = validateSimulations(String(MC_HARD_MAX_SIMULATIONS + 1));
    assert.equal(over.ok, false);
    assert.match(over.ok ? '' : over.error, /exceed/);
  });

  test('seed validation requires a whole number', () => {
    assert.deepEqual(validateSeed('0'), { ok: true, value: 0 });
    assert.deepEqual(validateSeed('-42'), { ok: true, value: -42 });
    assert.equal(validateSeed('1.2').ok, false);
    assert.equal(validateSeed('').ok, false);
  });

  test('small-sample warning is honest and never claims statistical strength', () => {
    assert.equal(smallSampleWarning(100), null);
    assert.equal(smallSampleWarning(30), null);
    assert.match(String(smallSampleWarning(7)), /Small sample/);
    assert.match(String(smallSampleWarning(1)), /at least 2/);
    assert.equal(smallSampleWarning(null), null);
  });

  test('basis and method labels distinguish price vs account', () => {
    assert.match(basisLabel('account'), /Account/);
    assert.match(basisLabel('price'), /price/i);
    assert.match(methodLabel('shuffle'), /without replacement/);
    assert.match(methodLabel('bootstrap'), /with replacement/);
  });
});
