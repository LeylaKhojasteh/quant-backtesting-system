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
  // Two market modes: tick execution for FX/metals, candle execution for
  // markets that only publish OHLC. The dataset itself decides the model.
  const [marketMode, setMarketMode] = useState<'tick' | 'candle'>('tick');
  const [spreadBps, setSpreadBps] = useState('2');
  const [catalogError, setCatalogError] = useState<string | null>(null);

  const [strategyId, setStrategyId] = useState('');
  const [params, setParams] = useState<Record<string, ParamValue>>({});
  const [datasetId, setDatasetId] = useState('');
  const [symbol, setSymbol] = useState('');
  const [timeframe, setTimeframe] = useState('15min');
  const [timeframes, setTimeframes] = useState<TimeframeOption[]>([]);
  const [start, setStart] = useState('');
  const [end, setEnd] = useState('');
  // SL/TP are entered in PIPS. Empty means "use the instrument's default",
  // derived from the legacy price-distance defaults (5.0 / 10.0) so the
  // effective default backtest behavior is unchanged.
  const [stop, setStop] = useState('');
  const [take, setTake] = useState('');
  // Price-distance fallback for symbols without pip metadata (e.g. crypto).
  const [stopPrice, setStopPrice] = useState('');
  const [takePrice, setTakePrice] = useState('');
  // SL/TP are optional: when unchecked the run is sent without any exit level.
  const [riskEnabled, setRiskEnabled] = useState(true);

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
  // (provider state updates are async). Stays locked while the job is active
  // and is released when the job settles so the next run can start without
  // a page refresh.
  const inFlight = useRef(false);
  // The activity bar owns the running state; the form only mirrors it.
  // Terminal outcome text is derived during render (no cascading effect):
  // completion is announced by the page shell after auto-loading.
  const running = jobActive;
  useEffect(() => {
    if (!jobActive) inFlight.current = false;
  }, [jobActive]);
  useEffect(() => {
    if (outcome) inFlight.current = false;
  }, [outcome]);
  const shownError = runError ?? (outcome?.kind === 'failed' ? `run failed: ${outcome.message}` : null);
  const shownNotice = notice ?? (outcome?.kind === 'stopped' ? 'Backtest stopped · run discarded' : null);
  // Keep the current selection so a catalog reload (e.g. after a strategy
  // delete) does not silently reset it unless it was actually removed.
  const strategyIdRef = useRef('');

  const [importState, setImportState] = useState<'idle' | 'importing' | 'success'>('idle');
  const [importError, setImportError] = useState<string | null>(null);
  const [importedName, setImportedName] = useState<string | null>(null);
  const [pendingOverwrite, setPendingOverwrite] = useState<{ filename: string; source: string } | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const importInFlight = useRef(false);

  const strategy = strategies.find((s) => s.id === strategyId);
  const dataset = datasets.find((d) => d.dataset_id === datasetId);
  const isCandleDataset = dataset?.data_type === 'candles';
  const visibleDatasets = datasets.filter(
    (d) => (d.data_type === 'candles') === (marketMode === 'candle'),
  );
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
      // Prefer a tick dataset with complete years; fall back to the first one
      // of either type so a candle-only install still selects something.
      const nextDataset =
        data.find((d) => d.data_type !== 'candles' && d.complete_years?.length) ??
        data.find((d) => d.data_type !== 'candles') ??
        data[0];
      setMarketMode(nextDataset?.data_type === 'candles' ? 'candle' : 'tick');
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
      // Prefer archived pip intent; else convert the legacy price distance to
      // pips for display only (the archive remains the source of truth).
      const detailPip = detail.execution?.pip_size ?? detail.instrument?.pip_size ?? null;
      if (detail.execution?.stop_loss_pips != null) {
        setStop(String(detail.execution.stop_loss_pips));
      } else if (detail.execution?.stop_distance != null && detailPip) {
        setStop(String(+(Number(detail.execution.stop_distance) / detailPip).toFixed(10)));
      }
      if (detail.execution?.take_profit_pips != null) {
        setTake(String(detail.execution.take_profit_pips));
      } else if (detail.execution?.take_profit_distance != null && detailPip) {
        setTake(String(+(Number(detail.execution.take_profit_distance) / detailPip).toFixed(10)));
      }
      if (detail.execution?.stop_distance != null) {
        setStopPrice(String(detail.execution.stop_distance));
      }
      if (detail.execution?.take_profit_distance != null) {
        setTakePrice(String(detail.execution.take_profit_distance));
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
      setMarketMode(chosen.data_type === 'candles' ? 'candle' : 'tick');
      setSymbol(chosen.symbol);
      setStart(chosen.default_start ?? '');
      setEnd(chosen.default_end ?? '');
      if (accountEnabled) applyInstrumentFor(chosen.symbol);
    }
  };

  const switchMarketMode = (mode: 'tick' | 'candle') => {
    setMarketMode(mode);
    const next = datasets.find((d) => (d.data_type === 'candles') === (mode === 'candle'));
    if (next) {
      selectDataset(next.dataset_id);
    } else {
      // Nothing imported for this market yet: never keep the other market's
      // dataset selected behind the toggle.
      setDatasetId('');
      setSymbol('');
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
    let source = '';
    try {
      source = await file.text();
    } catch {
      setImportState('idle');
      setImportError('could not read file');
      setImportedName(null);
      return;
    }
    importInFlight.current = true;
    setImportState('importing');
    setImportError(null);
    setImportedName(file.name);
    try {
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
        if (err.code === 'strategy_conflict') {
          // Same filename, new content: offer an explicit one-click update.
          setImportError(null);
          setPendingOverwrite({ filename: file.name, source });
        } else {
          setImportError(`${err.code}: ${err.message}`);
        }
      } else {
        setImportError(err instanceof Error ? err.message : 'import failed');
      }
    } finally {
      importInFlight.current = false;
    }
  };

  const confirmOverwrite = async () => {
    if (!pendingOverwrite || importInFlight.current) return;
    importInFlight.current = true;
    setImportState('importing');
    try {
      const entry = await importStrategy({ ...pendingOverwrite, overwrite: true });
      const list = await listStrategies();
      setStrategies(list);
      applyStrategySelection(list, entry.id);
      setImportState('success');
      setImportedName(`Updated ${entry.name} · v${entry.version}`);
      setPendingOverwrite(null);
    } catch (err) {
      setImportState('idle');
      setImportError(err instanceof BridgeError ? `${err.code}: ${err.message}` : err instanceof Error ? err.message : 'import failed');
    } finally {
      importInFlight.current = false;
    }
  };

  const instrumentSpec = instruments.find((i) => i.symbol.toUpperCase() === symbol.trim().toUpperCase()) ?? null;

  // Pip-denominated SL/TP. The displayed value follows the instrument's pip
  // size until the user edits it, so the default effective distance stays at
  // the legacy 5.0 / 10.0 price units (no silent research-behavior change).
  const pipSize = instrumentSpec?.pip_size ?? null;
  const defaultStopPips = pipSize ? String(+(5 / pipSize).toFixed(10)) : '';
  const defaultTakePips = pipSize ? String(+(10 / pipSize).toFixed(10)) : '';
  const stopPipsValue = stop !== '' ? stop : defaultStopPips;
  const takePipsValue = take !== '' ? take : defaultTakePips;
  const stopPriceValue = stopPrice !== '' ? stopPrice : '5';
  const takePriceValue = takePrice !== '' ? takePrice : '10';
  const effectiveDistance = (pips: string) => {
    const n = Number(pips);
    return pipSize && Number.isFinite(n) ? n * pipSize : null;
  };

  const validate = (): string | null => {
    if (!strategy) return 'Select a strategy';
    if (!dataset) return 'Select a dataset';
    // The market toggle only filters the list; the selected dataset must still
    // belong to that market or the run would execute on the wrong model.
    if ((dataset.data_type === 'candles') !== (marketMode === 'candle')) {
      return 'Select a dataset for the chosen market';
    }
    if (!symbol.trim()) return 'Select a dataset with a known symbol';
    if (timeframes.length > 0 && !timeframes.some((t) => t.value === timeframe)) {
      return 'Unsupported timeframe. Select one of the available timeframes.';
    }
    const isExternal = strategy.kind === 'external' || strategy.execution_model === 'strategy';
    if (riskEnabled && !isExternal) {
      const stopN = Number(pipSize ? stopPipsValue : stopPriceValue);
      const takeN = Number(pipSize ? takePipsValue : takePriceValue);
      const unit = pipSize ? 'pips' : 'price units';
      if (!Number.isFinite(stopN) || stopN <= 0) return `Stop Loss (${unit}) must be a positive number`;
      if (!Number.isFinite(takeN) || takeN <= 0) return `Take Profit (${unit}) must be a positive number`;
    }
    if (isCandleDataset) {
      const bps = Number(spreadBps);
      if (!Number.isFinite(bps) || bps < 0) {
        return 'Spread (bps) must be zero or a positive number';
      }
    }
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
        execution: (() => {
          const levels: Record<string, number> = {};
          const strategyOwnsExecution =
            strategy.kind === 'external' || strategy.execution_model === 'strategy';
          if (riskEnabled && !strategyOwnsExecution) {
            if (pipSize) {
              levels.stop_loss_pips = Number(stopPipsValue);
              levels.take_profit_pips = Number(takePipsValue);
            } else {
              levels.stop_distance = Number(stopPriceValue);
              levels.take_profit_distance = Number(takePriceValue);
            }
          }
          // Candle datasets have no measured spread; it is an explicit input.
          if (isCandleDataset) levels.spread_bps = Number(spreadBps);
          return levels;
        })(),
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
                    ...(instrumentSpec.pip_size != null ? { pip_size: instrumentSpec.pip_size } : {}),
                    ...(instrumentSpec.asset_class ? { asset_class: instrumentSpec.asset_class } : {}),
                    ...(instrumentSpec.digits != null ? { digits: instrumentSpec.digits } : {}),
                    ...(instrumentSpec.price_scale != null ? { price_scale: instrumentSpec.price_scale } : {}),
                    ...(instrumentSpec.session ? { session: instrumentSpec.session } : {}),
                    ...(instrumentSpec.leverage != null ? { leverage: instrumentSpec.leverage } : {}),
                    ...(instrumentSpec.margin_rate != null ? { margin_rate: instrumentSpec.margin_rate } : {}),
                    ...(instrumentSpec.swap != null ? { swap: instrumentSpec.swap } : {}),
                  }
                : undefined,
            }
          : {}),
        publish: true,
      });
      setNotice('Backtest started — tracking live progress above.');
      // Refresh the library in the background so the new run appears there
      // on completion even if the manifest lags by a poll tick.
      refreshArchived().catch(() => {});
    } catch (err) {
      // Never fall back to placeholder data on a real-run failure.
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
          {bridge.status === 'checking' || bridge.status === 'connecting'
            ? 'Contacting Python Bridge…'
            : bridge.reason === 'network'
              ? 'Network disconnected — check your network.'
              : 'Python Bridge Offline — start the bridge to run a real backtest.'}
          {bridge.status === 'disconnected' && (
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
            title="Opens in the strategies folder — pick the .py file to import or update"
            onChange={onFilePicked}
          />
          {importError && (
            <p className="rr-error" role="alert">
              <AlertTriangle size={12} /> {importError}
            </p>
          )}
          {pendingOverwrite && (
            <p className="rr-warn" role="status">
              {pendingOverwrite.filename} is already imported.{' '}
              <button type="button" onClick={confirmOverwrite} disabled={importState === 'importing'}>
                Update it in place
              </button>{' '}
              <button type="button" onClick={() => setPendingOverwrite(null)}>
                Cancel
              </button>
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
        <div className="rr-mode" role="group" aria-label="Market mode">
          <button
            type="button"
            className={marketMode === 'tick' ? 'rr-mode-btn active' : 'rr-mode-btn'}
            aria-pressed={marketMode === 'tick'}
            onClick={() => switchMarketMode('tick')}
          >
            FX &amp; Metals
          </button>
          <button
            type="button"
            className={marketMode === 'candle' ? 'rr-mode-btn active' : 'rr-mode-btn'}
            aria-pressed={marketMode === 'candle'}
            onClick={() => switchMarketMode('candle')}
          >
            Crypto
          </button>
        </div>
        <label>
          Dataset
          <Select value={datasetId} onValueChange={selectDataset}>
            <SelectTrigger aria-label="Real run dataset">
              <SelectValue
                placeholder={
                  loadingCatalogs
                    ? 'Loading…'
                    : visibleDatasets.length === 0
                      ? 'No datasets imported for this market'
                      : 'Select dataset'
                }
              />
            </SelectTrigger>
            <SelectContent>
              {visibleDatasets.map((d) => (
                <SelectItem key={d.dataset_id} value={d.dataset_id}>
                  {d.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {isCandleDataset && (
            <span className="rr-help">
              Candle execution: fills use bar prices and the assumed spread.
            </span>
          )}
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
          <input
            type="date"
            value={start.slice(0, 10)}
            min={dataset?.coverage_start?.slice(0, 10)}
            max={(end || dataset?.coverage_end)?.slice(0, 10)}
            onChange={(e) => setStart(e.target.value)}
            aria-label="Run start date"
          />
          <span className="date-to">to</span>
          <input
            type="date"
            value={end.slice(0, 10)}
            min={(start || dataset?.coverage_start)?.slice(0, 10)}
            max={dataset?.coverage_end?.slice(0, 10)}
            onChange={(e) => setEnd(e.target.value)}
            aria-label="Run end date"
          />
        </label>
        {isCandleDataset && (
          <label title="Full spread in basis points of the bar price. Buy pays ask, sell receives bid.">
            Spread <span className="meta">bps · assumed, not measured</span>
            <input
              type="number"
              step="any"
              min="0"
              value={spreadBps}
              onChange={(e) => setSpreadBps(e.target.value)}
              aria-label="Assumed spread in basis points"
            />
          </label>
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
          {strategy?.kind === 'external' || strategy?.execution_model === 'strategy' ? (
            <p className="rr-help">
              Strategy-owned execution: this strategy produces its own trades and
              manages its own exits. Engine SL / TP do not apply.
            </p>
          ) : (
          <>
          <p className="rr-help">Stop / take levels are engine execution inputs, not strategy parameters.</p>
          <label className="rr-check">
            <input
              type="checkbox"
              aria-label="Use stop loss and take profit"
              checked={riskEnabled}
              onChange={(e) => setRiskEnabled(e.target.checked)}
            />
            Use SL / TP
          </label>
          <label title={pipSize ? "Converted to price distance using the selected instrument's pip size." : 'Applied directly in instrument price units.'}>
            {pipSize ? 'Stop Loss (pips)' : 'Stop Loss (price units)'}
            <input
              type="number"
              step="any"
              min="0"
              disabled={!riskEnabled}
              value={pipSize ? stopPipsValue : stopPriceValue}
              onChange={(e) => (pipSize ? setStop(e.target.value) : setStopPrice(e.target.value))}
              aria-label={pipSize ? 'Stop loss pips' : 'Stop loss price distance'}
            />
          </label>
          <label title={pipSize ? "Converted to price distance using the selected instrument's pip size." : 'Applied directly in instrument price units.'}>
            {pipSize ? 'Take Profit (pips)' : 'Take Profit (price units)'}
            <input
              type="number"
              step="any"
              min="0"
              disabled={!riskEnabled}
              value={pipSize ? takePipsValue : takePriceValue}
              onChange={(e) => (pipSize ? setTake(e.target.value) : setTakePrice(e.target.value))}
              aria-label={pipSize ? 'Take profit pips' : 'Take profit price distance'}
            />
          </label>
          {pipSize ? (
            <p className="rr-help">
              Effective distance: SL {effectiveDistance(stopPipsValue)?.toFixed(5)} · TP {effectiveDistance(takePipsValue)?.toFixed(5)} price units
              {' '}(1 pip = {pipSize} · {symbol.trim().toUpperCase()})
            </p>
          ) : (
            <p className="rr-help">
              SL/TP use price units for {symbol.trim().toUpperCase() || 'this symbol'}.
            </p>
          )}
          </>
          )}

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
