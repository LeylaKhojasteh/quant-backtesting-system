/**
 * Identity of the market series a chart is showing.
 *
 * The chart viewport is re-initialised whenever this key changes. It must be an
 * explicit identity (the archived run id), never something inferred from the
 * candle data: two different runs can legitimately share the same last candle
 * timestamp, and inferring the key from data would then merge their viewports.
 */
export function viewportKey(
  runId: string | null | undefined,
  scope?: string | number | null,
): string {
  const base = runId ? `run:${runId}` : 'fixture:demo';
  return scope === undefined || scope === null || scope === '' ? base : `${base}:${String(scope)}`;
}
