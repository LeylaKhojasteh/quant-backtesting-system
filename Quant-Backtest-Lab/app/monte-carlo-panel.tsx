'use client';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { AlertTriangle, History, Loader2, Play, ExternalLink, BarChart3 } from 'lucide-react';
import { Select, SelectTrigger, SelectValue, SelectContent, SelectItem } from '@/components/ui/select';
import { useBridge } from './bridge-status';
import { useRunSource } from './run-context';
import {
  BridgeError, getMonteCarlo, listMonteCarlo, listRuns, publishRun, startMonteCarlo,
  type MonteCarloMethod, type MonteCarloStat, type MonteCarloStudy, type MonteCarloSummaryItem,
  type RunLibrarySummary,
} from './bridge-client';
import {
  MC_DEFAULT_SIMULATIONS, MC_HARD_MAX_SIMULATIONS, basisLabel, smallSampleWarning, validateSeed, validateSimulations,
} from './monte-carlo-utils';

function errText(err: unknown): string {
  if (err instanceof BridgeError) return `${err.code}: ${err.message}`;
  return err instanceof Error ? err.message : 'operation failed';
}

function fmtNum(value: number | null | undefined, digits = 2): string {
  return value === null || value === undefined
    ? '—'
    : value.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

function statLine(stat: MonteCarloStat | null): string {
  if (!stat) return 'Not available';
  return `min ${fmtNum(stat.min)} · p05 ${fmtNum(stat.p05)} · median ${fmtNum(stat.median)} · p95 ${fmtNum(stat.p95)} · max ${fmtNum(stat.max)}`;
}

/** Percentile envelope (p05/median/p95) with the original observed path. */
function EnvelopeChart({ envelope, original, pathLabel, originalLabel }: {
  envelope: MonteCarloStudy['envelope'];
  original: number[];
  pathLabel: string;
  originalLabel: string;
}) {
  if (envelope.length < 2) return null;
  const all = envelope.flatMap((p) => [p.p05, p.p95]).concat(original);
  const min = Math.min(...all);
  const max = Math.max(...all);
  const span = max - min || 1;
  const W = 820, H = 200, pad = 8;
  const x = (i: number) => pad + (i / Math.max(1, envelope.length - 1)) * (W - 2 * pad);
  const y = (v: number) => pad + (1 - (v - min) / span) * (H - 2 * pad);
  const band = envelope.map((p, i) => `${x(i)},${y(p.p95)}`).join(' ')
    + ' ' + [...envelope].reverse().map((p, i) => `${x(envelope.length - 1 - i)},${y(p.p05)}`).join(' ');
  return (
    <div className="mc-envelope">
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img" aria-label={pathLabel}>
        <polygon points={band} fill="#5498f5" opacity=".12" />
        <polyline fill="none" stroke="#5498f5" strokeWidth={1.1}
          points={envelope.map((p, i) => `${x(i)},${y(p.p05)}`).join(' ')} />
        <polyline fill="none" stroke="#5498f5" strokeWidth={1.1}
          points={envelope.map((p, i) => `${x(i)},${y(p.p95)}`).join(' ')} />
        <polyline fill="none" stroke="#67d88f" strokeWidth={1.5}
          points={envelope.map((p, i) => `${x(i)},${y(p.median)}`).join(' ')} />
        {original.length > 1 && (
          <polyline fill="none" stroke="#e6b450" strokeWidth={1.3} strokeDasharray="4 3"
            points={original.map((v, i) => `${x(i)},${y(v)}`).join(' ')} />
        )}
      </svg>
      <div className="mc-legend">
        <span><i style={{ background: '#5498f5' }} />p05 / p95</span>
        <span><i style={{ background: '#67d88f' }} />median</span>
        <span><i style={{ background: '#e6b450' }} />{originalLabel}</span>
        <span className="meta">{pathLabel} · by trade index</span>
      </div>
    </div>
  );
}

/** Compact distribution histogram from a study stat. */
function Histogram({ stat, label, accent = '#5498f5' }: { stat: MonteCarloStat | null; label: string; accent?: string }) {
  if (!stat || stat.histogram.bins.length === 0) return null;
  const bins = stat.histogram.bins;
  const counts = bins.map((b) => b.count);
  const maxCount = Math.max(1, ...counts);
  const W = 360, H = 120, pad = 4;
  const bw = (W - 2 * pad) / bins.length;
  return (
    <div className="mc-hist">
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img" aria-label={label}>
        {bins.map((b, i) => {
          const h = (b.count / maxCount) * (H - 2 * pad);
          return (
            <rect key={i} x={pad + i * bw + 0.5} y={H - pad - h} width={Math.max(1, bw - 1)} height={h}
              fill={accent} opacity=".8" />
          );
        })}
      </svg>
      <div className="mc-hist-axis">
        <span>{fmtNum(stat.histogram.from, 1)}</span>
        <span className="meta">{label}</span>
        <span>{fmtNum(stat.histogram.to, 1)}</span>
      </div>
    </div>
  );
}

/** Monte Carlo workflow inside the existing Research Workspace. */
export function MonteCarloPanel() {
  const { state: bridge } = useBridge();
  const connected = bridge.status === 'connected';
  const { refreshArchived, setSource } = useRunSource();

  const [runs, setRuns] = useState<RunLibrarySummary[]>([]);
  const [sourceRunId, setSourceRunId] = useState('');
  const [method, setMethod] = useState<MonteCarloMethod>('shuffle');
  const [simulations, setSimulations] = useState(String(MC_DEFAULT_SIMULATIONS));
  const [seed, setSeed] = useState('0');

  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [study, setStudy] = useState<MonteCarloStudy | null>(null);
  const [studies, setStudies] = useState<MonteCarloSummaryItem[]>([]);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [knownRuns, setKnownRuns] = useState<Set<string> | null>(null);

  const loadRuns = useCallback(async () => {
    try {
      const list = await listRuns();
      setRuns(list);
      setKnownRuns(new Set(list.map((r) => r.run_id)));
      setSourceRunId((prev) => (prev && list.some((r) => r.run_id === prev) ? prev : list[0]?.run_id ?? ''));
    } catch {
      setRuns([]);
      setKnownRuns(new Set());
    }
  }, []);

  const loadHistory = useCallback(async () => {
    try {
      setStudies(await listMonteCarlo());
    } catch {
      setStudies([]);
    }
  }, []);

  useEffect(() => {
    if (!connected) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- external-system load; state lands after the await.
    loadRuns();
  }, [connected, loadRuns]);

  useEffect(() => {
    if (!connected) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- external-system load; state lands after the await.
    loadHistory();
  }, [connected, loadHistory]);

  const sourceRun = runs.find((r) => r.run_id === sourceRunId);
  const simState = useMemo(() => validateSimulations(simulations), [simulations]);
  const seedState = useMemo(() => validateSeed(seed), [seed]);
  const tradeCount = sourceRun?.total_trades ?? null;
  const basis = sourceRun?.account_ready ? 'account' : 'price';
  const warning = smallSampleWarning(tradeCount);
  const canRun = connected && !!sourceRun && simState.ok && seedState.ok
    && (tradeCount ?? 0) >= 2 && !running;

  const runStudy = async () => {
    if (!canRun || !sourceRun || !simState.ok || !seedState.ok) return;
    setRunning(true);
    setError(null);
    try {
      const result = await startMonteCarlo({
        run_id: sourceRun.run_id,
        method,
        simulations: simState.value,
        seed: seedState.value,
      });
      setKnownRuns(null);
      setStudy(result);
      await Promise.all([loadHistory(), loadRuns()]);
    } catch (err) {
      setError(errText(err));
    } finally {
      setRunning(false);
    }
  };

  const openStudy = async (studyId: string) => {
    setBusyId(studyId);
    setError(null);
    try {
      setKnownRuns(null);
      setStudy(await getMonteCarlo(studyId));
      await loadRuns();
    } catch (err) {
      setError(errText(err));
    } finally {
      setBusyId(null);
    }
  };

  const openSourceRun = async (runId: string) => {
    setBusyId(runId);
    setError(null);
    try {
      await publishRun(runId);
      await refreshArchived();
      setSource({ mode: 'archived', runId });
    } catch (err) {
      setError(errText(err));
    } finally {
      setBusyId(null);
    }
  };

  const sourceMissing = study !== null && knownRuns !== null && !knownRuns.has(study.source.run_id);
  const summary = study?.summary;
  const accountMode = study?.basis === 'account';

  return (
    <div className="lib-panel">
      {error && <p className="lib-status lib-error" role="alert"><AlertTriangle size={12} /> {error}</p>}

      <div className="opt-form">
        <label>
          Source Run
          <Select value={sourceRunId} onValueChange={setSourceRunId}>
            <SelectTrigger aria-label="Monte Carlo source run"><SelectValue placeholder="Select an archived run" /></SelectTrigger>
            <SelectContent>
              {runs.map((r) => (
                <SelectItem key={r.run_id} value={r.run_id}>
                  {r.run_id} · {r.strategy.name} · {r.total_trades ?? '—'} trades{r.account_ready ? ' · account' : ''}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </label>
        <label>
          Method
          <Select value={method} onValueChange={(v) => setMethod(v as MonteCarloMethod)}>
            <SelectTrigger aria-label="Monte Carlo method"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="shuffle">Shuffle</SelectItem>
              <SelectItem value="bootstrap">Bootstrap</SelectItem>
            </SelectContent>
          </Select>
        </label>
        <label>
          Simulations <span className="meta">· max {MC_HARD_MAX_SIMULATIONS}</span>
          <input type="number" min={1} max={MC_HARD_MAX_SIMULATIONS} value={simulations}
            onChange={(e) => setSimulations(e.target.value)} aria-label="Monte Carlo simulations" />
        </label>
        <label>
          Seed
          <input type="number" step={1} value={seed} onChange={(e) => setSeed(e.target.value)} aria-label="Monte Carlo seed" />
        </label>
      </div>

      <div className="opt-run">
        <span className="mc-basis" aria-label="Monte Carlo basis">
          Source trades: <b>{tradeCount ?? '—'}</b> · basis: <b>{basisLabel(basis)}</b>
          {method === 'shuffle' ? ' · shuffle (terminal PnL constant)' : ' · bootstrap (with replacement)'}
        </span>
        {!simState.ok && <span className="lib-status lib-error"><AlertTriangle size={12} /> {simState.error}</span>}
        {!seedState.ok && <span className="lib-status lib-error"><AlertTriangle size={12} /> {seedState.error}</span>}
        {warning && <span className="mc-warning lib-status"><AlertTriangle size={12} /> {warning}</span>}
        <button className="primary" onClick={runStudy} disabled={!canRun} aria-label="Run Monte Carlo">
          {running ? <Loader2 size={14} className="spin" /> : <Play size={14} fill="currentColor" />}
          {running ? 'Running Monte Carlo…' : 'Run Monte Carlo'}
        </button>
      </div>

      {study && summary && (
        <>
          <div className="lib-title mc-study-head">
            <span className="lib-id">{study.study_id}</span>
            <span className="tag good">{study.status.toUpperCase()}</span>
            <span className="meta">{study.method} · {study.simulations} sims · seed {study.seed} · {basisLabel(study.basis)}</span>
          </div>

          <div className="wf-summary mc-source">
            <span>Source <b>{study.source.run_id}</b>
              {sourceMissing && <span className="tag bad mc-missing" title="Source run archive is missing">SOURCE RUN MISSING</span>}
            </span>
            <span>Strategy <b>{study.source.strategy?.name ?? '—'}</b></span>
            <span>Trades <b>{study.trade_count}</b></span>
            <span>Basis <b>{basisLabel(study.basis)}</b></span>
            {study.small_sample && <span className="mc-warning">small sample</span>}
          </div>

          <div className="rr-group-label"><BarChart3 size={12} /> {study.labels.path} — percentile envelope</div>
          <EnvelopeChart envelope={study.envelope} original={study.original_path}
            pathLabel={study.labels.path} originalLabel={study.labels.original} />

          <div className="rr-group-label">Robustness distributions</div>
          <div className="mc-grid">
            <div className="mc-card">
              <span className="mc-card-title">Max drawdown {accountMode ? '(account currency)' : '(price terms)'}</span>
              <Histogram stat={summary.max_drawdown} label="max drawdown" />
              <p className="meta">{statLine(summary.max_drawdown)}</p>
            </div>
            <div className="mc-card">
              <span className="mc-card-title">Max losing streak (trades)</span>
              <Histogram stat={summary.max_losing_streak} label="losing streak" accent="#ee757b" />
              <p className="meta">{statLine(summary.max_losing_streak)}</p>
            </div>
          </div>

          {study.method === 'bootstrap' && (
            <div className="mc-grid">
              <div className="mc-card">
                <span className="mc-card-title">Terminal PnL {accountMode ? '(account currency)' : '(price terms)'}</span>
                <Histogram stat={summary.terminal_pnl} label="terminal PnL" accent="#67d88f" />
                <p className="meta">{statLine(summary.terminal_pnl)}</p>
                {summary.probability_terminal_negative !== null && (
                  <p className="mc-prob">
                    P(terminal PnL &lt; 0) = <b>{fmtNum(summary.probability_terminal_negative * 100)}%</b>
                    <span className="meta"> · {summary.probability_terminal_negative_label}</span>
                  </p>
                )}
              </div>
              {accountMode && summary.ending_balance && (
                <div className="mc-card">
                  <span className="mc-card-title">Ending balance — {study.account?.currency ?? 'account currency'}</span>
                  <Histogram stat={summary.ending_balance} label="ending balance" accent="#b98cf0" />
                  <p className="meta">{statLine(summary.ending_balance)}</p>
                </div>
              )}
            </div>
          )}

          {study.method === 'shuffle' && (
            <p className="mc-note" role="note">
              Shuffle: terminal PnL is identical in every path by construction ({fmtNum(summary.terminal_pnl?.median)}),
              so terminal variation is not shown. The useful distributions are drawdown, losing streak and path shape.
            </p>
          )}

          {accountMode && summary.max_drawdown_pct && (
            <div className="mc-card">
              <span className="mc-card-title">Realized balance drawdown %</span>
              <Histogram stat={summary.max_drawdown_pct} label="drawdown %" accent="#ee757b" />
              <p className="meta">{statLine(summary.max_drawdown_pct)}</p>
            </div>
          )}

          <div className="opt-run">
            <button onClick={() => openSourceRun(study.source.run_id)} disabled={sourceMissing || busyId === study.source.run_id} aria-label={`Open source run ${study.source.run_id}`}>
              {busyId === study.source.run_id ? <Loader2 size={13} className="spin" /> : <ExternalLink size={13} />} Open Source Run
            </button>
          </div>
        </>
      )}

      <div className="rr-group-label"><History size={12} /> Monte Carlo History</div>
      {studies.length === 0 && <p className="lib-empty">No Monte Carlo studies yet.</p>}
      <div className="lib-list">
        {studies.map((s) => (
          <div className="lib-row" key={s.study_id}>
            <div className="lib-main">
              <div className="lib-title">
                <span className="lib-id">{s.study_id}</span>
                <span className="tag neutral">{s.method}</span>
                <span className="meta">{(s.created_at ?? '').slice(0, 19).replace('T', ' ')}</span>
              </div>
              <div className="lib-meta">
                source {s.source_run_id} · {s.strategy} · {basisLabel(s.basis)} · {s.simulations} sims · seed {s.seed}
              </div>
            </div>
            <div className="lib-actions">
              <button onClick={() => openStudy(s.study_id)} disabled={busyId === s.study_id} aria-label={`Open Monte Carlo ${s.study_id}`}>
                {busyId === s.study_id ? <Loader2 size={13} className="spin" /> : <History size={13} />} Open
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
