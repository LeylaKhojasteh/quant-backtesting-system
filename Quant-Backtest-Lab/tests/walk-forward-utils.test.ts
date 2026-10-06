import { test, describe } from 'node:test';
import { strict as assert } from 'node:assert';
import { foldCount, generateFolds, totalCandidateExecutions } from '../app/walk-forward-utils.js';

describe('walk-forward-utils - deterministic folds and limits', () => {
  test('rolling folds are deterministic and non-overlapping in time', () => {
    const folds = generateFolds('2025-01-01', '2025-01-05', 2, 1, 1, 'rolling');
    assert.deepEqual(folds.map((f) => [f.trainStart, f.trainEnd, f.testStart, f.testEnd]), [
      ['2025-01-01', '2025-01-02', '2025-01-03', '2025-01-03'],
      ['2025-01-02', '2025-01-03', '2025-01-04', '2025-01-04'],
      ['2025-01-03', '2025-01-04', '2025-01-05', '2025-01-05'],
    ]);
    for (const f of folds) assert.ok(f.trainEnd < f.testStart, 'test must follow train');
    assert.deepEqual(generateFolds('2025-01-01', '2025-01-05', 2, 1, 1, 'rolling'), folds);
  });

  test('anchored folds keep the training start fixed', () => {
    const folds = generateFolds('2025-01-01', '2025-01-05', 2, 1, 1, 'anchored');
    assert.deepEqual(folds.map((f) => f.trainStart), ['2025-01-01', '2025-01-01', '2025-01-01']);
    assert.deepEqual(folds.map((f) => f.trainEnd), ['2025-01-02', '2025-01-03', '2025-01-04']);
  });

  test('invalid configuration yields no folds', () => {
    assert.deepEqual(generateFolds('2025-01-05', '2025-01-01', 2, 1, 1, 'rolling'), []);
    assert.deepEqual(generateFolds('2025-01-01', '2025-01-05', 0, 1, 1, 'rolling'), []);
    assert.deepEqual(generateFolds('2025-01-01', '2025-01-05', 2, 1, 1, 'bad' as never), []);
    assert.deepEqual(generateFolds('', '2025-01-05', 2, 1, 1, 'rolling'), []);
    // window larger than the range
    assert.deepEqual(generateFolds('2025-01-01', '2025-01-02', 5, 5, 1, 'rolling'), []);
  });

  test('foldCount and total candidate executions', () => {
    assert.equal(foldCount('2025-01-01', '2025-01-05', 2, 1, 1, 'rolling'), 3);
    assert.equal(totalCandidateExecutions(3, 4), 12);
    assert.equal(totalCandidateExecutions(3, 0), 0);
    assert.equal(totalCandidateExecutions(0, 4), 0);
  });
});
