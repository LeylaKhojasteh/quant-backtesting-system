'use client';
import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  AlertTriangle, ExternalLink, FileText, GitCompare, Library, Loader2, RefreshCw, RotateCcw, Search, Trash2,
} from 'lucide-react';
import { Dialog, DialogContent, DialogTitle, DialogDescription } from '@/components/ui/dialog';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { useBridge } from './bridge-status';
import { useRunSource } from './run-context';
import { useWorkspace } from './workspace-context';
import { RunComparison } from './run-comparison';
import { OptimizationPanel } from './optimization-panel';
import { WalkForwardPanel } from './walk-forward-panel';
import {
  BridgeError,
  deleteRun,
  deleteStrategy,
  getRun,
  listRuns,
  listStrategies,
  publishRun,
  runReportUrl,
  type RunLibrarySummary,
  type StrategyInfo,
} from './bridge-client';

function errText(err: unknown): string {
  if (err instanceof BridgeError) return `${err.code}: ${err.message}`;
  return err instanceof Error ? err.message : 'operation failed';
}

function basename(value: string): string {
  return value.split(/[\\/]/).pop() ?? value;
}

function fmtNum(value: number | null | undefined, digits = 2): string {
  return value === null || value === undefined ? '—' : value.toLocaleString('en-US', {
    minimumFractionDigits: digits, maximumFractionDigits: digits,
  });
}

/**
 * Research Workspace: a compact Run Library + Strategy Management surface.
 * It reuses the existing client boundary and dashboard — it never recomputes
 * metrics or converts a BacktestResult itself.
 */
