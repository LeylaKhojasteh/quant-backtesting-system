/**
 * Frontend client for the local Python bridge (src/bridge.py).
 *
 * This is the only place the UI talks HTTP/JSON to Python. It is deliberately
 * framework-agnostic (no React, no chart/report imports) so it can be used by
 * any view later without touching the existing BacktestRun / RunProvider
 * architecture. HTTP calls stay outside chart and report components.
 */

export const BRIDGE_DEFAULT_URL = 'http://127.0.0.1:8765';

export interface BridgeHealth {
  status: string;
  service: string;
  version: string;
}

export type ParameterType = 'int' | 'float' | 'bool' | 'string' | 'unknown';

export interface StrategyParameterDescriptor {
  type: ParameterType;
  default: unknown;
}

export interface StrategyInfo {
  id: string;
  name: string;
  version: string;
  description: string;
  source: 'builtin' | 'file' | string;
  file: string | null;
  parameters: Record<string, StrategyParameterDescriptor>;
}

export interface DatasetInfo {
  id: string;
  filename: string;
  path: string;
  symbol: string;
  format: string;
  /** ISO date (YYYY-MM-DD) when deterministically known, else null. */
  start: string | null;
  end: string | null;
  bytes: number;
}

export interface BacktestRunRequestPayload {
  strategy: { selector: string; parameters?: Record<string, unknown> };
  market: {
    symbol: string;
    timeframe?: string;
    start?: string | null;
    end?: string | null;
    data_source: string;
  };
  execution?: { stop_distance?: number; take_profit_distance?: number };
  publish?: boolean;
}

export interface RunSummary {
  total_trades: number;
  win_rate: number;
  net_pnl: number;
  signals_received: number;
}

export interface RunResponse {
  run_id: string;
  status: string;
  strategy: { name: string; version: string; description: string };
  parameters: Record<string, unknown>;
  market: { symbol: string; timeframe: string; start: string; end: string };
  execution: { stop_distance: number; take_profit_distance: number };
  summary: RunSummary;
  archive: { directory: string; result: string; report: string | null };
  published: boolean;
  published_file: string | null;
}

/** Structured error returned by the bridge (code + HTTP status). */
export class BridgeError extends Error {
  code: string;
  status: number;

  constructor(message: string, code: string, status: number) {
    super(message);
    this.name = 'BridgeError';
    this.code = code;
    this.status = status;
  }
}

/** Base URL of the local bridge (env-overridable, defaults to localhost). */
export function bridgeBaseUrl(): string {
  const fromEnv =
    typeof process !== 'undefined' && process.env
      ? process.env.NEXT_PUBLIC_BRIDGE_URL
      : undefined;
  return (fromEnv && fromEnv.trim()) || BRIDGE_DEFAULT_URL;
}

async function requestJson<T>(
  path: string,
  init: RequestInit | undefined,
  fetchImpl?: typeof fetch,
): Promise<T> {
  const doFetch = fetchImpl ?? globalThis.fetch;
  const response = await doFetch(`${bridgeBaseUrl()}${path}`, init);
  const text = await response.text();
  let parsed: unknown;
  if (text) {
    try {
      parsed = JSON.parse(text);
    } catch {
      parsed = undefined;
    }
  }
  if (!response.ok) {
    let code = 'bridge_error';
    let message = `bridge request failed (${response.status})`;
    if (parsed && typeof parsed === 'object' && 'error' in parsed) {
      const error = (parsed as { error?: unknown }).error;
      if (error && typeof error === 'object') {
        const shape = error as { code?: unknown; message?: unknown };
        if (typeof shape.code === 'string') code = shape.code;
        if (typeof shape.message === 'string') message = shape.message;
      }
    }
    throw new BridgeError(message, code, response.status);
  }
  return parsed as T;
}

export async function getHealth(fetchImpl?: typeof fetch): Promise<BridgeHealth> {
  return requestJson<BridgeHealth>('/health', undefined, fetchImpl);
}

