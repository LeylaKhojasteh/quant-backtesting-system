'use client';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { AlertTriangle, FileText, GitCompare, History, Loader2, Play, ExternalLink } from 'lucide-react';
import { Select, SelectTrigger, SelectValue, SelectContent, SelectItem } from '@/components/ui/select';
import { useBridge } from './bridge-status';
import { useRunSource } from './run-context';
import {
  BridgeError, getStudy, listDatasets, listRuns, listStrategies, listStudies, publishRun, runReportUrl,
  startOptimization,
  type DatasetInfo, type ObjectiveMetric, type Study, type StudyCandidate, type StudySummary, type StrategyInfo,
} from './bridge-client';
import { candidateCount, parseGridValues, parseNumberList } from './optimization-utils';

const MAX_CANDIDATES = 100;
const OBJECTIVE_METRICS: ObjectiveMetric[] = [
  'net_pnl', 'profit_factor', 'win_rate', 'max_drawdown', 'expectancy', 'total_trades',
];

function errText(err: unknown): string {
  if (err instanceof BridgeError) return `${err.code}: ${err.message}`;
  return err instanceof Error ? err.message : 'operation failed';
}

function fmtNum(value: number | null | undefined, digits = 2): string {
  return value === null || value === undefined
    ? '—'
    : value.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

function paramsSummary(params: Record<string, unknown>): string {
  const entries = Object.entries(params ?? {});
  return entries.length ? entries.map(([k, v]) => `${k}=${String(v)}`).join(', ') : '—';
}

/** Optimization workflow inside the existing Research Workspace. */
export function OptimizationPanel({ onCompare }: { onCompare: (runIds: string[]) => void }) {
  const { state: bridge } = useBridge();
  const connected = bridge.status === 'connected';
  const { refreshArchived, setSource } = useRunSource();

  const [strategies, setStrategies] = useState<StrategyInfo[]>([]);
  const [datasets, setDatasets] = useState<DatasetInfo[]>([]);
  const [strategyId, setStrategyId] = useState('');
  const [datasetId, setDatasetId] = useState('');
  const [symbol, setSymbol] = useState('XAUUSD');
  const [timeframe, setTimeframe] = useState('15min');
  const [start, setStart] = useState('');
  const [end, setEnd] = useState('');
  const [baseStop, setBaseStop] = useState('5');
  const [baseTake, setBaseTake] = useState('10');
  const [paramRaws, setParamRaws] = useState<Record<string, string>>({});
  const [execStopRaw, setExecStopRaw] = useState('5');
  const [execTakeRaw, setExecTakeRaw] = useState('10');
  const [metric, setMetric] = useState<ObjectiveMetric>('net_pnl');
  const [direction, setDirection] = useState<'maximize' | 'minimize'>('maximize');

  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [study, setStudy] = useState<Study | null>(null);
  const [studies, setStudies] = useState<StudySummary[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [busyId, setBusyId] = useState<string | null>(null);
  // null = run catalog not loaded yet (avoids a false "missing" flash).
  const [knownRuns, setKnownRuns] = useState<Set<string> | null>(null);

  const strategy = strategies.find((s) => s.id === strategyId);
  const dataset = datasets.find((d) => d.dataset_id === datasetId);

  const loadCatalogs = useCallback(async () => {
    const [list, data] = await Promise.all([listStrategies(), listDatasets()]);
    setStrategies(list);
    setDatasets(data);
    const chosenStrategy = list.find((s) => s.id === 'candle_direction') ?? list[0];
    setStrategyId(chosenStrategy?.id ?? '');
    setParamRaws(
      Object.fromEntries(Object.entries(chosenStrategy?.parameters ?? {}).map(([k, d]) => [k, String(d.default ?? '')])),
    );
    const chosenDataset = data.find((d) => d.complete_years?.length) ?? data[0];
    setDatasetId(chosenDataset?.dataset_id ?? '');
    if (chosenDataset) {
      setSymbol(chosenDataset.symbol);
      setStart(chosenDataset.default_start ?? '');
      setEnd(chosenDataset.default_end ?? '');
    }
  }, []);

  const loadHistory = useCallback(async () => {
    try {
      setStudies(await listStudies());
    } catch {
      setStudies([]);
    }
  }, []);

  const loadRuns = useCallback(async () => {
    try {
      setKnownRuns(new Set((await listRuns()).map((r) => r.run_id)));
    } catch {
      setKnownRuns(new Set());
    }
  }, []);

  useEffect(() => {
    if (!connected) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- external-system load; state lands after the await.
    loadCatalogs().catch(() => {});
  }, [connected, loadCatalogs]);

  useEffect(() => {
    if (!connected) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- external-system load; state lands after the await.
    loadHistory();
  }, [connected, loadHistory]);

  useEffect(() => {
    if (!connected) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- external-system load; state lands after the await.
    loadRuns();
  }, [connected, loadRuns]);

  const selectStrategy = (id: string) => {
    setStrategyId(id);
    const s = strategies.find((x) => x.id === id);
    setParamRaws(Object.fromEntries(Object.entries(s?.parameters ?? {}).map(([k, d]) => [k, String(d.default ?? '')])));
  };

  // Parse grids + candidate count (never truncates silently).
  const gridState = useMemo(() => {
    try {
      const strategyGrid: Record<string, unknown[]> = {};
      for (const [key, desc] of Object.entries(strategy?.parameters ?? {})) {
        strategyGrid[key] = parseGridValues(desc.type, paramRaws[key] ?? String(desc.default ?? ''));
      }
      const executionGrid: Record<string, number[]> = {
        stop_distance: parseNumberList(execStopRaw),
        take_profit_distance: parseNumberList(execTakeRaw),
      };
      return { strategyGrid, executionGrid, count: candidateCount(strategyGrid, executionGrid), error: null as string | null };
    } catch (err) {
      return { strategyGrid: {}, executionGrid: {}, count: 0, error: err instanceof Error ? err.message : 'invalid grid' };
    }
  }, [strategy, paramRaws, execStopRaw, execTakeRaw]);

  const overLimit = gridState.count > MAX_CANDIDATES;
  const canRun = connected && !!strategy && !!dataset && gridState.count >= 1 && !overLimit && !running;

  const runOptimization = async () => {
    if (!canRun || !strategy || !dataset) return;
    setRunning(true);
    setError(null);
    try {
      const result = await startOptimization({
        strategy: strategy.id,
        market: { dataset_id: dataset.dataset_id, symbol, timeframe, start, end },
        execution: { stop_distance: Number(baseStop), take_profit_distance: Number(baseTake) },
        strategy_grid: gridState.strategyGrid,
        execution_grid: gridState.executionGrid,
        objective: { metric, direction },
        max_candidates: MAX_CANDIDATES,
      });
      setKnownRuns(null);
      setStudy(result);
      setSelected([]);
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
      setStudy(await getStudy(studyId));
      setSelected([]);
      await loadRuns();
    } catch (err) {
      setError(errText(err));
    } finally {
      setBusyId(null);
    }
  };

  const openCandidate = async (runId: string) => {
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

  const toggleCandidate = (runId: string) => {
    setSelected((prev) =>
      prev.includes(runId) ? prev.filter((x) => x !== runId) : prev.length >= 5 ? prev : [...prev, runId],
    );
  };

  const candidates = study?.candidates ?? [];
  const rankOf = (runId: string) => {
    const i = study?.ranking.indexOf(runId) ?? -1;
    return i >= 0 ? i + 1 : null;
  };
  const isMissing = (c: StudyCandidate) =>
    c.status === 'ok' && knownRuns !== null && !knownRuns.has(c.run_id);

  return (
    <div className="lib-panel">
      {error && <p className="lib-status lib-error" role="alert"><AlertTriangle size={12} /> {error}</p>}

      <div className="opt-form">
        <label>
          Strategy
          <Select value={strategyId} onValueChange={selectStrategy}>
            <SelectTrigger aria-label="Optimization strategy"><SelectValue placeholder="Select strategy" /></SelectTrigger>
            <SelectContent>
              {strategies.map((s) => <SelectItem key={s.id} value={s.id}>{s.name} · v{s.version}</SelectItem>)}
            </SelectContent>
          </Select>
        </label>
        <label>
          Dataset
          <Select value={datasetId} onValueChange={(id) => {
            setDatasetId(id);
            const d = datasets.find((x) => x.dataset_id === id);
            if (d) { setSymbol(d.symbol); setStart(d.default_start ?? ''); setEnd(d.default_end ?? ''); }
          }}>
            <SelectTrigger aria-label="Optimization dataset"><SelectValue placeholder="Select dataset" /></SelectTrigger>
            <SelectContent>
              {datasets.map((d) => <SelectItem key={d.dataset_id} value={d.dataset_id}>{d.label}</SelectItem>)}
            </SelectContent>
          </Select>
        </label>
        <label>Symbol<input value={symbol} onChange={(e) => setSymbol(e.target.value)} aria-label="Optimization symbol" /></label>
        <label>Timeframe<input value={timeframe} onChange={(e) => setTimeframe(e.target.value)} aria-label="Optimization timeframe" /></label>
        <label>
          Date Range <span className="meta">UTC</span>
          <input type="date" value={start} onChange={(e) => setStart(e.target.value)} aria-label="Optimization start date" />
          <span className="date-to">to</span>
          <input type="date" value={end} onChange={(e) => setEnd(e.target.value)} aria-label="Optimization end date" />
        </label>
        <label>Base Stop<input type="number" step="any" value={baseStop} onChange={(e) => setBaseStop(e.target.value)} aria-label="Optimization base stop" /></label>
        <label>Base Take Profit<input type="number" step="any" value={baseTake} onChange={(e) => setBaseTake(e.target.value)} aria-label="Optimization base take" /></label>
      </div>

      <div className="rr-group-label">Strategy Parameter Values <span className="meta">comma-separated</span></div>
      <div className="opt-grid">
        {Object.entries(strategy?.parameters ?? {}).map(([key, desc]) => (
          <label key={key}>
            {key} <span className="meta">· {desc.type}</span>
            <input
              value={paramRaws[key] ?? ''}
              onChange={(e) => setParamRaws((p) => ({ ...p, [key]: e.target.value }))}
              aria-label={`Optimization values ${key}`}
            />
          </label>
        ))}
      </div>

      <div className="rr-group-label">Execution Grid <span className="meta">comma-separated, optional</span></div>
      <div className="opt-grid">
        <label>stop_distance<input value={execStopRaw} onChange={(e) => setExecStopRaw(e.target.value)} aria-label="Optimization stop values" /></label>
        <label>take_profit_distance<input value={execTakeRaw} onChange={(e) => setExecTakeRaw(e.target.value)} aria-label="Optimization take values" /></label>
      </div>

      <div className="opt-form">
        <label>
          Objective
          <Select value={metric} onValueChange={(v) => setMetric(v as ObjectiveMetric)}>
            <SelectTrigger aria-label="Optimization objective metric"><SelectValue /></SelectTrigger>
            <SelectContent>{OBJECTIVE_METRICS.map((m) => <SelectItem key={m} value={m}>{m}</SelectItem>)}</SelectContent>
          </Select>
        </label>
        <label>
          Direction
          <Select value={direction} onValueChange={(v) => setDirection(v as 'maximize' | 'minimize')}>
            <SelectTrigger aria-label="Optimization objective direction"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="maximize">maximize</SelectItem>
              <SelectItem value="minimize">minimize</SelectItem>
            </SelectContent>
          </Select>
        </label>
      </div>

      <div className="opt-run">
        <span className="opt-count" aria-label="Candidate combinations">
          Candidate combinations: <b>{gridState.count}</b> / {MAX_CANDIDATES}
        </span>
        {gridState.error && <span className="lib-status lib-error"><AlertTriangle size={12} /> {gridState.error}</span>}
        {overLimit && <span className="lib-status lib-error"><AlertTriangle size={12} /> exceeds limit — narrow the grids</span>}
        <button className="primary" onClick={runOptimization} disabled={!canRun} aria-label="Run optimization">
          {running ? <Loader2 size={14} className="spin" /> : <Play size={14} fill="currentColor" />}
          {running ? 'Running study…' : 'Run Optimization'}
        </button>
      </div>

      {study && (
        <>
          <div className="lib-title opt-study-head">
            <span className="lib-id">{study.study_id}</span>
            <span className={'tag ' + (study.status === 'completed' ? 'good' : 'bad')}>{study.status.toUpperCase()}</span>
            <span className="meta">objective {study.objective.metric} · {study.objective.direction} · best {study.best_run_id ?? '—'}</span>
          </div>
          <div className="cmp-table-wrap">
            <table className="cmp-table">
              <thead>
                <tr>
                  <th></th><th>Rank</th><th>Run</th><th>Parameters</th><th>Stop/Take</th>
                  <th>Trades</th><th>Win %</th><th>Net PnL</th><th>PF</th><th>Max DD</th><th>Objective</th><th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {candidates.map((c) => {
                  const missing = isMissing(c);
                  const netPnl = c.metrics?.net_pnl;
                  const hasNetPnl = typeof netPnl === 'number' && Number.isFinite(netPnl);
                  return (
                    <tr key={c.run_id} className={missing ? 'opt-missing' : undefined}>
                      <td>
                        <input
                          type="checkbox"
                          checked={selected.includes(c.run_id)}
                          disabled={c.status !== 'ok' || missing || (!selected.includes(c.run_id) && selected.length >= 5)}
                          onChange={() => toggleCandidate(c.run_id)}
                          aria-label={`Select candidate ${c.run_id}`}
                        />
                      </td>
                      <td>{rankOf(c.run_id) ?? '—'}</td>
                      <td className="mono small">
                        {c.run_id}
                        {missing && <span className="tag bad" title="Candidate run archive is missing">MISSING</span>}
                      </td>
                      <td className="mono small">{paramsSummary(c.parameters)}</td>
                      <td>{fmtNum(c.execution.stop_distance)} / {fmtNum(c.execution.take_profit_distance)}</td>
                      <td>{c.metrics?.total_trades ?? '—'}</td>
                      <td>{fmtNum(c.metrics?.win_rate)}</td>
                      <td className={hasNetPnl ? (netPnl >= 0 ? 'good-text' : 'bad-text') : ''}>{fmtNum(netPnl)}</td>
                      <td>{fmtNum(c.metrics?.profit_factor)}</td>
                      <td>{fmtNum(c.metrics?.max_drawdown)}</td>
                      <td>{fmtNum(c.objective_value)}{c.status === 'failed' ? ` (${c.failure?.code ?? 'failed'})` : ''}</td>
                      <td className="opt-actions">
                        <button onClick={() => openCandidate(c.run_id)} disabled={c.status !== 'ok' || missing || busyId === c.run_id} aria-label={`Open candidate ${c.run_id}`}>
                          {busyId === c.run_id ? <Loader2 size={12} className="spin" /> : <ExternalLink size={12} />}
                        </button>
                        <button onClick={() => window.open(runReportUrl(c.run_id), '_blank', 'noopener,noreferrer')} disabled={c.status !== 'ok' || missing} aria-label={`Open candidate report ${c.run_id}`}>
                          <FileText size={12} />
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <div className="opt-run">
            <button onClick={() => onCompare(selected)} disabled={selected.length < 2} aria-label="Compare selected candidates">
              <GitCompare size={13} /> Compare selected ({selected.length})
            </button>
          </div>
        </>
      )}

      <div className="rr-group-label"><History size={12} /> Study History</div>
      {studies.length === 0 && <p className="lib-empty">No studies yet.</p>}
      <div className="lib-list">
        {studies.map((s) => (
          <div className="lib-row" key={s.study_id}>
            <div className="lib-main">
              <div className="lib-title">
                <span className="lib-id">{s.study_id}</span>
                <span className={'tag ' + (s.status === 'completed' ? 'good' : 'bad')}>{s.status.toUpperCase()}</span>
                <span className="meta">{(s.created_at ?? '').slice(0, 19).replace('T', ' ')}</span>
              </div>
              <div className="lib-meta">{s.strategy} · {s.candidate_count} candidates · objective {s.objective.metric} ({s.objective.direction})</div>
              <div className="lib-stats">
                <span>Best <b className="mono">{s.best_run_id ?? '—'}</b></span>
                <span>Objective <b>{fmtNum(s.best_objective_value)}</b></span>
              </div>
            </div>
            <div className="lib-actions">
              <button onClick={() => openStudy(s.study_id)} disabled={busyId === s.study_id} aria-label={`Open study ${s.study_id}`}>
                {busyId === s.study_id ? <Loader2 size={13} className="spin" /> : <History size={13} />} Open
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
