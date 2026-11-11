'use client';
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { Check, Info, RefreshCw } from 'lucide-react';
import { getHealth, type BridgeHealth } from './bridge-client';

export type BridgeStatusKind = 'checking' | 'connected' | 'connecting' | 'disconnected';
export type BridgeDownReason = 'bridge' | 'network' | null;

export type BridgeConnection =
  | { status: 'checking'; health: null; error: null; reason: null }
  | { status: 'connected'; health: BridgeHealth; error: null; reason: null }
  | { status: 'connecting'; health: null; error: null; reason: null }
  | { status: 'disconnected'; health: null; error: string; reason: BridgeDownReason };

export type BridgeValue = { state: BridgeConnection; refresh: () => Promise<void> };

const BridgeContext = createContext<BridgeValue>({
  state: { status: 'checking', health: null, error: null, reason: null },
  refresh: async () => {},
});

function isOffline(): boolean {
  return typeof navigator !== 'undefined' && navigator.onLine === false;
}

function downReason(): BridgeDownReason {
  return isOffline() ? 'network' : 'bridge';
}

/**
 * Tracks whether the local Python bridge is reachable. Real-run controls and
 * the archived-run bridge APIs depend on it.
 */
export function BridgeProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<BridgeConnection>({ status: 'checking', health: null, error: null, reason: null });

  const check = useCallback(async () => {
    if (isOffline()) {
      setState({ status: 'disconnected', health: null, error: 'browser offline', reason: 'network' });
      return;
    }
    try {
      const health = await getHealth();
      setState({ status: 'connected', health, error: null, reason: null });
    } catch (err) {
      setState({
        status: 'disconnected',
        health: null,
        error: err instanceof Error ? err.message : 'Python bridge unavailable',
        reason: downReason(),
      });
    }
  }, []);

  const refresh = useCallback(async () => {
    setState({ status: 'connecting', health: null, error: null, reason: null });
    await check();
  }, [check]);

  useEffect(() => {
    // First statement is async, so no synchronous setState during the effect.
    // eslint-disable-next-line react-hooks/set-state-in-effect -- bridge health is an external-system sync; state lands after the await.
    check();
  }, [check]);

  // If the bridge starts after the page, retry until it becomes reachable.
  useEffect(() => {
    if (state.status !== 'disconnected') return;
    const timer = setInterval(() => { void check(); }, 10000);
    return () => clearInterval(timer);
  }, [state.status, check]);

  // Browser network changes are distinct from the bridge going down.
  useEffect(() => {
    const onDown = () => {
      setState({ status: 'disconnected', health: null, error: 'browser offline', reason: 'network' });
    };
    const onUp = () => {
      setState({ status: 'connecting', health: null, error: null, reason: null });
      void check();
    };
    window.addEventListener('offline', onDown);
    window.addEventListener('online', onUp);
    return () => {
      window.removeEventListener('offline', onDown);
      window.removeEventListener('online', onUp);
    };
  }, [check]);

  const value = useMemo(() => ({ state, refresh }), [state, refresh]);
  return <BridgeContext.Provider value={value}>{children}</BridgeContext.Provider>;
}

export function useBridge(): BridgeValue {
  return useContext(BridgeContext);
}

/**
 * Compact topbar indicator. Bridge and Network are separate pills: a
 * connected bridge never implies the browser network is up, and a network
 * outage names itself instead of blaming the bridge.
 */
export function EngineStatus() {
  const { state, refresh } = useBridge();
  const connected = state.status === 'connected';
  const busy = state.status === 'checking' || state.status === 'connecting';
  const netDown = state.status === 'disconnected' && state.reason === 'network';
  const bridgeLabel =
    connected
      ? `Connected${state.health.version ? ` · v${state.health.version}` : ''}`
      : state.status === 'connecting'
        ? 'Connecting…'
        : state.status === 'checking'
          ? 'Checking…'
          : 'Disconnected';
  const netLabel = netDown ? 'Disconnected — check your network' : 'Connected';
  const bridgeTone = connected ? 'engine-live' : busy ? 'engine-warn' : 'engine-bad';
  return (
    <div className="engine-group" role="status" aria-label={`Bridge: ${bridgeLabel}. Network: ${netLabel}.`}>
      <span className={'engine ' + bridgeTone}>
        {connected ? <Check size={14} /> : busy ? <RefreshCw size={14} className="spin" /> : <Info size={14} />}
        <span>Bridge: {bridgeLabel}</span>
      </span>
      <span className={'engine engine-net ' + (netDown ? 'engine-bad' : 'engine-live')}>
        <span>Net: {netLabel}</span>
      </span>
      {state.status === 'disconnected' && (
        <button className="engine-retry" onClick={refresh} aria-label="Retry bridge connection">
          <RefreshCw size={12} /> Retry
        </button>
      )}
    </div>
  );
}
