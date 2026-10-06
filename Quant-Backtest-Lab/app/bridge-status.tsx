'use client';
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { Check, Info, RefreshCw } from 'lucide-react';
import { getHealth, type BridgeHealth } from './bridge-client';

export type BridgeConnection =
  | { status: 'checking'; health: null; error: null }
  | { status: 'connected'; health: BridgeHealth; error: null }
  | { status: 'unavailable'; health: null; error: string };

export type BridgeValue = { state: BridgeConnection; refresh: () => Promise<void> };

const BridgeContext = createContext<BridgeValue>({
  state: { status: 'checking', health: null, error: null },
  refresh: async () => {},
});

/**
 * Tracks whether the local Python bridge is reachable. Real-run controls use
 * this gate; the seeded Demo workflow never depends on it.
 */
export function BridgeProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<BridgeConnection>({ status: 'checking', health: null, error: null });

  const check = useCallback(async () => {
    try {
      const health = await getHealth();
      setState({ status: 'connected', health, error: null });
    } catch (err) {
      setState({
        status: 'unavailable',
        health: null,
        error: err instanceof Error ? err.message : 'Python bridge unavailable',
      });
    }
  }, []);

  const refresh = useCallback(async () => {
    setState({ status: 'checking', health: null, error: null });
    await check();
  }, [check]);

  useEffect(() => {
    // First statement is async, so no synchronous setState during the effect.
    // eslint-disable-next-line react-hooks/set-state-in-effect -- bridge health is an external-system sync; state lands after the await.
    check();
  }, [check]);

  const value = useMemo(() => ({ state, refresh }), [state, refresh]);
  return <BridgeContext.Provider value={value}>{children}</BridgeContext.Provider>;
}

export function useBridge(): BridgeValue {
  return useContext(BridgeContext);
}

/** Compact topbar indicator: Python engine Connected / Unavailable. */
export function EngineStatus({ context }: { context: string }) {
  const { state, refresh } = useBridge();
  const connected = state.status === 'connected';
  const label =
    state.status === 'checking'
      ? 'Checking…'
      : connected
        ? `Connected${state.health.version ? ` · v${state.health.version}` : ''}`
        : 'Unavailable';
  return (
    <div
      className={'engine ' + (connected ? 'engine-live' : 'engine-muted')}
      role="status"
      aria-label={`Python engine: ${label}`}
    >
      {connected ? <Check size={14} /> : <Info size={14} />}
      <span>Python engine: {label}</span>
      <span className="engine-context meta">· {context}</span>
      {!connected && state.status === 'unavailable' && (
        <button className="engine-retry" onClick={refresh} aria-label="Retry bridge connection">
          <RefreshCw size={12} /> Retry
        </button>
      )}
    </div>
  );
}
