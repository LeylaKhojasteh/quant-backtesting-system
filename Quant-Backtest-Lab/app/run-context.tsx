'use client';
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import type { BacktestRun } from './schema';
import { getActiveRunJob } from './bridge-client';
import { listArchivedRuns, loadArchivedRun, type ArchivedRunRef } from '../adapters/python-result-adapter';

export type RunSource = { mode: 'none' } | { mode: 'archived'; runId: string };

export type RunState =
  | { status: 'loading' }
  | { status: 'empty' }
  | { status: 'ready'; run: BacktestRun; source: RunSource }
  | { status: 'error'; error: string }
  | { status: 'malformed'; message: string };

const RunContext = createContext<RunState>({ status: 'loading' });
const SourceContext = createContext<{
  source: RunSource;
  setSource: (s: RunSource) => void;
  archived: ArchivedRunRef[];
  refreshArchived: () => Promise<ArchivedRunRef[]>;
}>({
  source: { mode: 'none' },
  setSource: () => {},
  archived: [],
  refreshArchived: async () => [],
});

/**
 * Loads the single BacktestRun for the whole application.
 *
 * On open it selects the newest archived real run, or shows a neutral empty
 * state prompting a real backtest. Archived runs are mapped through the
 * adapter boundary and validated there.
 */
export function RunProvider({ children }: { children: ReactNode }) {
  const [source, setSource] = useState<RunSource>({ mode: 'none' });
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
    } catch (err) {
      // Keep the last known list and let the caller surface the failure.
      throw err instanceof Error ? err : new Error('Failed to refresh archived runs.');
    }
  }, []);

  // Boot priority (authoritative, newest-first):
  //   1. active run job (reconnect handled by the job provider; the visible
  //      result stays untouched until the new run successfully completes)
  //   2. newest successfully completed valid run = manifest head
  //      (listArchivedRuns is newest-first; deleted runs are already gone
  //      from the list; stopped/failed/incomplete jobs never enter it)
  //   3. neutral empty state.
  // Persisted localStorage is NEVER authoritative for run selection: a stale
  // stored run id must not override the newest successful run.
  const booted = useRef(false);
  useEffect(() => {
    if (booted.current) return;
    booted.current = true;
    let alive = true;
    (async () => {
      let runs: ArchivedRunRef[];
      try {
        runs = await listArchivedRuns();
        if (!alive) return;
        setArchived(runs);
      } catch (err) {
        if (!alive) return;
        setState({
          status: 'error',
          error: err instanceof Error ? err.message : 'Failed to load archived runs from the local server.',
        });
        return;
      }
      // When a job is active, keep whatever result is currently loaded: the
      // activity bar represents the NEW run and replacement happens only on
      // successful completion. Do not touch the visible result here.
      try {
        const active = await getActiveRunJob();
        if (!alive) return;
        if (active) return;
      } catch {
        // Bridge unreachable at boot: fall through to archived runs.
      }
      // Newest successful real run first = manifest head. If the newest was
      // deleted, the next newest valid run is already head. Stopped/failed/
      // incomplete jobs never appear in the manifest, so they can never win.
      // localStorage is intentionally NOT consulted here.
      const pick = runs.length ? runs[0].runId : null;
      if (pick) {
        setSource({ mode: 'archived', runId: pick });
        return;
      }
      setState({ status: 'empty' });
    })();
    return () => {
      alive = false;
    };
  }, [refreshArchived]);

  // Minor UI-state hint only (e.g. last viewed run for non-boot surfaces).
  // It is NEVER read for boot selection: the manifest head is authoritative.
  useEffect(() => {
    if (source.mode === 'archived') {
      try {
        window.localStorage.setItem('qbl:lastViewedRunId', source.runId);
      } catch {
        // Storage unavailable (private mode): nothing depends on this.
      }
    }
  }, [source]);

  useEffect(() => {
    if (source.mode === 'none') return;
    let alive = true;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- source switch must reset to loading before the async load resolves.
    setState({ status: 'loading' });
    (async () => {
      const result = await loadArchivedRun(source.runId);
      if (!alive) return;
      if (!result.ok) {
        setState({ status: 'error', error: result.error });
        return;
      }
      setState({ status: 'ready', run: result.run, source });
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