export async function listStrategies(fetchImpl?: typeof fetch): Promise<StrategyInfo[]> {
  const data = await requestJson<{ strategies?: StrategyInfo[] }>(
    '/strategies',
    undefined,
    fetchImpl,
  );
  return Array.isArray(data?.strategies) ? data.strategies : [];
}

export async function listDatasets(fetchImpl?: typeof fetch): Promise<DatasetInfo[]> {
  const data = await requestJson<{ datasets?: DatasetInfo[] }>(
    '/datasets',
    undefined,
    fetchImpl,
  );
  return Array.isArray(data?.datasets) ? data.datasets : [];
}

export async function submitRun(
  payload: BacktestRunRequestPayload,
  fetchImpl?: typeof fetch,
): Promise<RunResponse> {
  return requestJson<RunResponse>(
    '/runs',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    },
    fetchImpl,
  );
}

export interface ImportStrategyPayload {
  filename: string;
  source: string;
}

/** Import one trusted local .py strategy; returns its catalog entry. */
export async function importStrategy(
  payload: ImportStrategyPayload,
  fetchImpl?: typeof fetch,
): Promise<StrategyInfo> {
  const data = await requestJson<{ strategy: StrategyInfo }>(
    '/strategies/import',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    },
    fetchImpl,
  );
  return data.strategy;
}

export interface RunLibrarySummary {
  run_id: string;
  created_at: string | null;
  strategy: { name: string; version: string };
  strategy_selector: string | null;
  strategy_file: string | null;
  symbol: string | null;
  timeframe: string | null;
  start: string | null;
  end: string | null;
  total_trades: number | null;
  win_rate: number | null;
  net_pnl: number | null;
  profit_factor: number | null;
  report: boolean;
  published: boolean;
}

export interface RunDetail extends RunLibrarySummary {
  description: string;
  parameters: Record<string, unknown>;
  overrides: Record<string, unknown>;
  execution: { stop_distance: number | null; take_profit_distance: number | null };
  market: {
    symbol: string | null;
    timeframe: string | null;
    start: string | null;
    end: string | null;
    data_source: string;
    dataset: string | null;
  };
  selector: string | null;
  file: string | null;
}

export interface DeleteRunResult {
  run_id: string;
  deleted: boolean;
  removed_artifact: string | null;
  removed_manifest_entry: boolean;
}

export interface DeleteStrategyResult {
  deleted: boolean;
  selector: string;
  file: string;
}

const enc = (value: string) => encodeURIComponent(value);

export async function listRuns(fetchImpl?: typeof fetch): Promise<RunLibrarySummary[]> {
  const data = await requestJson<{ runs?: RunLibrarySummary[] }>('/runs', undefined, fetchImpl);
  return Array.isArray(data?.runs) ? data.runs : [];
}

export async function getRun(runId: string, fetchImpl?: typeof fetch): Promise<RunDetail> {
  const data = await requestJson<{ run: RunDetail }>(`/runs/${enc(runId)}`, undefined, fetchImpl);
  return data.run;
}

export async function publishRun(
  runId: string,
  fetchImpl?: typeof fetch,
): Promise<{ run_id: string; published: string; wrote_manifest: boolean }> {
  return requestJson(`/runs/${enc(runId)}/publish`, { method: 'POST' }, fetchImpl);
}

export async function deleteRun(
  runId: string,
  fetchImpl?: typeof fetch,
): Promise<DeleteRunResult> {
  return requestJson(`/runs/${enc(runId)}`, { method: 'DELETE' }, fetchImpl);
}

export async function deleteStrategy(
  selector: string,
  fetchImpl?: typeof fetch,
): Promise<DeleteStrategyResult> {
  return requestJson(`/strategies/${enc(selector)}`, { method: 'DELETE' }, fetchImpl);
}

/** Absolute URL of the bridge-served standalone report for a run. */
export function runReportUrl(runId: string): string {
  return `${bridgeBaseUrl()}/runs/${enc(runId)}/report`;
}

