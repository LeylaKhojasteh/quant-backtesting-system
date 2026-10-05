import { test, describe } from 'node:test';
import { strict as assert } from 'node:assert';
import {
  parseUtcDateTimeLocal,
  validateUtcRange,
  toUtcIso,
  isUtcIso,
  formatUtc,
  formatUtcMs,
} from '../app/utc.js';

describe('utc.ts - UTC time contract', () => {
  test('parseUtcDateTimeLocal accepts valid datetime-local and returns UTC ISO with Z', () => {
    const result = parseUtcDateTimeLocal('2025-01-31T22:00', 'start');
    assert.ok(result.ok);
    if (result.ok) {
      assert.ok(result.utc.endsWith('Z'));
      assert.equal(result.utc, '2025-01-31T22:00:00.000Z');
    }
  });

  test('parseUtcDateTimeLocal accepts seconds', () => {
    const result = parseUtcDateTimeLocal('2025-01-31T22:00:30', 'start');
    assert.ok(result.ok);
    if (result.ok) {
      assert.equal(result.utc, '2025-01-31T22:00:30.000Z');
    }
  });

  test('parseUtcDateTimeLocal rejects empty string', () => {
    const result = parseUtcDateTimeLocal('', 'start');
    assert.ok(!result.ok);
    if (!result.ok) assert.equal(result.error.code, 'empty');
  });

  test('parseUtcDateTimeLocal rejects malformed string', () => {
    const result = parseUtcDateTimeLocal('2025/01/31 22:00', 'start');
    assert.ok(!result.ok);
    if (!result.ok) assert.equal(result.error.code, 'malformed');
  });

  test('parseUtcDateTimeLocal rejects invalid date (month 13)', () => {
    const result = parseUtcDateTimeLocal('2025-13-01T00:00', 'start');
    assert.ok(!result.ok);
    if (!result.ok) assert.equal(result.error.code, 'invalid-date');
  });

  test('parseUtcDateTimeLocal rejects invalid date (day 40)', () => {
    const result = parseUtcDateTimeLocal('2025-01-40T00:00', 'start');
    assert.ok(!result.ok);
    if (!result.ok) assert.equal(result.error.code, 'invalid-date');
  });

  test('validateUtcRange accepts valid start < end', () => {
    const result = validateUtcRange('2025-01-31T00:00', '2025-01-31T22:00');
    assert.ok(result.ok);
    if (result.ok) {
      assert.ok(result.spanMs > 0);
      assert.ok(result.startUtc.endsWith('Z'));
      assert.ok(result.endUtc.endsWith('Z'));
    }
  });

  test('validateUtcRange rejects start after end', () => {
    const result = validateUtcRange('2025-01-31T22:00', '2025-01-31T00:00');
    assert.ok(!result.ok);
    if (!result.ok) assert.equal(result.error.code, 'start-after-end');
  });

  test('validateUtcRange rejects equal start and end', () => {
    const result = validateUtcRange('2025-01-31T00:00', '2025-01-31T00:00');
    assert.ok(!result.ok);
    if (!result.ok) assert.equal(result.error.code, 'start-after-end');
  });

  test('toUtcIso produces ISO string ending with Z', () => {
    const iso = toUtcIso(Date.UTC(2025, 0, 31, 22, 0, 0));
    assert.ok(iso.endsWith('Z'));
    assert.equal(iso, '2025-01-31T22:00:00.000Z');
  });

  test('isUtcIso accepts valid UTC ISO with Z', () => {
    assert.ok(isUtcIso('2025-01-31T22:00:00Z'));
    assert.ok(isUtcIso('2025-01-31T22:00:00.000Z'));
  });

  test('isUtcIso rejects offset form (+00:00)', () => {
    assert.ok(!isUtcIso('2025-01-31T22:00:00+00:00'));
  });

  test('isUtcIso rejects zone-less string', () => {
    assert.ok(!isUtcIso('2025-01-31T22:00:00'));
  });

  test('formatUtc includes UTC label', () => {
    const formatted = formatUtc('2025-01-31T22:00:00Z');
    assert.ok(formatted.includes('UTC'));
  });

  test('formatUtcMs formats epoch ms as UTC', () => {
    const formatted = formatUtcMs(Date.UTC(2025, 0, 31, 22, 0, 0));
    assert.ok(formatted.includes('UTC'));
  });
});