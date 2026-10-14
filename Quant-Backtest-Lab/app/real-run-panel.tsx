'use client';
import { useCallback, useEffect, useRef, useState } from 'react';
import { AlertTriangle, Loader2, Play, RefreshCw, Upload, Settings2 } from 'lucide-react';
import { Dialog, DialogContent, DialogTitle, DialogDescription } from '@/components/ui/dialog';
import { Select, SelectTrigger, SelectValue, SelectContent, SelectItem } from '@/components/ui/select';
import { useBridge } from './bridge-status';
import { useRunSource } from './run-context';
import { useRunJob } from './run-job-context';
import { useWorkspace } from './workspace-context';
import {
  BridgeError,
  importStrategy,
  listDatasets,
  listInstruments,
  listStrategies,
  listTimeframes,
  type DatasetInfo,
  type InstrumentInfo,
  type RunDetail,
  type StrategyInfo,
  type TimeframeOption,
} from './bridge-client';

type ParamValue = string | boolean;

function defaultParams(strategy: StrategyInfo | undefined): Record<string, ParamValue> {
  const out: Record<string, ParamValue> = {};
  if (!strategy) return out;
  for (const [key, desc] of Object.entries(strategy.parameters)) {
    out[key] = desc.type === 'bool' ? Boolean(desc.default) : String(desc.default ?? '');
  }
  return out;
}

