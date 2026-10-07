'use client';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { AlertTriangle, FileText, GitCompare, History, Loader2, Play, ExternalLink, TrendingUp } from 'lucide-react';
import { Select, SelectTrigger, SelectValue, SelectContent, SelectItem } from '@/components/ui/select';
import { useBridge } from './bridge-status';
import { useRunSource } from './run-context';
import {
  BridgeError, getWalkForward, listDatasets, listRuns, listStrategies, listStudies, listWalkForwards,
  publishRun, runReportUrl, startWalkForward,
  type DatasetInfo, type ObjectiveMetric, type StrategyInfo, type WalkForwardFold, type WalkForwardStudy,
  type WalkForwardSummary,
} from './bridge-client';
import { candidateCount, parseGridValues, parseNumberList } from './optimization-utils';
import { generateFolds, totalCandidateExecutions, type WindowMode } from './walk-forward-utils';

const MAX_FOLDS = 10;
const MAX_CANDIDATES = 100;
const MAX_TOTAL_CANDIDATES = 500;
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

function paramsSummary(params: Record<string, unknown> | null): string {
  const entries = Object.entries(params ?? {});
  return entries.length ? entries.map(([k, v]) => `${k}=${String(v)}`).join(', ') : '—';
}

/**
 * Isolated out-of-sample cumulative PnL chart. Concatenates each fold's
 * price-term cumulative PnL with an explicit fold-boundary marker. This is
 * OUT-OF-SAMPLE price-term PnL — never an account balance.
 */
function OosPnlChart({ folds }: { folds: WalkForwardFold[] }) {
  const ok = folds.filter((f) => f.status === 'ok' && (f.equity?.length ?? 0) > 0);
  if (ok.length === 0) return null;
  const pts: number[] = [0];
  const boundaries: number[] = [];
  let offset = 0;
  ok.forEach((f, i) => {
    if (i > 0) boundaries.push(pts.length - 1);
    for (const p of f.equity) pts.push(offset + p.cumulative_pnl);
    offset += f.equity.length ? f.equity[f.equity.length - 1].cumulative_pnl : 0;
  });
  const min = Math.min(0, ...pts);
  const max = Math.max(0, ...pts);
  const span = max - min || 1;
  const W = 820, H = 190, pad = 8;
  const x = (i: number) => pad + (i / Math.max(1, pts.length - 1)) * (W - 2 * pad);
  const y = (v: number) => pad + (1 - (v - min) / span) * (H - 2 * pad);
  return (
    <div className="wf-chart">
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img"
           aria-label="Out-of-sample cumulative price-term PnL">
        <line x1={pad} y1={y(0)} x2={W - pad} y2={y(0)} stroke="#2a3340" strokeDasharray="3 3" />
        {boundaries.map((b) => (
          <line key={b} x1={x(b)} y1={pad} x2={x(b)} y2={H - pad} stroke="#e6b450" strokeWidth={0.8} strokeDasharray="2 3" />
        ))}
        <polyline fill="none" stroke="#5498f5" strokeWidth={1.4}
                  points={pts.map((v, i) => `${x(i)},${y(v)}`).join(' ')} />
      </svg>
      <div className="wf-chart-legend">
        <span className="meta">OUT-OF-SAMPLE · price-term cumulative PnL · {ok.length} folds (dashed lines = fold boundaries)</span>
      </div>
    </div>
  );
}

