/**
 * Python result adapter — the boundary between archived Python runs and the UI.
 *
 * Responsibilities (and nothing else):
 * - load `result.json` for a known archived run id
 * - validate required fields via `python-result`
 * - map Python contract fields to the frontend `BacktestRun` payload
 * - schema-validate the mapped payload before handing it to the UI
 *
 * The UI never touches Python files directly; it calls `loadArchivedRun`
 * and receives either a validated `BacktestRun` or a loud error string.
 * No charts, reports, analytics, or inspector components are touched here.
 */
import { BacktestRunSchema, type BacktestRun } from '../app/schema';
import { mapPythonResultToBacktestRun, validatePythonResult, type PythonResult } from '../app/python-result';

export type ArchivedRunRef = { runId: string; file: string; label: string; description?: string };

export type ArchivedRunResult = { ok: true; run: BacktestRun } | { ok: false; error: string };

const MANIFEST_URL = '/adapters-run-manifest.json';

/** Override the file paths the adapter fetches (used by tests). */
const fileOverrides = new Map<string, string>();

export function setAdapterFile(runId: string, file: string) {
  fileOverrides.set(runId, file);
}

export function clearAdapterFiles() {
  fileOverrides.clear();
}

/** Map a manifest `file` value to a served URL. Plain filenames resolve to `/`. */
export function resolveRunFile(file: string): string {
  if (/^(https?:)?\/\//.test(file)) return file;
  if (file.startsWith('/')) return file;
  if (file.startsWith('runs/')) return `/${file.replace(/^runs\//, 'runs-').replace(/\//g, '-')}`;
  return `/${file}`;
}

/** Known archived runs, from the co-located manifest. */
export async function listArchivedRuns(fetchImpl: typeof fetch = fetch): Promise<ArchivedRunRef[]> {
  const res = await fetchImpl(MANIFEST_URL);
  if (!res.ok) throw new Error(`run manifest unavailable (HTTP ${res.status})`);
  const manifest = (await res.json()) as { runs?: ArchivedRunRef[] };
  if (!Array.isArray(manifest.runs)) throw new Error('run manifest has no runs array');
  return manifest.runs;
}

function fileForRun(runs: ArchivedRunRef[], runId: string): string | null {
  if (fileOverrides.has(runId)) return fileOverrides.get(runId)!;
  const raw = runs.find((r) => r.runId === runId)?.file ?? null;
  return raw ? resolveRunFile(raw) : null;
}

/**
 * Load and map one archived run. Never throws: failures are returned as
 * `{ ok: false, error }` so the UI can render the reason honestly.
 */
export async function loadArchivedRun(runId: string, fetchImpl: typeof fetch = fetch): Promise<ArchivedRunResult> {
  try {
    const runs = await listArchivedRuns(fetchImpl);
    const file = fileForRun(runs, runId);
    if (!file) return { ok: false, error: `unknown archived run: ${runId}` };
    const res = await fetchImpl(file.startsWith('/') || file.startsWith('http') ? file : `/${file}`);
    if (!res.ok) return { ok: false, error: `archived run ${runId} unavailable (HTTP ${res.status})` };
    const payload = (await res.json()) as PythonResult;
    const violations = validatePythonResult(payload);
    if (violations.length) return { ok: false, error: `result.json failed adapter validation: ${violations.join('; ')}` };
    const candidate = mapPythonResultToBacktestRun(payload);
    const parsed = BacktestRunSchema.safeParse(candidate);
    if (!parsed.success) return { ok: false, error: `mapped run failed BacktestRun validation: ${parsed.error.message}` };
    return { ok: true, run: parsed.data };
  } catch (err) {
    return { ok: false, error: err instanceof Error ? err.message : 'unknown adapter failure' };
  }
}