function paramLabel(key: string): string {
  return key.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

/**
 * Real-run configuration, rendered inside the existing sidebar. Strategy
 * parameters are generated from the strategy's declared metadata (never
 * hardcoded) and kept visually separate from execution settings. It publishes
 * with publish=true so the newly generated run loads through the existing
 * adapter and the dashboard switches to it automatically.
 */
export function RealRunPanel() {
  const { state: bridge, refresh } = useBridge();
  const { refreshArchived } = useRunSource();
  const { job: activeJob, active: jobActive, start: startJob, outcome, clearOutcome } = useRunJob();
  const { pendingConfig, clearReuse, catalogVersion } = useWorkspace();
  const connected = bridge.status === 'connected';

  const [strategies, setStrategies] = useState<StrategyInfo[]>([]);
  const [datasets, setDatasets] = useState<DatasetInfo[]>([]);
  const [catalogError, setCatalogError] = useState<string | null>(null);

  const [strategyId, setStrategyId] = useState('');
  const [params, setParams] = useState<Record<string, ParamValue>>({});
  const [datasetId, setDatasetId] = useState('');
  const [symbol, setSymbol] = useState('XAUUSD');
  const [timeframe, setTimeframe] = useState('15min');
  const [timeframes, setTimeframes] = useState<TimeframeOption[]>([]);
  const [start, setStart] = useState('');
  const [end, setEnd] = useState('');
  const [stop, setStop] = useState('5');
  const [take, setTake] = useState('10');

  // Optional account / trading-economics model. Disabled by default: a run
  // without it behaves exactly as before (price-term only).
  const [instruments, setInstruments] = useState<InstrumentInfo[]>([]);
  const [accountEnabled, setAccountEnabled] = useState(false);
  const [accountCurrency, setAccountCurrency] = useState('USD');
  const [startingBalance, setStartingBalance] = useState('10000');
  const [positionLots, setPositionLots] = useState('1');
  const [commission, setCommission] = useState('0');
  const [, setContractSize] = useState('100');
  const [, setQuoteCurrency] = useState('USD');
  const [, setTickSize] = useState('0.01');

  const [settingsOpen, setSettingsOpen] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [runError, setRunError] = useState<string | null>(null);
  const [reuseWarning, setReuseWarning] = useState<string | null>(null);
  // Synchronous guard so two clicks in the same tick cannot start two runs
  // (provider state updates are async). Cleared on terminal outcome.
  const inFlight = useRef(false);
  // The activity bar owns the running state; the form only mirrors it.
  // Terminal outcome text is derived during render (no cascading effect):
  // completion is announced by the page shell after auto-loading.
  const running = jobActive;
  const shownError = runError ?? (outcome?.kind === 'failed' ? `run failed: ${outcome.message}` : null);
  const shownNotice = notice ?? (outcome?.kind === 'stopped' ? 'Backtest stopped · run discarded' : null);
  // Keep the current selection so a catalog reload (e.g. after a strategy
  // delete) does not silently reset it unless it was actually removed.
  const strategyIdRef = useRef('');

  const [importState, setImportState] = useState<'idle' | 'importing' | 'success'>('idle');
  const [importError, setImportError] = useState<string | null>(null);
  const [importedName, setImportedName] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const importInFlight = useRef(false);

  const strategy = strategies.find((s) => s.id === strategyId);
  const dataset = datasets.find((d) => d.dataset_id === datasetId);
  const loadingCatalogs = connected && strategies.length === 0 && !catalogError;

  // Select one strategy id (falling back to candle_direction/first) and load
  // its declared parameter defaults into the generic parameter form.
  const applyStrategySelection = useCallback((list: StrategyInfo[], wantedId?: string) => {
    const chosen =
      list.find((s) => s.id === wantedId) ??
      list.find((s) => s.id === 'candle_direction') ??
      list[0];
    strategyIdRef.current = chosen?.id ?? '';
    setStrategyId(chosen?.id ?? '');
    setParams(defaultParams(chosen));
  }, []);

  const loadCatalogs = useCallback(async () => {
    try {
      // First statement is async, so no synchronous setState during an effect.
      const [list, data, instr, tfs] = await Promise.all([
        listStrategies(), listDatasets(), listInstruments().catch(() => [] as InstrumentInfo[]),
        listTimeframes().catch(() => [] as TimeframeOption[]),
      ]);
      setStrategies(list);
      setDatasets(data);
      setInstruments(instr);
      setTimeframes(tfs);
      setTimeframe((cur) =>
        tfs.some((t) => t.value === cur)
          ? cur
          : (tfs.find((t) => t.value === '15min')?.value ?? tfs[0]?.value ?? cur),
      );
      setCatalogError(null);
      applyStrategySelection(list, strategyIdRef.current || undefined);
      const nextDataset = data.find((d) => d.complete_years?.length) ?? data[0];
      setDatasetId(nextDataset?.dataset_id ?? '');
      if (nextDataset) {
        setSymbol(nextDataset.symbol);
        setStart(nextDataset.default_start ?? '');
        setEnd(nextDataset.default_end ?? '');
      }
    } catch (err) {
      setCatalogError(err instanceof Error ? err.message : 'catalog unavailable');
      setStrategies([]);
      setDatasets([]);
    }
  }, [applyStrategySelection]);

  useEffect(() => {
    if (!connected) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- catalog load is an external-system sync; state lands after the await.
    loadCatalogs();
  }, [connected, catalogVersion, loadCatalogs]);

  /** Apply an archived run configuration requested from the Run Library. */
  const applyReusedConfig = useCallback(
    (detail: RunDetail) => {
      const warnings: string[] = [];
      const wanted = detail.selector ?? detail.file ?? '';
      const strat = strategies.find(
        (s) => s.id === wanted || (detail.file && s.file === detail.file),
      );
      if (strat) {
        strategyIdRef.current = strat.id;
        setStrategyId(strat.id);
        const defaults = defaultParams(strat);
        const archived = detail.parameters ?? {};
        const merged: Record<string, ParamValue> = { ...defaults };
        for (const key of Object.keys(defaults)) {
          if (key in archived) {
            merged[key] =
              typeof defaults[key] === 'boolean'
                ? Boolean(archived[key])
                : String(archived[key]);
          }
        }
        setParams(merged);
      } else if (wanted) {
        warnings.push(`strategy "${wanted}" is not in the catalog`);
      }

      const ds = datasets.find((d) => d.dataset_id === (detail.market?.dataset_id ?? ''));
      if (ds) {
        setDatasetId(ds.dataset_id);
      }
      if (detail.market?.symbol) setSymbol(detail.market.symbol);
      if (detail.market?.timeframe) setTimeframe(detail.market.timeframe);
      if (detail.market?.start) setStart(String(detail.market.start).slice(0, 10));
      if (detail.market?.end) setEnd(String(detail.market.end).slice(0, 10));
      if (detail.execution?.stop_distance != null) setStop(String(detail.execution.stop_distance));
      if (detail.execution?.take_profit_distance != null) {
        setTake(String(detail.execution.take_profit_distance));
      }
      if (detail.account) {
        setAccountEnabled(true);
        if (detail.account.account_currency) setAccountCurrency(detail.account.account_currency);
        if (detail.account.starting_balance != null) setStartingBalance(String(detail.account.starting_balance));
        if (detail.account.position_size_lots != null) setPositionLots(String(detail.account.position_size_lots));
        if (detail.account.commission_per_lot_round_turn != null) {
          setCommission(String(detail.account.commission_per_lot_round_turn));
        }
      }
      if (detail.instrument) {
        if (detail.instrument.quote_currency) setQuoteCurrency(detail.instrument.quote_currency);
        if (detail.instrument.contract_size != null) setContractSize(String(detail.instrument.contract_size));
        if (detail.instrument.tick_size != null) setTickSize(String(detail.instrument.tick_size));
      }
      setReuseWarning(warnings.length ? `Loaded with warnings: ${warnings.join('; ')}` : null);
    },
    [strategies, datasets],
  );

  useEffect(() => {
    if (!pendingConfig) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- applying an external reuse request.
    applyReusedConfig(pendingConfig);
    clearReuse();
  }, [pendingConfig, applyReusedConfig, clearReuse]);

  const selectStrategy = (id: string) => {
    strategyIdRef.current = id;
    setStrategyId(id);
    setParams(defaultParams(strategies.find((s) => s.id === id)));
  };

  const applyInstrumentFor = useCallback((sym: string) => {
    const spec = instruments.find((i) => i.symbol.toUpperCase() === sym.trim().toUpperCase());
    if (spec) {
      setQuoteCurrency(spec.quote_currency);
      setContractSize(String(spec.contract_size));
      if (spec.tick_size != null) setTickSize(String(spec.tick_size));
    }
  }, [instruments]);

  const toggleAccount = (enabled: boolean) => {
    setAccountEnabled(enabled);
    if (enabled) applyInstrumentFor(symbol);
  };

  const selectDataset = (id: string) => {
    setDatasetId(id);
    const chosen = datasets.find((d) => d.dataset_id === id);
    if (chosen) {
      setSymbol(chosen.symbol);
      setStart(chosen.default_start ?? '');
      setEnd(chosen.default_end ?? '');
      if (accountEnabled) applyInstrumentFor(chosen.symbol);
    }
  };

  const onFilePicked = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = ''; // allow re-selecting the same file later
    if (!file || importInFlight.current) return;
    if (!file.name.toLowerCase().endsWith('.py')) {
      setImportState('idle');
      setImportError('Only .py strategy files can be imported');
      setImportedName(null);
      return;
    }
    importInFlight.current = true;
    setImportState('importing');
    setImportError(null);
    setImportedName(file.name);
    try {
      const source = await file.text();
      const entry = await importStrategy({ filename: file.name, source });
      // Refresh the catalog (no browser reload) and select the new strategy;
      // the existing generic parameter form renders its declared parameters.
      const list = await listStrategies();
      setStrategies(list);
      applyStrategySelection(list, entry.id);
      setImportState('success');
      setImportedName(`${entry.name} · v${entry.version}`);
    } catch (err) {
      setImportState('idle');
      if (err instanceof BridgeError) {
        setImportError(
          err.code === 'strategy_conflict'
            ? `Filename already exists — choose another. (${err.message})`
            : `${err.code}: ${err.message}`,
        );
      } else {
        setImportError(err instanceof Error ? err.message : 'import failed');
      }
    } finally {
      importInFlight.current = false;
    }
  };

  const instrumentSpec = instruments.find((i) => i.symbol.toUpperCase() === symbol.trim().toUpperCase()) ?? null;

  const validate = (): string | null => {
    if (!strategy) return 'Select a strategy';
    if (!dataset) return 'Select a dataset';
    if (!symbol.trim()) return 'Select a dataset with a known symbol';
    if (timeframes.length > 0 && !timeframes.some((t) => t.value === timeframe)) {
      return 'Unsupported timeframe. Select one of the available timeframes.';
    }
    const stopN = Number(stop);
    const takeN = Number(take);
    if (!Number.isFinite(stopN) || stopN <= 0) return 'Stop distance must be a positive number';
    if (!Number.isFinite(takeN) || takeN <= 0) return 'Take profit distance must be a positive number';
    if (!start || !end) return 'Start and end dates are required';
    if (start > end) return 'Start date must be on or before the end date';
    if (accountEnabled) {
      if (!accountCurrency.trim()) return 'Account currency is required';
      if (!instrumentSpec) {
        return `No instrument economics configured for ${symbol || 'this symbol'} — choose a supported instrument.`;
      }
      const sb = Number(startingBalance);
      const lots = Number(positionLots);
      const comm = Number(commission);
      if (!Number.isFinite(sb) || sb <= 0) return 'Starting balance must be a positive number';
      if (!Number.isFinite(lots) || lots <= 0) return 'Position size must be a positive number';
      if (!Number.isFinite(comm) || comm < 0) return 'Commission must be zero or positive';
    }
    for (const [key, desc] of Object.entries(strategy.parameters)) {
      const value = params[key];
      if (desc.type === 'bool') continue;
      if (desc.type === 'int') {
        const n = Number(value);
        if (!Number.isInteger(n)) return `${paramLabel(key)} must be an integer`;
      } else if (desc.type === 'float') {
        if (!Number.isFinite(Number(value))) return `${paramLabel(key)} must be a number`;
      } else if (value === '') {
        return `${paramLabel(key)} is required`;
      }
    }
    return null;
  };

  const ready = connected && !loadingCatalogs && !running && !!strategy && !!dataset && !!symbol.trim()
    && timeframes.length > 0 && timeframes.some((t) => t.value === timeframe);

  const submit = async () => {
    if (inFlight.current || running || !strategy || !dataset) return;
    const problem = validate();
    if (problem) {
      setRunError(problem);
      setNotice(null);
      return;
    }
    inFlight.current = true;
    setRunError(null);
    setNotice(null);
    clearOutcome();
    // Typed strategy parameters derived from declared metadata (no hardcoding).
    const parameters: Record<string, unknown> = {};
    for (const [key, desc] of Object.entries(strategy.parameters)) {
      const value = params[key];
      if (desc.type === 'bool') parameters[key] = Boolean(value);
      else if (desc.type === 'int') parameters[key] = parseInt(String(value), 10);
      else if (desc.type === 'float') parameters[key] = Number(value);
      else parameters[key] = String(value);
    }
    try {
      // Async job: the worker runs the existing runner; the activity bar
      // tracks it and the page shell auto-loads the result on completion.
      // The currently visible result stays until then (never cleared here).
      await startJob({
        strategy: { selector: strategy.id, parameters },
        market: {
          dataset_id: dataset.dataset_id,
          symbol,
          timeframe,
          start,
          end,
        },
        execution: {
          stop_distance: Number(stop),
          take_profit_distance: Number(take),
        },
        ...(accountEnabled
          ? {
              account: {
                account_currency: accountCurrency.trim(),
                starting_balance: Number(startingBalance),
                position_size_lots: Number(positionLots),
                commission_per_lot_round_turn: Number(commission),
              },
              instrument: instrumentSpec
                ? {
                    symbol,
                    quote_currency: instrumentSpec.quote_currency,
                    contract_size: instrumentSpec.contract_size,
                    ...(instrumentSpec.tick_size != null ? { tick_size: instrumentSpec.tick_size } : {}),
                  }
                : undefined,
            }
          : {}),
        publish: true,
      });
      setNotice('Backtest started — tracking live progress above.');
      // Refresh the library in the background so the new run appears there
      // on completion even if the manifest lags by a poll tick.
      refreshArchived();
    } catch (err) {
      // Never fall back to Demo on a real-run failure.
      inFlight.current = false;
      if (err instanceof BridgeError) {
        setRunError(err.code === 'run_in_progress'
          ? 'A backtest is already running — stop it before starting another.'
          : `${err.code}: ${err.message}`);
      } else setRunError(err instanceof Error ? err.message : 'run failed');
    }
  };

  return (
    <div className="config-section real-run">
      {!connected && (
        <p className="rr-status rr-offline" role="status">
          {bridge.status === 'checking'
            ? 'Contacting Python Bridge…'
            : 'Python Bridge Offline — start the bridge to run a real backtest.'}
          {bridge.status === 'unavailable' && (
            <button type="button" className="rr-retry" onClick={refresh}>
              <RefreshCw size={12} /> Retry
            </button>
          )}
        </p>
      )}

      {connected && catalogError && (
        <p className="rr-status rr-offline" role="alert">
          Catalog unavailable: {catalogError}
          <button type="button" className="rr-retry" onClick={loadCatalogs}>
            <RefreshCw size={12} /> Retry
          </button>
        </p>
      )}

      <fieldset disabled={!ready} className="rr-fieldset">
        <label>
          Strategy
          <Select value={strategyId} onValueChange={selectStrategy}>
            <SelectTrigger aria-label="Real run strategy">
              <SelectValue placeholder={loadingCatalogs ? 'Loading…' : 'Select strategy'} />
            </SelectTrigger>
            <SelectContent>
              {strategies.map((s) => (
                <SelectItem key={s.id} value={s.id}>
                  {s.name} · v{s.version}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </label>
        {strategy?.description && <p className="strategy-desc">{strategy.description}</p>}

        <div className="rr-import">
          <button
            type="button"
            className="rr-import-btn"
            onClick={() => fileInputRef.current?.click()}
            disabled={!connected || importState === 'importing'}
          >
            {importState === 'importing' ? <Loader2 size={13} className="spin" /> : <Upload size={13} />}
            {importState === 'importing' ? 'Importing…' : 'Import .py Strategy'}
          </button>
          <input
            ref={fileInputRef}
            type="file"
            accept=".py,text/x-python"
            className="rr-file"
            aria-label="Import strategy file"
            onChange={onFilePicked}
          />
          {importError && (
            <p className="rr-error" role="alert">
              <AlertTriangle size={12} /> {importError}
            </p>
          )}
          {importState === 'success' && importedName && (
            <p className="rr-import-ok" role="status">Imported {importedName}</p>
          )}
        </div>

        <button
          type="button"
          className="rr-settings-btn"
          onClick={() => setSettingsOpen(true)}
          aria-label="Strategy & Trade Settings"
        >
          <Settings2 size={13} /> Strategy &amp; Trade Settings
          <span className="meta">
            {Object.keys(strategy?.parameters ?? {}).length} params · {stop}/{take} · {accountEnabled ? `${accountCurrency} account` : 'price terms'}
          </span>
        </button>

        <div className="rr-group-label">Market / Data</div>
        <label>
          Dataset
          <Select value={datasetId} onValueChange={selectDataset}>
            <SelectTrigger aria-label="Real run dataset">
              <SelectValue placeholder={loadingCatalogs ? 'Loading…' : 'Select dataset'} />
            </SelectTrigger>
            <SelectContent>
              {datasets.map((d) => (
                <SelectItem key={d.dataset_id} value={d.dataset_id}>
                  {d.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </label>
        <label>
          Symbol <span className="meta">· from dataset</span>
          <span className="rr-derived" aria-label="Run symbol">{symbol || '—'}</span>
        </label>
        <label>
          Timeframe
          <Select value={timeframe} onValueChange={setTimeframe}>
            <SelectTrigger aria-label="Run timeframe"><SelectValue placeholder="Select timeframe" /></SelectTrigger>
            <SelectContent>
              {timeframes.map((t) => <SelectItem key={t.value} value={t.value}>{t.label}</SelectItem>)}
            </SelectContent>
          </Select>
        </label>
        <label>
          Date Range <span className="meta">UTC</span>
          <input type="date" value={start} onChange={(e) => setStart(e.target.value)} aria-label="Run start date" />
          <span className="date-to">to</span>
          <input type="date" value={end} onChange={(e) => setEnd(e.target.value)} aria-label="Run end date" />
        </label>
        {dataset && (
          <p className="rr-help" aria-label="Dataset status">
            Coverage {String(dataset.coverage_start).slice(0, 10)} → {String(dataset.coverage_end).slice(0, 10)} ·{' '}
            {dataset.prepared_timeframes.includes(timeframe) ? 'Data Ready' : `Preparation required for ${timeframe}`}
            {dataset.partial_months.length ? ` · partial: ${dataset.partial_months.join(', ')}` : ''}
          </p>
        )}
      </fieldset>

      {reuseWarning && (
        <p className="rr-warn" role="status">
          <AlertTriangle size={12} /> {reuseWarning}
        </p>
      )}
      {shownError && (
        <p className="rr-error" role="alert">
          <AlertTriangle size={12} /> {shownError}
        </p>
      )}
      {shownNotice && <p className="rr-notice" role="status">{shownNotice}</p>}

      {jobActive && activeJob && (
        <p className="rr-notice" role="status">
          {activeJob.state === 'stopping' ? 'Stopping the active backtest…' : `Tracking ${activeJob.job_id} · ${activeJob.stage_label}`}
        </p>
      )}

      <button
        className="primary run"
        onClick={submit}
        disabled={!ready}
        aria-disabled={!ready}
        title={running ? 'A backtest is already running — stop it before starting another.' : undefined}
      >
        {running ? <Loader2 size={15} className="spin" /> : <Play size={15} fill="currentColor" />}
        {running ? 'Backtest running…' : 'Run Backtest'}
      </button>
      <p className="config-note">Archives + report + publishes to the dashboard</p>

            <Dialog open={settingsOpen} onOpenChange={setSettingsOpen}>
        <DialogContent className="settings-dialog">
          <DialogTitle><Settings2 size={16} /> Strategy &amp; Trade Settings</DialogTitle>
          <DialogDescription>
            Strategy inputs, trade execution and the optional account model for this backtest.
          </DialogDescription>

          <div className="rr-group-label">Strategy inputs</div>
          {strategy && Object.keys(strategy.parameters).length > 0 ? (
            <div className="rr-params settings-params">
              {Object.entries(strategy.parameters).map(([key, desc]) =>
                desc.type === 'bool' ? (
                  <label key={key} className="rr-check">
                    <input
                      type="checkbox"
                      aria-label={`Strategy parameter ${key}`}
                      checked={Boolean(params[key])}
                      onChange={(e) => setParams((p) => ({ ...p, [key]: e.target.checked }))}
                    />
                    <span>{paramLabel(key)}</span>
                  </label>
                ) : (
                  <label key={key}>
                    {paramLabel(key)}
                    <span className="meta"> · {desc.type}</span>
                    <input
                      type={desc.type === 'string' ? 'text' : 'number'}
                      step={desc.type === 'int' ? 1 : 'any'}
                      aria-label={`Strategy parameter ${key}`}
                      value={String(params[key] ?? '')}
                      onChange={(e) => setParams((p) => ({ ...p, [key]: e.target.value }))}
                    />
                  </label>
                ),
              )}
            </div>
          ) : (
            <p className="meta">This strategy declares no parameters.</p>
          )}

          <div className="rr-group-label">Trade / Execution</div>
          <p className="rr-help">Stop / take levels are engine execution inputs, not strategy parameters.</p>
          <label>
            Stop Distance
            <input type="number" step="any" value={stop} onChange={(e) => setStop(e.target.value)} aria-label="Stop distance" />
          </label>
          <label>
            Take Profit Distance
            <input type="number" step="any" value={take} onChange={(e) => setTake(e.target.value)} aria-label="Take profit distance" />
          </label>

          <div className="rr-group-label">Position sizing &amp; account <span className="meta">· optional</span></div>
          <label className="rr-check">
            <input
              type="checkbox"
              aria-label="Enable account model"
              checked={accountEnabled}
              onChange={(e) => toggleAccount(e.target.checked)}
            />
            <span>Enable Account Model</span>
          </label>
          <p className="rr-help">
            Optional — converts price-based PnL into account currency, realized balance, return, and
            commission-aware metrics. Position size is a run-level lot size applied to every trade.
          </p>
          {accountEnabled && (
            <div className="rr-account">
              <label>
                Starting Balance
                <input type="number" step="any" value={startingBalance} onChange={(e) => setStartingBalance(e.target.value)} aria-label="Account starting balance" />
              </label>
              <label>
                Account Currency
                <input value={accountCurrency} onChange={(e) => setAccountCurrency(e.target.value)} aria-label="Account currency" />
              </label>
              <label>
                Position Size <span className="meta">· lots · run-level</span>
                <input type="number" step="any" value={positionLots} onChange={(e) => setPositionLots(e.target.value)} aria-label="Account position size lots" />
              </label>
              <label>
                Commission <span className="meta">· / lot / round turn</span>
                <input type="number" step="any" value={commission} onChange={(e) => setCommission(e.target.value)} aria-label="Account commission per lot" />
              </label>
                            {instrumentSpec && accountCurrency.trim().toUpperCase() !== instrumentSpec.quote_currency.trim().toUpperCase() && (
                <p className="rr-warn" role="status">
                  <AlertTriangle size={12} /> Account currency and quote currency differ — account metrics stay unavailable (no FX conversion in v1).
                </p>
              )}
            </div>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