/** Walk-forward workflow inside the existing Research Workspace. */
export function WalkForwardPanel({ onCompare }: { onCompare: (runIds: string[]) => void }) {
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
  const [trainDays, setTrainDays] = useState('2');
  const [testDays, setTestDays] = useState('1');
  const [stepDays, setStepDays] = useState('1');
  const [mode, setMode] = useState<WindowMode>('rolling');

  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [study, setStudy] = useState<WalkForwardStudy | null>(null);
  const [wfStudies, setWfStudies] = useState<WalkForwardSummary[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [knownRuns, setKnownRuns] = useState<Set<string> | null>(null);
  const [knownOptStudies, setKnownOptStudies] = useState<Set<string> | null>(null);

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
      setWfStudies(await listWalkForwards());
    } catch {
      setWfStudies([]);
    }
  }, []);

  const loadRuns = useCallback(async () => {
    try {
      setKnownRuns(new Set((await listRuns()).map((r) => r.run_id)));
    } catch {
      setKnownRuns(new Set());
    }
  }, []);

  const loadOptStudies = useCallback(async () => {
    try {
      setKnownOptStudies(new Set((await listStudies()).map((s) => s.study_id)));
    } catch {
      setKnownOptStudies(new Set());
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

  const selectStrategy = (id: string) => {
    setStrategyId(id);
    const s = strategies.find((x) => x.id === id);
    setParamRaws(Object.fromEntries(Object.entries(s?.parameters ?? {}).map(([k, d]) => [k, String(d.default ?? '')])));
  };

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

  const folds = useMemo(
    () => generateFolds(start, end, Number(trainDays), Number(testDays), Number(stepDays), mode),
    [start, end, trainDays, testDays, stepDays, mode],
  );
  const totalCandidates = totalCandidateExecutions(folds.length, gridState.count);

  const overFoldLimit = folds.length > MAX_FOLDS;
  const overCandidateLimit = gridState.count > MAX_CANDIDATES;
  const overTotalLimit = totalCandidates > MAX_TOTAL_CANDIDATES;
  const windowValid = folds.length >= 1;
  const canRun = connected && !!strategy && !!dataset && windowValid && gridState.count >= 1
    && !overFoldLimit && !overCandidateLimit && !overTotalLimit && !running;

  const runWalkForward = async () => {
    if (!canRun || !strategy || !dataset) return;
    setRunning(true);
    setError(null);
    try {
      const result = await startWalkForward({
        strategy: strategy.id,
        market: { dataset_id: dataset.dataset_id, symbol, timeframe, start, end },
        execution: { stop_distance: Number(baseStop), take_profit_distance: Number(baseTake) },
        strategy_grid: gridState.strategyGrid,
        execution_grid: gridState.executionGrid,
        objective: { metric, direction },
        window: {
          train_days: Number(trainDays),
          test_days: Number(testDays),
          step_days: Number(stepDays),
          mode,
        },
        max_folds: MAX_FOLDS,
        max_candidates: MAX_CANDIDATES,
        max_total_candidates: MAX_TOTAL_CANDIDATES,
      });
      setKnownRuns(null);
      setKnownOptStudies(null);
      setStudy(result);
      setSelected([]);
      await Promise.all([loadHistory(), loadRuns(), loadOptStudies()]);
    } catch (err) {
      setError(errText(err));
    } finally {
      setRunning(false);
    }
  };

  const openStudy = async (wfId: string) => {
    setBusyId(wfId);
    setError(null);
    try {
      setKnownRuns(null);
      setKnownOptStudies(null);
      setStudy(await getWalkForward(wfId));
      setSelected([]);
      await Promise.all([loadRuns(), loadOptStudies()]);
    } catch (err) {
      setError(errText(err));
    } finally {
      setBusyId(null);
    }
  };

  const openOosRun = async (runId: string) => {
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

  const toggleOos = (runId: string) => {
    setSelected((prev) =>
      prev.includes(runId) ? prev.filter((x) => x !== runId) : prev.length >= 5 ? prev : [...prev, runId],
    );
  };

  const runMissing = (runId: string | null) =>
    !!runId && knownRuns !== null && !knownRuns.has(runId);
  const optStudyMissing = (studyId: string | null) =>
    !!studyId && knownOptStudies !== null && !knownOptStudies.has(studyId);

  const foldsList = study?.folds ?? [];
  const okFolds = foldsList.filter((f) => f.status === 'ok');
  const agg = study?.aggregate;

  return (
    <div className="lib-panel">
      {error && <p className="lib-status lib-error" role="alert"><AlertTriangle size={12} /> {error}</p>}

      <div className="opt-form">
        <label>
          Strategy
          <Select value={strategyId} onValueChange={selectStrategy}>
            <SelectTrigger aria-label="Walk-forward strategy"><SelectValue placeholder="Select strategy" /></SelectTrigger>
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
            <SelectTrigger aria-label="Walk-forward dataset"><SelectValue placeholder="Select dataset" /></SelectTrigger>
            <SelectContent>
              {datasets.map((d) => <SelectItem key={d.dataset_id} value={d.dataset_id}>{d.label}</SelectItem>)}
            </SelectContent>
          </Select>
        </label>
        <label>Symbol<input value={symbol} onChange={(e) => setSymbol(e.target.value)} aria-label="Walk-forward symbol" /></label>
        <label>Timeframe<input value={timeframe} onChange={(e) => setTimeframe(e.target.value)} aria-label="Walk-forward timeframe" /></label>
        <label>
          Overall Range <span className="meta">UTC</span>
          <input type="date" value={start} onChange={(e) => setStart(e.target.value)} aria-label="Walk-forward start date" />
          <span className="date-to">to</span>
          <input type="date" value={end} onChange={(e) => setEnd(e.target.value)} aria-label="Walk-forward end date" />
        </label>
        <label>Base Stop<input type="number" step="any" value={baseStop} onChange={(e) => setBaseStop(e.target.value)} aria-label="Walk-forward base stop" /></label>
        <label>Base Take Profit<input type="number" step="any" value={baseTake} onChange={(e) => setBaseTake(e.target.value)} aria-label="Walk-forward base take" /></label>
      </div>

      <div className="rr-group-label">Strategy Parameter Values <span className="meta">comma-separated</span></div>
      <div className="opt-grid">
        {Object.entries(strategy?.parameters ?? {}).map(([key, desc]) => (
          <label key={key}>
            {key} <span className="meta">· {desc.type}</span>
            <input
              value={paramRaws[key] ?? ''}
              onChange={(e) => setParamRaws((p) => ({ ...p, [key]: e.target.value }))}
              aria-label={`Walk-forward values ${key}`}
            />
          </label>
        ))}
      </div>

      <div className="rr-group-label">Execution Grid <span className="meta">comma-separated, optional</span></div>
      <div className="opt-grid">
        <label>stop_distance<input value={execStopRaw} onChange={(e) => setExecStopRaw(e.target.value)} aria-label="Walk-forward stop values" /></label>
        <label>take_profit_distance<input value={execTakeRaw} onChange={(e) => setExecTakeRaw(e.target.value)} aria-label="Walk-forward take values" /></label>
      </div>

      <div className="opt-form">
        <label>
          Objective
          <Select value={metric} onValueChange={(v) => setMetric(v as ObjectiveMetric)}>
            <SelectTrigger aria-label="Walk-forward objective metric"><SelectValue /></SelectTrigger>
            <SelectContent>{OBJECTIVE_METRICS.map((m) => <SelectItem key={m} value={m}>{m}</SelectItem>)}</SelectContent>
          </Select>
        </label>
        <label>
          Direction
          <Select value={direction} onValueChange={(v) => setDirection(v as 'maximize' | 'minimize')}>
            <SelectTrigger aria-label="Walk-forward objective direction"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="maximize">maximize</SelectItem>
              <SelectItem value="minimize">minimize</SelectItem>
            </SelectContent>
          </Select>
        </label>
      </div>

      <div className="rr-group-label">Walk-Forward Window <span className="meta">sizes in days</span></div>
      <div className="opt-grid">
        <label>training window<input type="number" min={1} value={trainDays} onChange={(e) => setTrainDays(e.target.value)} aria-label="Walk-forward train days" /></label>
        <label>test window<input type="number" min={1} value={testDays} onChange={(e) => setTestDays(e.target.value)} aria-label="Walk-forward test days" /></label>
        <label>step<input type="number" min={1} value={stepDays} onChange={(e) => setStepDays(e.target.value)} aria-label="Walk-forward step days" /></label>
        <label>
          mode
          <Select value={mode} onValueChange={(v) => setMode(v as WindowMode)}>
            <SelectTrigger aria-label="Walk-forward mode"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="rolling">rolling</SelectItem>
              <SelectItem value="anchored">anchored</SelectItem>
            </SelectContent>
          </Select>
        </label>
      </div>

      <div className="opt-run">
        <span className="opt-count wf-count" aria-label="Walk-forward fold count">
          Folds: <b>{folds.length}</b> · candidates/fold: <b>{gridState.count}</b> · total executions: <b>{totalCandidates}</b>
        </span>
        {gridState.error && <span className="lib-status lib-error"><AlertTriangle size={12} /> {gridState.error}</span>}
        {!windowValid && <span className="lib-status lib-error"><AlertTriangle size={12} /> window produces no folds within the range</span>}
        {overFoldLimit && <span className="lib-status lib-error"><AlertTriangle size={12} /> exceeds {MAX_FOLDS} folds — widen the step</span>}
        {overCandidateLimit && <span className="lib-status lib-error"><AlertTriangle size={12} /> exceeds {MAX_CANDIDATES} candidates/fold</span>}
        {overTotalLimit && <span className="lib-status lib-error"><AlertTriangle size={12} /> exceeds {MAX_TOTAL_CANDIDATES} total candidate executions</span>}
        <button className="primary" onClick={runWalkForward} disabled={!canRun} aria-label="Run walk-forward">
          {running ? <Loader2 size={14} className="spin" /> : <Play size={14} fill="currentColor" />}
          {running ? 'Running walk-forward…' : 'Run Walk-Forward'}
        </button>
      </div>

      {study && (
        <>
          <div className="lib-title opt-study-head">
            <span className="lib-id">{study.wf_id}</span>
            <span className={'tag ' + (study.status === 'completed' ? 'good' : study.status === 'partial' ? 'neutral' : 'bad')}>
              {study.status.toUpperCase()}
            </span>
            <span className="meta">
              objective {study.objective.metric} · {study.objective.direction} · window {study.window.mode}
            </span>
          </div>

          <div className="wf-summary">
            <span>Folds <b>{agg?.fold_count ?? 0}</b></span>
            <span>Completed <b>{agg?.completed_folds ?? 0}</b></span>
            <span>Profitable folds <b>{agg?.profitable_folds ?? '—'}</b>{agg?.profitable_fold_pct != null ? ` (${fmtNum(agg.profitable_fold_pct, 0)}%)` : ''}</span>
            <span>OOS trades <b>{agg?.total_oos_trades ?? '—'}</b></span>
            <span>OOS net PnL <b className={(agg?.total_oos_net_pnl ?? 0) >= 0 ? 'good-text' : 'bad-text'}>{fmtNum(agg?.total_oos_net_pnl)}</b></span>
            <span>OOS win rate <b>{agg?.oos_win_rate == null ? '—' : `${fmtNum(agg.oos_win_rate)}%`}</b></span>
            <span>OOS PF <b>{fmtNum(agg?.oos_profit_factor)}</b></span>
            <span>Best / worst fold <b>{fmtNum(agg?.best_fold_pnl)} / {fmtNum(agg?.worst_fold_pnl)}</b></span>
          </div>

          <div className="cmp-table-wrap">
            <table className="cmp-table wf-fold-table">
              <thead>
                <tr>
                  <th></th><th>Fold</th><th>Train</th><th>Test</th><th>Selected parameters</th>
                  <th>Train obj</th><th>Trades</th><th>Net PnL</th><th>Win %</th><th>PF</th><th>Test run</th><th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {foldsList.map((f) => {
                  const missing = f.status === 'ok' && runMissing(f.test_run_id);
                  const ok = f.status === 'ok';
                  return (
                    <tr key={f.index} className={missing ? 'opt-missing' : undefined}>
                      <td>
                        <input
                          type="checkbox"
                          checked={!!f.test_run_id && selected.includes(f.test_run_id)}
                          disabled={!ok || missing || (!selected.includes(f.test_run_id ?? '') && selected.length >= 5)}
                          onChange={() => f.test_run_id && toggleOos(f.test_run_id)}
                          aria-label={`Select OOS ${f.test_run_id}`}
                        />
                      </td>
                      <td>{f.index + 1}</td>
                      <td className="mono small">{f.train_start} → {f.train_end}</td>
                      <td className="mono small">{f.test_start} → {f.test_end}</td>
                      <td className="mono small">{paramsSummary(f.selected_parameters)}</td>
                      <td>{fmtNum(f.training_objective_value)}</td>
                      <td>{f.test_metrics?.total_trades ?? '—'}</td>
                      <td className={(f.test_metrics?.net_pnl ?? 0) >= 0 ? 'good-text' : 'bad-text'}>{fmtNum(f.test_metrics?.net_pnl)}</td>
                      <td>{f.test_metrics == null ? '—' : `${fmtNum(f.test_metrics.win_rate)}%`}</td>
                      <td>{fmtNum(f.test_metrics?.profit_factor)}</td>
                      <td className="mono small">
                        {f.test_run_id ?? '—'}
                        {missing && <span className="tag bad" title="OOS run archive is missing">MISSING</span>}
                        {optStudyMissing(f.optimization_study_id) && <span className="tag bad" title="Training optimization study is missing">TRAIN MISSING</span>}
                        {f.status !== 'ok' && <span className="tag bad">{f.failure?.code ?? f.status}</span>}
                      </td>
                      <td className="opt-actions">
                        <button onClick={() => f.test_run_id && openOosRun(f.test_run_id)} disabled={!ok || missing || busyId === f.test_run_id} aria-label={`Open OOS ${f.test_run_id}`}>
                          {busyId === f.test_run_id ? <Loader2 size={12} className="spin" /> : <ExternalLink size={12} />}
                        </button>
                        <button onClick={() => f.test_run_id && window.open(runReportUrl(f.test_run_id), '_blank', 'noopener,noreferrer')} disabled={!ok || missing} aria-label={`Open OOS report ${f.test_run_id}`}>
                          <FileText size={12} />
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {okFolds.length > 0 && <OosPnlChart folds={foldsList} />}

          <div className="rr-group-label"><TrendingUp size={12} /> Selected Parameters by Fold</div>
          <div className="cmp-table-wrap">
            <table className="cmp-table wf-params">
              <thead><tr><th>Fold</th><th>Test period</th><th>Selected parameters</th></tr></thead>
              <tbody>
                {foldsList.map((f) => (
                  <tr key={f.index}>
                    <td>{f.index + 1}</td>
                    <td className="mono small">{f.test_start} → {f.test_end}</td>
                    <td className="mono small">{paramsSummary(f.selected_parameters)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="opt-run">
            <button onClick={() => onCompare(selected)} disabled={selected.length < 2} aria-label="Compare selected OOS">
              <GitCompare size={13} /> Compare selected OOS ({selected.length})
            </button>
          </div>
        </>
      )}

      <div className="rr-group-label"><History size={12} /> Walk-Forward History</div>
      {wfStudies.length === 0 && <p className="lib-empty">No walk-forward studies yet.</p>}
      <div className="lib-list">
        {wfStudies.map((s) => (
          <div className="lib-row" key={s.wf_id}>
            <div className="lib-main">
              <div className="lib-title">
                <span className="lib-id">{s.wf_id}</span>
                <span className={'tag ' + (s.status === 'completed' ? 'good' : s.status === 'partial' ? 'neutral' : 'bad')}>
                  {s.status.toUpperCase()}
                </span>
                <span className="meta">{(s.created_at ?? '').slice(0, 19).replace('T', ' ')}</span>
              </div>
              <div className="lib-meta">
                {s.strategy} · {s.fold_count} folds · objective {s.objective.metric} ({s.objective.direction})
              </div>
              <div className="lib-stats">
                <span>OOS PnL <b>{fmtNum(s.oos_net_pnl)}</b></span>
                <span>Profitable folds <b>{s.profitable_folds ?? '—'}</b></span>
              </div>
            </div>
            <div className="lib-actions">
              <button onClick={() => openStudy(s.wf_id)} disabled={busyId === s.wf_id} aria-label={`Open walk-forward ${s.wf_id}`}>
                {busyId === s.wf_id ? <Loader2 size={13} className="spin" /> : <History size={13} />} Open
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
