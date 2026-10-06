'use client';
import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react';
import type { RunDetail } from './bridge-client';

export type WorkspaceValue = {
  /** A run configuration requested for reuse by the Real Run form. */
  pendingConfig: RunDetail | null;
  reuseConfig: (detail: RunDetail) => void;
  clearReuse: () => void;
  /** Bumped when the strategy catalog changes so the form reloads it. */
  catalogVersion: number;
  refreshCatalog: () => void;
};

const WorkspaceContext = createContext<WorkspaceValue>({
  pendingConfig: null,
  reuseConfig: () => {},
  clearReuse: () => {},
  catalogVersion: 0,
  refreshCatalog: () => {},
});

/**
 * Small cross-panel coordination layer for the Research Workspace. It carries
 * "reuse this archived configuration" requests to the existing Real Run form
 * and signals catalog changes after a strategy is deleted. No filesystem or
 * backtest logic lives here.
 */
export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const [pendingConfig, setPendingConfig] = useState<RunDetail | null>(null);
  const [catalogVersion, setCatalogVersion] = useState(0);

  const reuseConfig = useCallback((detail: RunDetail) => setPendingConfig(detail), []);
  const clearReuse = useCallback(() => setPendingConfig(null), []);
  const refreshCatalog = useCallback(() => setCatalogVersion((v) => v + 1), []);

  const value = useMemo(
    () => ({ pendingConfig, reuseConfig, clearReuse, catalogVersion, refreshCatalog }),
    [pendingConfig, reuseConfig, clearReuse, catalogVersion, refreshCatalog],
  );
  return <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>;
}

export function useWorkspace(): WorkspaceValue {
  return useContext(WorkspaceContext);
}
