'use client';
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { BacktestRunSchema, type BacktestRun } from './schema';
import { loadBacktestRun } from './runner';
import { listArchivedRuns, loadArchivedRun, type ArchivedRunRef } from '../adapters/python-result-adapter';

export type RunSource = { mode: 'demo' } | { mode: 'archived'; runId: string };

export type RunState =
  | { status: 'loading' }
  | { status: 'ready'; run: BacktestRun; source: RunSource }
  | { status: 'error'; error: string }
  | { status: 'malformed'; message: string };

const RunContext = createContext<RunState>({ status: 'loading' });
const SourceContext = createContext<{
  source: RunSource;
  setSource: (s: RunSource) => void;
  archived: ArchivedRunRef[];
  refreshArchived: () => Promise<ArchivedRunRef[]>;
}>({ source: { mode: 'demo' }, setSource: () => {}, archived: [], refreshArchived: async () => [] });

/**
 * Loads the single BacktestRun for the whole application. The UI reads only
 * this value; scattered constants and raw `trades`/`cumulative` are not used
 * for reporting.
 *
 * Two sources, one contract:
 * - demo: the existing seeded reference (default, unchanged behavior).
 * - archived: a Python `result.json` mapped through the adapter boundary.
 *
 * - Loading: shows a compact pending state.
 * - Error / malformed: shows exactly why, and no invented numbers.
 * - Ready: carries the validated run plus which source supplied it.
 */
export function RunProvider({ children }: { children: ReactNode }) {
  const [source, setSource] = useState<RunSource>({ mode: 'demo' });
  const [state, setState] = useState<RunState>({ status: 'loading' });
  const [archived, setArchived] = useState<ArchivedRunRef[]>([]);

  // Re-read the served manifest (no-store) so a just-published run appears
  // immediately, without a manual page refresh.
  const refreshArchived = useCallback(async () => {
    const noStore: typeof fetch = (input, init) =>
      fetch(input, { ...(init ?? {}), cache: 'no-store' });
    try {
      const runs = await listArchivedRuns(noStore);
      setArchived(runs);
      return runs;
    } catch {
      setArchived([]);
      return [];
    }
  }, []);

  useEffect(() => {
    let alive = true;
    (async () => {
      const runs = await refreshArchived();
      if (alive) setArchived(runs);
    })();
    return () => {
      alive = false;
    };
  }, [refreshArchived]);

  useEffect(() => {
    let alive = true;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- source switch must reset to loading before the async load resolves.
    setState({ status: 'loading' });
    (async () => {
      if (source.mode === 'archived') {
        const result = await loadArchivedRun(source.runId);
        if (!alive) return;
        if (!result.ok) {
          setState({ status: 'error', error: result.error });
          return;
        }
        setState({ status: 'ready', run: result.run, source });
        return;
      }
      const result = await loadBacktestRun();
      if (!alive) return;
      if (!result.ok) {
        setState({ status: 'error', error: result.error });
        return;
      }
      // Validate again at the boundary so a hand-off payload
      // that slipped past the adapter cannot reach the UI as real results.
      const parsed = BacktestRunSchema.safeParse(result.run);
      if (!parsed.success) {
        setState({ status: 'malformed', message: parsed.error.message });
        return;
      }
      setState({ status: 'ready', run: parsed.data, source });
    })();
    return () => {
      alive = false;
    };
  }, [source]);

  const value = useMemo(() => state, [state]);
  const sourceValue = useMemo(
    () => ({ source, setSource, archived, refreshArchived }),
    [source, archived, refreshArchived],
  );
  return (
    <RunContext.Provider value={value}>
      <SourceContext.Provider value={sourceValue}>{children}</SourceContext.Provider>
    </RunContext.Provider>
  );
}

export function useRun(): RunState {
  return useContext(RunContext);
}

export function useRunSource() {
  return useContext(SourceContext);
}

export function useReadyRun(): BacktestRun | null {
  const s = useContext(RunContext);
  return s.status === 'ready' ? s.run : null;
}
