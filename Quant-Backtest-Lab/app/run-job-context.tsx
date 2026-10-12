'use client';
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { useBridge } from './bridge-status';
import {
  BridgeError,
  getActiveRunJob,
  getRunJob,
  startRunJob,
  stopRunJob,
  type BacktestRunRequestPayload,
  type RunJob,
} from './bridge-client';

export type JobOutcome =
  | { kind: 'completed'; job: RunJob }
  | { kind: 'failed'; job: RunJob; message: string }
  | { kind: 'stopped'; job: RunJob };

export type RunJobValue = {
  /** The job the activity bar represents (null when idle). */
  job: RunJob | null;
  /** True while a real job is queued/running/stopping. */
  active: boolean;
  /** Start one job; throws BridgeError (409 when one is already active). */
  start: (payload: BacktestRunRequestPayload) => Promise<RunJob>;
  /** Request a stop (with caller-owned confirmation dialog). */
  stop: () => Promise<RunJob | null>;
  /** Last terminal outcome, kept so the UI can announce it once. */
  outcome: JobOutcome | null;
  clearOutcome: () => void;
  /** Reconnect explicitly (used after browser reload races). */
  reconnect: () => Promise<RunJob | null>;
  bridgeAvailable: boolean;
};

const RunJobContext = createContext<RunJobValue>({
  job: null,
  active: false,
  start: async () => { throw new Error('RunJobProvider not mounted'); },
  stop: async () => null,
  outcome: null,
  clearOutcome: () => {},
  reconnect: async () => null,
  bridgeAvailable: false,
});

const ACTIVE_STATES = new Set(['queued', 'running', 'stopping']);
const TERMINAL_STATES = new Set(['completed', 'failed', 'stopped']);
const POLL_MS = 1000;

function outcomeOf(job: RunJob): JobOutcome | null {
  if (job.state === 'completed') return { kind: 'completed', job };
  if (job.state === 'stopped') return { kind: 'stopped', job };
  if (job.state === 'failed') {
    const err = job.error;
    const message =
      err && typeof err === 'object' ? (err.message || err.code) : typeof err === 'string' ? err : 'run failed';
    return { kind: 'failed', job, message: message || 'run failed' };
  }
  return null;
}

/**
 * Owns the single active real-backtest job for the session.
 *
 * - On mount it queries `/run-jobs/active` so a browser reload reconnects
 *   to the SAME job (never creates another one).
 * - Polls at ~1 Hz while active; stops on completed/failed/stopped.
 * - The currently loaded successful result is never cleared here: the
 *   activity bar represents the NEW run, and replacement happens only when
 *   a completion is consumed by the page shell.
 */
