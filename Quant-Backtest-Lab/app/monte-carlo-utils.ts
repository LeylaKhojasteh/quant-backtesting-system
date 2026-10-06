/**
 * Pure helpers for the Monte Carlo configuration UI. React-free so they can be
 * unit-tested directly. Mirrors the backend limits in ``src.monte_carlo``.
 */

export type MonteCarloMethod = 'shuffle' | 'bootstrap';
export type MonteCarloBasis = 'price' | 'account';

export const MC_DEFAULT_SIMULATIONS = 1000;
export const MC_HARD_MAX_SIMULATIONS = 5000;
export const MC_SMALL_SAMPLE = 30;

export type Validation<T> = { ok: true; value: T } | { ok: false; error: string };

export function validateSimulations(raw: string): Validation<number> {
  const text = (raw ?? '').trim();
  if (!/^-?\d+$/.test(text)) return { ok: false, error: 'Simulations must be a whole number' };
  const value = Number(text);
  if (value < 1) return { ok: false, error: 'Simulations must be at least 1' };
  if (value > MC_HARD_MAX_SIMULATIONS) {
    return { ok: false, error: `Simulations cannot exceed ${MC_HARD_MAX_SIMULATIONS}` };
  }
  return { ok: true, value };
}

export function validateSeed(raw: string): Validation<number> {
  const text = (raw ?? '').trim();
  if (!/^-?\d+$/.test(text)) return { ok: false, error: 'Seed must be a whole number' };
  return { ok: true, value: Number(text) };
}

/** Honest small-sample warning; never claims a small sample is statistically strong. */
export function smallSampleWarning(trades: number | null | undefined): string | null {
  if (trades === null || trades === undefined) return null;
  if (trades < 2) return `Only ${trades} closed trade(s) — Monte Carlo requires at least 2.`;
  if (trades < MC_SMALL_SAMPLE) {
    return `Small sample: ${trades} closed trades. Resampling a small sample is not statistically strong.`;
  }
  return null;
}

export function basisLabel(basis: MonteCarloBasis): string {
  return basis === 'account' ? 'Account (net account PnL)' : 'Price terms (price PnL)';
}

export function methodLabel(method: MonteCarloMethod): string {
  return method === 'shuffle' ? 'Shuffle (without replacement)' : 'Bootstrap (with replacement)';
}
