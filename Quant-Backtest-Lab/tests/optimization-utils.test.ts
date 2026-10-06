import { test, describe } from 'node:test';
import { strict as assert } from 'node:assert';
import { candidateCount, parseGridValues, parseNumberList } from '../app/optimization-utils.js';

describe('optimization-utils - grid parsing and candidate count', () => {
  test('parseGridValues handles int / float / bool / string', () => {
    assert.deepEqual(parseGridValues('int', '1, 2 ,3'), [1, 2, 3]);
    assert.deepEqual(parseGridValues('float', '0.5, 1'), [0.5, 1]);
    assert.deepEqual(parseGridValues('bool', 'true, false'), [true, false]);
    assert.deepEqual(parseGridValues('string', 'both, long'), ['both', 'long']);
  });

  test('parseGridValues rejects malformed values and empty input', () => {
    assert.throws(() => parseGridValues('int', '1.5'), /integer/);
    assert.throws(() => parseGridValues('float', 'abc'), /number/);
    assert.throws(() => parseGridValues('bool', 'maybe'), /true\/false/);
    assert.throws(() => parseGridValues('int', '  '), /at least one/);
  });

  test('parseNumberList rejects non-positive and empty', () => {
    assert.deepEqual(parseNumberList('3, 5'), [3, 5]);
    assert.throws(() => parseNumberList('0'), /positive/);
    assert.throws(() => parseNumberList('-2'), /positive/);
    assert.throws(() => parseNumberList(''), /at least one/);
  });

  test('candidateCount multiplies the grid dimensions', () => {
    assert.equal(candidateCount({ a: [1, 2], b: [1, 2, 3] }, {}), 6);
    assert.equal(candidateCount({ a: [1, 2] }, { stop_distance: [3, 5] }), 4);
    assert.equal(candidateCount({}, {}), 1);
    assert.equal(candidateCount({ a: [] }, {}), 0);
  });
});