export interface ComparisonRun {
  run_id: string;
  created_at: string | null;
  strategy: { name: string; version: string };
  strategy_selector: string | null;
  strategy_file: string | null;
  symbol: string | null;
  timeframe: string | null;
  start: string | null;
  end: string | null;
  parameters: Record<string, unknown>;
  execution: { stop_distance: number | null; take_profit_distance: number | null };
  total_trades: number | null;
  long_trades: number | null;
  short_trades: number | null;
  wins: number | null;
  losses: number | null;
  win_rate: number | null;
  net_pnl: number | null;
  avg_trade: number | null;
  profit_factor: number | null;
  max_drawdown: number | null;
  expectancy: number | null;
  data_quality_status: string;
}

export interface ComparisonResult {
  runs: ComparisonRun[];
  warnings: string[];
  differences: { symbol: boolean; timeframe: boolean; date_range: boolean; strategy: boolean };
  equity: Record<string, number[]>;
  units: string;
}

export async function compareRuns(
  runIds: string[],
  fetchImpl?: typeof fetch,
): Promise<ComparisonResult> {
  return requestJson<ComparisonResult>(
    '/comparisons',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ run_ids: runIds }),
    },
    fetchImpl,
  );
}

export type ObjectiveMetric =
  | 'net_pnl' | 'profit_factor' | 'win_rate' | 'max_drawdown' | 'expectancy' | 'total_trades';

export interface OptimizationPayload {
  strategy: string;
  market: { symbol: string; timeframe?: string; start?: string | null; end?: string | null; data_source: string };
  execution: { stop_distance: number; take_profit_distance: number };
  strategy_grid: Record<string, unknown[]>;
  execution_grid: Record<string, number[]>;
  objective: { metric: ObjectiveMetric; direction: 'maximize' | 'minimize' };
  max_candidates?: number;
}

export interface StudyMetrics {
  total_trades: number;
  wins: number;
  losses: number;
  win_rate: number;
  net_pnl: number;
  avg_trade: number | null;
  profit_factor: number | null;
  max_drawdown: number;
  expectancy: number;
}

export interface StudyCandidate {
  index: number;
  run_id: string;
  parameters: Record<string, unknown>;
  execution: Record<string, number>;
  status: 'ok' | 'failed' | 'pending';
  failure: { code: string; message: string } | null;
  metrics: StudyMetrics | null;
  objective_value: number | null;
}

export interface Study {
  study_id: string;
  created_at: string;
  status: string;
  strategy: { selector: string; name: string; version: string };
  dataset: { symbol: string; timeframe: string; start: string | null; end: string | null; data_source: string };
  base: { parameters: Record<string, unknown>; execution: { stop_distance: number; take_profit_distance: number } };
  strategy_grid: Record<string, unknown[]>;
  execution_grid: Record<string, number[]>;
  objective: { metric: string; direction: string };
  candidate_count: number;
  candidates: StudyCandidate[];
  ranking: string[];
  best_run_id: string | null;
}

export interface StudySummary {
  study_id: string;
  created_at: string;
  status: string;
  strategy: string;
  candidate_count: number;
  objective: { metric?: string; direction?: string };
  best_run_id: string | null;
  best_objective_value: number | null;
}

export async function startOptimization(
  payload: OptimizationPayload,
  fetchImpl?: typeof fetch,
): Promise<Study> {
  const data = await requestJson<{ study: Study }>(
    '/optimizations',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    },
    fetchImpl,
  );
  return data.study;
}

export async function listStudies(fetchImpl?: typeof fetch): Promise<StudySummary[]> {
  const data = await requestJson<{ studies?: StudySummary[] }>('/optimizations', undefined, fetchImpl);
  return Array.isArray(data?.studies) ? data.studies : [];
}

export async function getStudy(studyId: string, fetchImpl?: typeof fetch): Promise<Study> {
  const data = await requestJson<{ study: Study }>(`/optimizations/${enc(studyId)}`, undefined, fetchImpl);
  return data.study;
}