export function RunJobProvider({ children }: { children: ReactNode }) {
  const { state: bridge } = useBridge();
  const connected = bridge.status === 'connected';
  const [job, setJob] = useState<RunJob | null>(null);
  const [outcome, setOutcome] = useState<JobOutcome | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const jobIdRef = useRef<string | null>(null);
  const seenTerminalRef = useRef<string | null>(null);
  // Last job this provider rendered, even after the tracked id is released.
  // Gives Stop a target when the id was released on terminal ingest.
  const lastJobRef = useRef<RunJob | null>(null);

  const stopPolling = useCallback(() => {
    if (pollRef.current !== null) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  // Poller reads via a ref so the interval callback never captures `ingest`
  // before it is declared (react-hooks/immutability).
  const ingestRef = useRef<(next: RunJob | null) => void>(() => {});

  const ingest = useCallback((next: RunJob | null) => {
    ingestRef.current(next);
  }, []);

  // Stable implementation assigned once; safe for interval + effects.
  useEffect(() => {
    ingestRef.current = (next: RunJob | null) => {
      if (!next) {
        jobIdRef.current = null;
        setJob(null);
        stopPolling();
        return;
      }
      jobIdRef.current = next.job_id;
      lastJobRef.current = next;
      setJob(next);
      if (TERMINAL_STATES.has(next.state)) {
        stopPolling();
        if (seenTerminalRef.current !== next.job_id) {
          seenTerminalRef.current = next.job_id;
          const out = outcomeOf(next);
          if (out) setOutcome(out);
        }
        // Release the id so the external-job watcher can adopt a LATER job
        // started elsewhere; the completed bar stays rendered from state.
        jobIdRef.current = null;
        return;
      }
      // Active: ensure the 1 Hz poller is running.
      if (pollRef.current === null) {
        pollRef.current = setInterval(async () => {
          const id = jobIdRef.current;
          if (!id) return;
          try {
            const fresh = await getRunJob(id);
            ingestRef.current(fresh);
          } catch {
            // Transient poll failure: keep the last known state; the next
            // tick retries. The worker runs independently of polling.
          }
        }, POLL_MS);
      }
    };
  }, [stopPolling]);

  const reconnect = useCallback(async () => {
    if (!connected) return null;
    try {
      const active = await getActiveRunJob();
      ingest(active);
      return active;
    } catch {
      return null;
    }
  }, [connected, ingest]);

  // Reconnect on mount and whenever the bridge becomes available.
  useEffect(() => {
    if (!connected) return;
    let alive = true;
    (async () => {
      try {
        const active = await getActiveRunJob();
        if (!alive) return;
        // Never disturb a job this provider already tracks (started locally).
        if (!jobIdRef.current) ingest(active);
        else if (active && active.job_id === jobIdRef.current) ingest(active);
      } catch {
        // Bridge hiccup at boot: stay idle; the user can retry.
      }
    })();
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- boot reconnect only.
  }, [connected]);

  // Adopt an externally started active job (another tab, or a job started
  // while this tab showed a terminal outcome). Runs only when this provider
  // is NOT tracking an active job, so it can never steal or duplicate one.
  useEffect(() => {
    if (!connected || jobIdRef.current) return;
    let alive = true;
    const iv = setInterval(async () => {
      if (!alive || jobIdRef.current) return;
      try {
        const active = await getActiveRunJob();
        if (!alive || jobIdRef.current) return;
        if (active) ingest(active);
      } catch {
        // Transient: retry on the next tick.
      }
    }, 2000);
    return () => {
      alive = false;
      clearInterval(iv);
    };
  }, [connected, ingest]);

  useEffect(() => stopPolling, [stopPolling]);

  const start = useCallback(async (payload: BacktestRunRequestPayload) => {
    if (jobIdRef.current) {
      const current = await getRunJob(jobIdRef.current).catch(() => null);
      if (current && ACTIVE_STATES.has(current.state)) {
        throw new BridgeError('a real backtest is already running; stop it before starting another', 'run_in_progress', 409);
      }
    }
    setOutcome(null);
    seenTerminalRef.current = null;
    const created = await startRunJob(payload);
    ingest(created);
    return created;
  }, [ingest]);

  const stop = useCallback(async () => {
    const id = jobIdRef.current ?? lastJobRef.current?.job_id ?? null;
    if (!id) return null;
    const current = await getRunJob(id).catch(() => null);
    if (current && TERMINAL_STATES.has(current.state)) {
      ingest(current);
      return current;
    }
    const stopped = await stopRunJob(id);
    ingest(stopped);
    return stopped;
  }, [ingest]);

  const clearOutcome = useCallback(() => setOutcome(null), []);

  const value = useMemo<RunJobValue>(
    () => ({
      job,
      active: !!job && ACTIVE_STATES.has(job.state),
      start,
      stop,
      outcome,
      clearOutcome,
      reconnect,
      bridgeAvailable: connected,
    }),
    [job, start, stop, outcome, clearOutcome, reconnect, connected],
  );
  return <RunJobContext.Provider value={value}>{children}</RunJobContext.Provider>;
}

export function useRunJob(): RunJobValue {
  return useContext(RunJobContext);
}
