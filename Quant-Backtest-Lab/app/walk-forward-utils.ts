/**
 * Pure helpers for the Walk-Forward configuration UI. Mirrors the Python
 * `src.walk_forward.generate_folds` algorithm so the fold preview shown before
 * running matches what the backend will produce. Kept React-free so it is
 * directly unit-testable.
 */

export type WindowMode = 'rolling' | 'anchored';

export interface Fold {
  index: number;
  trainStart: string;
  trainEnd: string;
  testStart: string;
  testEnd: string;
}

function parseDate(text: string): Date | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec((text ?? '').trim());
  if (!match) return null;
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const d = new Date(Date.UTC(year, month - 1, day));
  if (d.getUTCFullYear() !== year || d.getUTCMonth() !== month - 1 || d.getUTCDate() !== day) return null;
  return d;
}

function addDays(d: Date, n: number): Date {
  return new Date(d.getTime() + n * 86_400_000);
}

function iso(d: Date): string {
  return d.toISOString().slice(0, 10);
}

function positiveInt(value: number): boolean {
  return Number.isInteger(value) && value >= 1;
}

/**
 * Deterministic chronological folds. Rolling moves the training window forward;
 * anchored keeps the training start fixed while the end expands. A fold is
 * emitted only when its test window fits inside the overall range.
 */
export function generateFolds(
  overallStart: string,
  overallEnd: string,
  trainDays: number,
  testDays: number,
  stepDays: number,
  mode: WindowMode,
): Fold[] {
  const start = parseDate(overallStart);
  const end = parseDate(overallEnd);
  if (!start || !end || end < start) return [];
  if (!positiveInt(trainDays) || !positiveInt(testDays) || !positiveInt(stepDays)) return [];
  if (mode !== 'rolling' && mode !== 'anchored') return [];

  const folds: Fold[] = [];
  let cursor = start;
  let index = 0;
  // Bound the loop defensively; the backend enforces the real fold limit.
  while (index < 10_000) {
    const trainStart = mode === 'anchored' ? start : cursor;
    const trainEnd = addDays(cursor, trainDays - 1);
    const testStart = addDays(trainEnd, 1);
    const testEnd = addDays(testStart, testDays - 1);
    if (testEnd > end) break;
    folds.push({
      index,
      trainStart: iso(trainStart),
      trainEnd: iso(trainEnd),
      testStart: iso(testStart),
      testEnd: iso(testEnd),
    });
    index += 1;
    cursor = addDays(cursor, stepDays);
  }
  return folds;
}

/** Number of folds for the configured window (0 when invalid). */
export function foldCount(
  overallStart: string,
  overallEnd: string,
  trainDays: number,
  testDays: number,
  stepDays: number,
  mode: WindowMode,
): number {
  return generateFolds(overallStart, overallEnd, trainDays, testDays, stepDays, mode).length;
}

/** Total candidate executions across all folds (informational preview). */
export function totalCandidateExecutions(folds: number, candidatesPerFold: number): number {
  if (!Number.isFinite(folds) || !Number.isFinite(candidatesPerFold)) return 0;
  return Math.max(0, folds) * Math.max(0, candidatesPerFold);
}