export interface WalkForwardWindowPayload {
  train_days: number;
  test_days: number;
  step_days: number;
  mode: 'rolling' | 'anchored';
}

export interface WalkForwardPayload {
  strategy: string;
  market: { symbol: string; timeframe?: string; start?: string | null; end?: string | null; data_source: string };
  execution: { stop_distance: number; take_profit_distance: number };
  strategy_grid: Record<string, unknown[]>;
  execution_grid: Record<string, number[]>;
  objective: { metric: ObjectiveMetric; direction: 'maximize' | 'minimize' };
  window: WalkForwardWindowPayload;
  max_folds?: number;
  max_candidates?: number;
  max_total_candidates?: number;
}

export interface WalkForwardFoldMetrics {
  total_trades: number;
  wins: number;
  losses: number;
  win_rate: number;
  net_pnl: number;
  gross_profit: number;
  gross_loss: number;
  profit_factor: number | null;
  max_drawdown: number;
  expectancy: number;
}

export interface WalkForwardEquityPoint {
  timestamp: string;
  cumulative_pnl: number;
}

export interface WalkForwardFold {
  index: number;
  train_start: string;
  train_end: string;
  test_start: string;
  test_end: string;
  optimization_study_id: string | null;
  selected_run_id: string | null;
  selected_parameters: Record<string, unknown> | null;
  training_objective_value: number | null;
  test_run_id: string | null;
  test_metrics: WalkForwardFoldMetrics | null;
  equity: WalkForwardEquityPoint[];
  status: 'ok' | 'train_failed' | 'test_failed' | 'pending';
  failure: { code: string; message: string } | null;
}

export interface WalkForwardAggregate {
  fold_count: number;
  completed_folds: number;
  total_oos_trades: number | null;
  oos_wins: number | null;
  oos_losses: number | null;
  oos_win_rate: number | null;
  total_oos_net_pnl: number | null;
  average_fold_pnl: number | null;
  profitable_folds: number | null;
  profitable_fold_pct: number | null;
  worst_fold_pnl: number | null;
  best_fold_pnl: number | null;
  oos_profit_factor: number | null;
}

export interface WalkForwardStudy {
  wf_id: string;
  created_at: string;
  status: 'completed' | 'partial' | 'failed';
  strategy: { selector: string; name: string; version: string };
  dataset: { symbol: string; timeframe: string; start: string; end: string; data_source: string };
  base: { parameters: Record<string, unknown>; execution: { stop_distance: number; take_profit_distance: number } };
  window: WalkForwardWindowPayload;
  objective: { metric: string; direction: string };
  strategy_grid: Record<string, unknown[]>;
  execution_grid: Record<string, number[]>;
  candidates_per_fold: number;
  fold_count: number;
  folds: WalkForwardFold[];
  aggregate: WalkForwardAggregate;
}

export interface WalkForwardSummary {
  wf_id: string;
  created_at: string;
  status: string;
  strategy: string;
  fold_count: number;
  objective: { metric?: string; direction?: string };
  oos_net_pnl: number | null;
  profitable_folds: number | null;
}

export async function startWalkForward(
  payload: WalkForwardPayload,
  fetchImpl?: typeof fetch,
): Promise<WalkForwardStudy> {
  const data = await requestJson<{ walkforward: WalkForwardStudy }>(
    '/walkforwards',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    },
    fetchImpl,
  );
  return data.walkforward;
}

export async function listWalkForwards(fetchImpl?: typeof fetch): Promise<WalkForwardSummary[]> {
  const data = await requestJson<{ walkforwards?: WalkForwardSummary[] }>('/walkforwards', undefined, fetchImpl);
  return Array.isArray(data?.walkforwards) ? data.walkforwards : [];
}

export async function getWalkForward(wfId: string, fetchImpl?: typeof fetch): Promise<WalkForwardStudy> {
  const data = await requestJson<{ walkforward: WalkForwardStudy }>(`/walkforwards/${enc(wfId)}`, undefined, fetchImpl);
  return data.walkforward;
}
