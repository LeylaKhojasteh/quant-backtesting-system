'use client';
import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { BacktestRunSchema, type BacktestRun } from './schema';
import { loadBacktestRun } from './runner';

export type RunState =
  | { status: 'loading' }
  | { status: 'ready'; run: BacktestRun }
  | { status: 'error'; error: string }
  | { status: 'malformed'; message: string };

const RunContext = createContext<RunState>({ status: 'loading' });

/**
 * Loads the single BacktestRun for the whole application. The UI reads only
 * this value; scattered constants and raw `trades`/`cumulative` are not used
 * for reporting.
 *
 * - Loading: shows a compact pending state.
 * - Error / malformed: shows exactly why, and no invented numbers.
 * - Ready: hand-off point for the Python engine adapter.
 */
export function RunProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<RunState>({ status: 'loading' });

  useEffect(() => {
    let alive = true;
    (async () => {
      const result = await loadBacktestRun();
      if (!alive) return;
      if (!result.ok) {
        setState({ status: 'error', error: result.error });
        return;
      }
      // Validate again at the boundary so a hand-off payload (future engine)
      // that slipped past the adapter cannot reach the UI as real results.
      const parsed = BacktestRunSchema.safeParse(result.run);
      if (!parsed.success) {
        setState({ status: 'malformed', message: parsed.error.message });
        return;
      }
      setState({ status: 'ready', run: parsed.data });
    })();
    return () => {
      alive = false;
    };
  }, []);

  const value = useMemo(() => state, [state]);
  return <RunContext.Provider value={value}>{children}</RunContext.Provider>;
}

export function useRun(): RunState {
  return useContext(RunContext);
}

export function useReadyRun(): BacktestRun | null {
  const s = useContext(RunContext);
  return s.status === 'ready' ? s.run : null;
}
