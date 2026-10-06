/**
 * Pure helpers for the optimization configuration UI. Kept free of React so
 * candidate counting / grid parsing are directly unit-testable.
 */

export type ParamType = 'int' | 'float' | 'bool' | 'string' | 'unknown';

/** Parse a comma-separated value list using a declared strategy parameter type. */
export function parseGridValues(type: ParamType, raw: string): (string | number | boolean)[] {
  const parts = raw.split(',').map((s) => s.trim()).filter((s) => s.length > 0);
  const out: (string | number | boolean)[] = [];
  for (const part of parts) {
    if (type === 'int') {
      const n = Number(part);
      if (!Number.isInteger(n)) throw new Error(`"${part}" is not an integer`);
      out.push(n);
    } else if (type === 'float') {
      const n = Number(part);
      if (!Number.isFinite(n)) throw new Error(`"${part}" is not a number`);
      out.push(n);
    } else if (type === 'bool') {
      const low = part.toLowerCase();
      if (low === 'true') out.push(true);
      else if (low === 'false') out.push(false);
      else throw new Error(`"${part}" is not true/false`);
    } else {
      out.push(part);
    }
  }
  if (out.length === 0) throw new Error('enter at least one value');
  return out;
}

/** Parse a comma-separated numeric list (execution grids). */
export function parseNumberList(raw: string): number[] {
  const parts = raw.split(',').map((s) => s.trim()).filter((s) => s.length > 0);
  if (parts.length === 0) throw new Error('enter at least one value');
  return parts.map((part) => {
    const n = Number(part);
    if (!Number.isFinite(n) || n <= 0) throw new Error(`"${part}" must be a positive number`);
    return n;
  });
}

/** Deterministic candidate count for the configured grids (0 if any list empty). */
export function candidateCount(
  strategyGrid: Record<string, readonly unknown[]>,
  executionGrid: Record<string, readonly unknown[]>,
): number {
  const dims = [...Object.values(strategyGrid), ...Object.values(executionGrid)];
  if (dims.length === 0) return 1;
  return dims.reduce((acc, values) => acc * (Array.isArray(values) ? values.length : 0), 1);
}
