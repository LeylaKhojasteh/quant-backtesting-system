'use client';
import { useEffect, useMemo, useState } from 'react';
import { Loader2, Square, ChevronDown, ChevronUp } from 'lucide-react';
import { useRunJob } from './run-job-context';
import type { RunJob } from './bridge-client';

function formatElapsed(job: RunJob, now: number): string {
  const startMs = job.started_at ? Date.parse(job.started_at)
    : job.created_at ? Date.parse(job.created_at) : NaN;
  if (!Number.isFinite(startMs)) return '--:--';
  const endMs = job.finished_at ? Date.parse(job.finished_at) : now;
  const secs = Math.max(0, Math.floor((endMs - startMs) / 1000));
  const mm = String(Math.floor(secs / 60)).padStart(2, '0');
  const ss = String(secs % 60).padStart(2, '0');
  return `${mm}:${ss}`;
}

function heartbeatText(job: RunJob): { label: string; stale: boolean } {
  if (job.state === 'completed') return { label: 'Completed', stale: false };
  if (job.state === 'failed') return { label: 'Failed', stale: false };
  if (job.state === 'stopped') return { label: 'Stopped', stale: false };
  if (job.state === 'stopping') return { label: 'Stopping…', stale: false };
  if (job.heartbeat === 'alive') return { label: 'Running', stale: false };
  if (job.heartbeat === 'starting') return { label: 'Starting…', stale: false };
  return { label: 'Worker heartbeat delayed', stale: true };
}

/**
 * One compact live-activity bar for the active real backtest.
 *
 * Shows current stage + elapsed time + worker heartbeat and concise events.
 * The bar renders no fabricated completion metrics.
 * The heartbeat comes from the WORKER wrapper (job.heartbeat_at), never from
 * the bridge itself.
 */
export function RunActivityBar({ onStop }: { onStop: () => void }) {
  const { job } = useRunJob();
  const [now, setNow] = useState(() => Date.now());
  const [details, setDetails] = useState(false);

  useEffect(() => {
    if (!job) return;
    if (job.state === 'completed' || job.state === 'failed' || job.state === 'stopped') return;
    const iv = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(iv);
  }, [job]);

  const events = useMemo(() => (job ? job.events.slice(-12) : []), [job]);
  if (!job) return null;

  const hb = heartbeatText(job);
  const elapsed = formatElapsed(job, now);
  const stopping = job.state === 'stopping';

  return (
    <section className="run-activity" aria-live="polite" aria-label="Backtest activity">
      <span className={`activity-dot${hb.stale ? ' stale' : ''}`} aria-hidden />
      <span className="activity-main">
        {job.state === 'stopped' ? (
          <><b>■ Backtest stopped</b><span className="meta"> · {elapsed}</span></>
        ) : job.state === 'failed' ? (
          <><b>■ Backtest failed</b><span className="meta"> · {elapsed}</span></>
        ) : job.state === 'completed' ? (
          <><b>■ Backtest completed</b><span className="meta"> · {elapsed}</span></>
        ) : (
          <><b>● Backtest running</b><span className="meta"> · {job.stage_label} · {elapsed}</span></>
        )}
      </span>
      <span className={`activity-heartbeat${hb.stale ? ' stale' : ''}`} title={job.heartbeat_at ? `Worker heartbeat ${job.heartbeat_at}` : 'Waiting for the first worker heartbeat'}>
        {hb.label}
      </span>
      <button
        type="button"
        className="activity-details"
        onClick={() => setDetails((d) => !d)}
        aria-expanded={details}
        aria-label={details ? 'Hide run details' : 'Show run details'}
      >
        {details ? <ChevronUp size={13} /> : <ChevronDown size={13} />} Details
      </button>
      {(job.state === 'running' || job.state === 'queued' || stopping) && (
        <button
          type="button"
          className="activity-stop"
          onClick={onStop}
          disabled={stopping}
          aria-label="Stop backtest"
        >
          {stopping ? <Loader2 size={13} className="spin" /> : <Square size={13} />}
          {stopping ? 'Stopping…' : 'Stop Backtest'}
        </button>
      )}
      {details && (
        <ol className="activity-events">
          {events.length === 0 && <li className="meta">No events yet.</li>}
          {events.map((e, i) => (
            <li key={`${e.t}-${i}`}>
              <span className="meta">{e.t.slice(11, 19) || e.t}</span> {e.event}
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