export function RunLibrary() {
  const { state: bridge } = useBridge();
  const connected = bridge.status === 'connected';
  const { source, setSource, refreshArchived } = useRunSource();
  const { reuseConfig, refreshCatalog, catalogVersion } = useWorkspace();

  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState('runs');
  const [runs, setRuns] = useState<RunLibrarySummary[]>([]);
  const [strategies, setStrategies] = useState<StrategyInfo[]>([]);
  const [runsError, setRunsError] = useState<string | null>(null);
  const [strategiesError, setStrategiesError] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const [busy, setBusy] = useState<string | null>(null);
  const [confirmId, setConfirmId] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [selectedRuns, setSelectedRuns] = useState<string[]>([]);

  const loadRuns = useCallback(async () => {
    try {
      const list = await listRuns();
      setRuns(list);
      setRunsError(null);
    } catch (err) {
      setRunsError(errText(err));
    }
  }, []);

  const loadStrategies = useCallback(async () => {
    try {
      const list = await listStrategies();
      setStrategies(list);
      setStrategiesError(null);
    } catch (err) {
      setStrategiesError(errText(err));
    }
  }, []);

  useEffect(() => {
    if (!open) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- external-system load; state lands after the await.
    loadRuns();
  }, [open, loadRuns]);

  useEffect(() => {
    if (!open || tab !== 'strategies') return;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- external-system load; state lands after the await.
    loadStrategies();
  }, [open, tab, catalogVersion, loadStrategies]);

  const filteredRuns = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return runs;
    return runs.filter((r) =>
      [r.run_id, r.strategy.name, r.symbol ?? ''].some((v) => v.toLowerCase().includes(q)),
    );
  }, [runs, search]);

  const toggleRunSelection = (runId: string) => {
    setSelectedRuns((prev) =>
      prev.includes(runId) ? prev.filter((x) => x !== runId) : prev.length >= 5 ? prev : [...prev, runId],
    );
  };

  const runAction = async (runId: string, fn: () => Promise<void>) => {
    setBusy(runId);
    setActionError(null);
    try {
      await fn();
    } catch (err) {
      setActionError(errText(err));
    } finally {
      setBusy(null);
    }
  };

  const openRun = (run: RunLibrarySummary) => runAction(run.run_id, async () => {
    if (!run.published) {
      // Publish first so the existing adapter has the canonical artifact.
      await publishRun(run.run_id);
      await refreshArchived();
    }
    setSource({ mode: 'archived', runId: run.run_id });
    setNotice(`Opened ${run.run_id}`);
    setOpen(false);
  });

  const reuseRunConfig = (run: RunLibrarySummary) => runAction(run.run_id, async () => {
    const detail = await getRun(run.run_id);
    reuseConfig(detail);
    setNotice(`Loaded configuration from ${run.run_id}`);
    setOpen(false);
  });

  const openReport = (run: RunLibrarySummary) => {
    if (!run.report) return;
    window.open(runReportUrl(run.run_id), '_blank', 'noopener,noreferrer');
  };

  const confirmDeleteRun = (run: RunLibrarySummary) => runAction(run.run_id, async () => {
    await deleteRun(run.run_id);
    if (source.mode === 'archived' && source.runId === run.run_id) {
      setSource({ mode: 'demo' });
    }
    await refreshArchived();
    await loadRuns();
    setConfirmId(null);
    setNotice(`Deleted ${run.run_id}`);
  });

  const confirmDeleteStrategy = (entry: StrategyInfo) => runAction(entry.id, async () => {
    await deleteStrategy(basename(entry.file ?? entry.id));
    refreshCatalog();
    await loadStrategies();
    setConfirmId(null);
    setNotice(`Deleted strategy ${entry.name}`);
  });

  return (
    <>
      <button className="library-button" onClick={() => setOpen(true)} aria-label="Open Research Workspace">
        <Library size={14} /> Run Library
      </button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="workspace-dialog">
          <DialogTitle><Library size={18} />Research Workspace</DialogTitle>
          <DialogDescription>
            Saved runs and imported strategies. The local run archive is the source of truth.
          </DialogDescription>

          <Tabs value={tab} onValueChange={setTab}>
            <TabsList className="workspace-tabs">
              <TabsTrigger value="runs">Runs</TabsTrigger>
              <TabsTrigger value="compare">Compare</TabsTrigger>
              <TabsTrigger value="optimize">Optimize</TabsTrigger>
              <TabsTrigger value="walkforward">Walk-Forward</TabsTrigger>
              <TabsTrigger value="strategies">Strategies</TabsTrigger>
            </TabsList>
          </Tabs>

          {!connected && (
            <p className="lib-status lib-warn" role="status">Python engine unavailable — Run Library needs the bridge.</p>
          )}
          {notice && <p className="lib-status lib-ok" role="status">{notice}</p>}
          {actionError && (
            <p className="lib-status lib-error" role="alert"><AlertTriangle size={12} /> {actionError}</p>
          )}

          {tab === 'runs' && (
            <div className="lib-panel">
              <div className="lib-toolbar">
                <label className="lib-search">
                  <Search size={13} />
                  <input
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                    placeholder="Search run id / strategy / symbol"
                    aria-label="Search runs"
                  />
                </label>
                <button className="lib-refresh" onClick={loadRuns} disabled={!connected} aria-label="Refresh runs">
                  <RefreshCw size={13} />
                </button>
                <button
                  className="lib-compare"
                  onClick={() => setTab('compare')}
                  disabled={selectedRuns.length < 2}
                  aria-label="Compare selected runs"
                >
                  <GitCompare size={13} /> Compare ({selectedRuns.length})
                </button>
              </div>
              {runsError && <p className="lib-status lib-error" role="alert">{runsError}</p>}
              {!runsError && filteredRuns.length === 0 && (
                <p className="lib-empty">No saved runs{search ? ' match your search' : ' yet'}.</p>
              )}
              <div className="lib-list" role="list">
                {filteredRuns.map((run) => (
                  <div className="lib-row" role="listitem" key={run.run_id}>
                    <div className="lib-main">
                      <div className="lib-title">
                        <input
                          type="checkbox"
                          className="lib-select"
                          checked={selectedRuns.includes(run.run_id)}
                          disabled={!selectedRuns.includes(run.run_id) && selectedRuns.length >= 5}
                          onChange={() => toggleRunSelection(run.run_id)}
                          aria-label={`Select ${run.run_id}`}
                        />
                        <span className="lib-id">{run.run_id}</span>
                        <span className={'tag ' + (run.published ? 'good' : 'neutral')}>
                          {run.published ? 'PUBLISHED' : 'LOCAL'}
                        </span>
                        {!run.report && <span className="tag bad">NO REPORT</span>}
                      </div>
                      <div className="lib-meta">
                        {run.strategy.name} · v{run.strategy.version} · {run.symbol ?? '—'} {run.timeframe ?? ''} ·{' '}
                        {(run.start ?? '').slice(0, 10)}
                      </div>
                      <div className="lib-stats">
                        <span>Trades <b>{run.total_trades ?? '—'}</b></span>
                        <span>PnL <b className={(run.net_pnl ?? 0) >= 0 ? 'good-text' : 'bad-text'}>{fmtNum(run.net_pnl)}</b></span>
                        <span>Win <b>{run.win_rate === null || run.win_rate === undefined ? '—' : `${fmtNum(run.win_rate)}%`}</b></span>
                        <span>PF <b>{fmtNum(run.profit_factor)}</b></span>
                      </div>
                    </div>
                    {confirmId === run.run_id ? (
                      <div className="lib-confirm">
                        <span>Delete run? Archive, report and published artifact are removed.</span>
                        <button onClick={() => setConfirmId(null)}>Cancel</button>
                        <button className="danger" disabled={busy === run.run_id} onClick={() => confirmDeleteRun(run)}>
                          {busy === run.run_id ? <Loader2 size={13} className="spin" /> : <Trash2 size={13} />} Delete
                        </button>
                      </div>
                    ) : (
                      <div className="lib-actions">
                        <button onClick={() => openRun(run)} disabled={!connected || busy === run.run_id} aria-label={`Open ${run.run_id} in dashboard`}>
                          {busy === run.run_id ? <Loader2 size={13} className="spin" /> : <ExternalLink size={13} />} Open
                        </button>
                        <button
                          onClick={() => openReport(run)}
                          disabled={!run.report}
                          title={run.report ? 'Open standalone report' : 'No report available'}
                          aria-label={`Open report for ${run.run_id}`}
                        >
                          <FileText size={13} /> Report
                        </button>
                        <button onClick={() => reuseRunConfig(run)} disabled={!connected || busy === run.run_id} aria-label={`Reuse configuration from ${run.run_id}`}>
                          <RotateCcw size={13} /> Reuse
                        </button>
                        <button className="danger" onClick={() => setConfirmId(run.run_id)} aria-label={`Delete ${run.run_id}`}>
                          <Trash2 size={13} /> Delete
                        </button>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {tab === 'compare' && <RunComparison runIds={selectedRuns} />}

          {tab === 'optimize' && (
            <OptimizationPanel
              onCompare={(ids) => {
                setSelectedRuns(ids.slice(0, 5));
                setTab('compare');
              }}
            />
          )}

          {tab === 'walkforward' && (
            <WalkForwardPanel
              onCompare={(ids) => {
                setSelectedRuns(ids.slice(0, 5));
                setTab('compare');
              }}
            />
          )}

          {tab === 'strategies' && (
            <div className="lib-panel">
              <div className="lib-toolbar">
                <span className="lib-hint">Built-ins cannot be deleted; imported files can.</span>
                <button className="lib-refresh" onClick={loadStrategies} disabled={!connected} aria-label="Refresh strategies">
                  <RefreshCw size={13} />
                </button>
              </div>
              {strategiesError && <p className="lib-status lib-error" role="alert">{strategiesError}</p>}
              <div className="lib-list" role="list">
                {strategies.map((entry) => {
                  const isFile = entry.source === 'file';
                  return (
                    <div className="lib-row" role="listitem" key={entry.id}>
                      <div className="lib-main">
                        <div className="lib-title">
                          <span className="lib-id">{entry.name} · v{entry.version}</span>
                          <span className={'tag ' + (isFile ? 'neutral' : 'good')}>
                            {isFile ? 'IMPORTED' : 'BUILTIN'}
                          </span>
                        </div>
                        <div className="lib-meta">
                          {isFile ? basename(entry.file ?? entry.id) : 'built-in alias'}
                          {entry.description ? ` · ${entry.description}` : ''}
                        </div>
                        <div className="lib-stats">
                          <span>Parameters <b>{Object.keys(entry.parameters).length}</b></span>
                          {Object.entries(entry.parameters).map(([key, desc]) => (
                            <span key={key}>{key} <b>{desc.type}</b></span>
                          ))}
                        </div>
                      </div>
                      {isFile ? (
                        confirmId === entry.id ? (
                          <div className="lib-confirm">
                            <span>Delete this imported strategy? Historical runs keep their snapshot.</span>
                            <button onClick={() => setConfirmId(null)}>Cancel</button>
                            <button className="danger" disabled={busy === entry.id} onClick={() => confirmDeleteStrategy(entry)}>
                              {busy === entry.id ? <Loader2 size={13} className="spin" /> : <Trash2 size={13} />} Delete
                            </button>
                          </div>
                        ) : (
                          <div className="lib-actions">
                            <button className="danger" onClick={() => setConfirmId(entry.id)} aria-label={`Delete strategy ${entry.name}`}>
                              <Trash2 size={13} /> Delete
                            </button>
                          </div>
                        )
                      ) : (
                        <div className="lib-actions">
                          <button disabled title="Built-in strategies cannot be deleted" aria-label={`Built-in ${entry.name} protected`}>
                            <Trash2 size={13} /> Protected
                          </button>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </>
  );
}
