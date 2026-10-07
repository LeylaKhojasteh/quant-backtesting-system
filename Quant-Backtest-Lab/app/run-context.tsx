'use client';
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { BacktestRunSchema, type BacktestRun } from './schema';
import { loadBacktestRun } from './runner';
import { listArchivedRuns, loadArchivedRun, type ArchivedRunRef } from '../adapters/python-result-adapter';

export type RunSource = { mode: 'none' } | { mode: 'demo' } | { mode: 'archived'; runId: string };

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
  /** True only when the app was opened with the explicit `?fixture=demo` test hook. */
  fixture: boolean;
}>({
  source: { mode: 'none' },
  setSource: () => {},
  archived: [],
  refreshArchived: async () => [],
  fixture: false,
});

/**
 * Loads the single BacktestRun for the whole application.
 *
 * Product default is a REAL run: on open it selects the first archived run, or
 * shows a neutral empty state prompting a real backtest. The seeded fixture is
 * NOT a product mode; it is only mounted when the app is opened with the
 * explicit test hook `?fixture=demo` (used by deterministic tests).
 *
 * `archived` runs are mapped through the adapter boundary; the fixture is the
 * existing seeded reference kept for tests.
 */
export function RunProvider({ children }: { children: ReactNode }) {
  const [source, setSource] = useState<RunSource>({ mode: 'none' });
  const [state, setState] = useState<RunState>({ status: 'loading' });
  const [archived, setArchived] = useState<ArchivedRunRef[]>([]);
  const [fixture, setFixture] = useState(false);

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

  // Boot: choose the first real run (archived) or the explicit test fixture.
  const booted = useRef(false);
  useEffect(() => {
    if (booted.current) return;
    booted.current = true;
    let alive = true;
    (async () => {
      const params = typeof window !== 'undefined' ? new URLSearchParams(window.location.search) : null;
      const useFixture = params?.get('fixture') === 'demo';
      const runs = await refreshArchived();
      if (!alive) return;
      if (useFixture) {
        setFixture(true);
        setSource({ mode: 'demo' });
        return;
      }
      if (runs.length) {
        setSource({ mode: 'archived', runId: runs[0].runId });
        return;
      }
      setState({ status: 'empty' });
    })();
    return () => {
      alive = false;
    };
  }, [refreshArchived]);

  useEffect(() => {
    if (source.mode === 'none') return;
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
    () => ({ source, setSource, archived, refreshArchived, fixture }),
    [source, archived, refreshArchived, fixture],
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
