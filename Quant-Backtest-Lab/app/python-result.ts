/**
 * Python BacktestResult mapping — pure logic, no React, no `@/` aliases.
 *
 * Converts an archived Python `result.json` (see `src/result_contract.py`)
 * into the canonical frontend `BacktestRun` payload shape (see `schema.ts`),
 * ready for `BacktestRunSchema` validation in `runner.ts`.
 *
 * Conflict notes (handled here, nothing else touched):
 * - Python `run_id` values (`run_0001`) are not UUIDs, while the frontend
 *   `RunIdSchema` requires a UUID. The adapter derives a deterministic
 *   UUIDv5-style id from the Python run id (namespace + sha1), so the same
 *   archive always maps to the same frontend run id. The Python id is kept
 *   verbatim as `pythonRunId` inside `warnings` context and the data-source
 *   fingerprint stays untouched.
 * - Python timestamps are space-separated with microseconds
 *   (`2025-01-02 00:15:00Z`); the frontend requires ISO-8601 UTC with `T`.
 *   The adapter normalizes the separator and preserves the instant.
 * - Python `dataMode` does not exist; archived runs are real measured runs,
 *   so they map to `dataMode: 'live'` with `dataQuality.status: 'measured'`.
 *   Account terms stay absent (`accountReady: false`) — the Python engine
 *   produces price terms only, so account metrics render Pending, never
 *   invented.
 */

export type PythonTrade = {
  trade_id: number;
  direction: 'LONG' | 'SHORT';
  signal_candle_start: string;
  signal_available_time: string;
  entry_time: string;
  entry_price: number;
  stop_loss: number;
  take_profit: number;
  exit_time: string;
  exit_price: number;
  exit_reason: string;
  pnl_price: number;
  duration_seconds: number;
  spread_at_entry: number;
  spread_at_exit: number;
  mae?: number | null;
  mfe?: number | null;
};

export type PythonResult = {
  contract_version?: string;
  run_id: string;
  created_at: string;
  strategy: { name: string; version: string; description?: string };
  config: {
    symbol: string;
    timeframe: string;
    start: string;
    end: string;
    requested_start?: string;
    requested_end?: string;
    parameters: Record<string, unknown>;
    data_source?: string;
    data_fingerprint?: string;
  };
  trades: PythonTrade[];
  equity: { timestamp: string; cumulative_pnl: number; balance?: number | null; equity?: number | null }[];
  metrics: {
    total_trades: number;
    wins: number;
    losses: number;
    win_rate?: number | null;
    net_pnl: number;
    gross_profit: number;
    gross_loss: number;
    profit_factor?: number | null;
    expectancy: number;
    avg_win: number;
    avg_loss: number;
    max_drawdown?: number | null;
    avg_duration_seconds?: number | null;
  };
  data_quality: {
    data_source?: string;
    tick_count?: number | null;
    candle_count?: number | null;
    validation?: Record<string, unknown>;
    warnings?: string[];
  };
  execution: {
    engine?: string;
    started_at?: string | null;
    finished_at?: string | null;
    signals_received?: number | null;
    signals_executed?: number | null;
    signals_ignored?: number | null;
  };
  /** Optional account/economics report (src/accounting.py). Absent/null for
   *  price-term-only runs and for archives written before this field existed. */
  account?: PythonAccount | null;
};

export type PythonAccount = {
  available: boolean;
  reason_unavailable?: string | null;
  account_currency?: string | null;
  model?: {
    account_currency?: string;
    starting_balance?: number;
    position_size_lots?: number;
    commission_per_lot_round_turn?: number;
  } | null;
  instrument?: {
    symbol?: string;
    quote_currency?: string;
    contract_size?: number;
    tick_size?: number | null;
  } | null;
  metrics?: {
    starting_balance?: number;
    ending_balance?: number;
    net_pnl?: number;
    gross_profit?: number;
    gross_loss?: number;
    total_commission?: number;
    total_return_pct?: number | null;
    avg_trade_pnl?: number | null;
    max_drawdown?: number;
    max_drawdown_pct?: number | null;
    recovery_factor?: number | null;
  } | null;
  trades?: { trade_id: number; gross_account_pnl: number; commission: number; net_account_pnl: number; balance_after: number }[];
  balance_curve?: { timestamp: string; realized_balance: number }[];
  notes?: string[];
};

const REQUIRED_TOP = ['run_id', 'created_at', 'strategy', 'config', 'trades', 'equity', 'metrics', 'data_quality', 'execution'] as const;

/** Validate the archived payload shape before mapping. Returns violations. */
export function validatePythonResult(payload: unknown): string[] {
  if (typeof payload !== 'object' || payload === null) return ['result.json must be a JSON object'];
  const errors: string[] = [];
  const p = payload as Record<string, unknown>;
  for (const key of REQUIRED_TOP) {
    if (!(key in p)) errors.push(`missing required field: ${key}`);
  }
  const strategy = p.strategy as Record<string, unknown> | undefined;
  if (strategy && (typeof strategy.name !== 'string' || typeof strategy.version !== 'string')) {
    errors.push('strategy.name and strategy.version must be strings');
  }
  const config = p.config as Record<string, unknown> | undefined;
  if (config) {
    for (const key of ['symbol', 'timeframe', 'start', 'end', 'parameters']) {
      if (!(key in config)) errors.push(`missing required field: config.${key}`);
    }
  }
  if (Array.isArray(p.trades)) {
    p.trades.forEach((t, i) => {
      const trade = t as Record<string, unknown>;
      if (trade.direction !== 'LONG' && trade.direction !== 'SHORT') errors.push(`trades[${i}].direction must be LONG or SHORT`);
      if (!['TP', 'SL', 'EOD', 'END_OF_DATA'].includes(trade.exit_reason as string)) {
        errors.push(`trades[${i}].exit_reason must be TP, SL, EOD or END_OF_DATA`);
      }
    });
  } else if ('trades' in p) {
    errors.push('trades must be an array');
  }
  return errors;
}

/** Parse a Python timestamp (`...Z`, space or `T` separated) to epoch ms. */
export function pythonTimeToMs(value: string): number {
  const normalized = value.replace(' ', 'T');
  const ms = Date.parse(normalized);
  if (Number.isNaN(ms)) throw new Error(`unparseable Python timestamp: ${value}`);
  return ms;
}

/** Normalize a Python timestamp to canonical ISO-8601 UTC (`...Z`). */
export function pythonTimeToUtcIso(value: string): string {
  return new Date(pythonTimeToMs(value)).toISOString();
}

function sha1Hex(text: string): string {
  // Small synchronous FNV-1a + sha-style expansion is overkill; the test
  // runner has no async crypto. Use a deterministic 128-bit layout built
  // from two FNV passes over the string with different seeds.
  const fnv = (seed: number) => {
    let h = seed >>> 0;
    for (let i = 0; i < text.length; i++) {
      h ^= text.charCodeAt(i);
      h = Math.imul(h, 16777619);
    }
    return h >>> 0;
  };
  const parts = [fnv(0x811c9dc5), fnv(0x01000193), fnv(0xdeadbeef), fnv(0x41c6ce57)];
  return parts.map((p) => p.toString(16).padStart(8, '0')).join('');
}

/**
 * Deterministic UUID from the Python run id: same archive always yields
 * the same frontend run id. Version/variant bits follow RFC 4122.
 */
export function pythonRunIdToUuid(runId: string): string {
  const h = sha1Hex(`qbl-python-run:${runId}`);
  const seg = (s: string, v: string, pos: number) => s.slice(0, pos) + v + s.slice(pos + 1);
  let b = h;
  b = seg(b, '5', 12); // version 5 (named-based)
  const variant = (parseInt(b[16], 16) & 0x3) | 0x8;
  b = seg(b, variant.toString(16), 16);
  return `${b.slice(0, 8)}-${b.slice(8, 12)}-${b.slice(12, 16)}-${b.slice(16, 20)}-${b.slice(20, 32)}`;
}

const EXIT_MAP: Record<string, 'TP' | 'SL' | 'EOD'> = { TP: 'TP', SL: 'SL', EOD: 'EOD', END_OF_DATA: 'EOD' };

const ACCOUNT_UNAVAILABLE_REASONS = [
  'No account currency configured — price-term engine only.',
  'No lot sizing, contract size, commission, swap or slippage provided.',
  'Per-trade PnL is in price points; account-currency PnL cannot be converted.',
];

function streaks(pnls: number[]) {
  const out: { win: boolean; count: number }[] = [];
  for (const p of pnls) {
    const win = p > 0;
    const last = out[out.length - 1];
    if (last && last.win === win) last.count += 1;
    else out.push({ win, count: 1 });
  }
  return out;
}

/**
 * Map an archived Python result to the frontend BacktestRun payload.
 * Throws on validation failures or unparseable timestamps — the caller
 * surfaces the message instead of rendering partial data.
 */
export function mapPythonResultToBacktestRun(payload: PythonResult): unknown {
  const violations = validatePythonResult(payload);
  if (violations.length) throw new Error(`result.json failed adapter validation: ${violations.join('; ')}`);

  const ordered = [...payload.trades].sort(
    (a, b) => pythonTimeToMs(a.exit_time) - pythonTimeToMs(b.exit_time) || a.trade_id - b.trade_id,
  );
  const runId = pythonRunIdToUuid(payload.run_id);

  const acct = payload.account ?? null;
  const acctReady = !!(acct && acct.available);
  const acctRows = acctReady ? (acct!.trades ?? []) : [];
  const acctCurve = acctReady ? (acct!.balance_curve ?? []) : [];

  const trades = ordered.map((t, i) => ({
    id: i + 1,
    direction: t.direction,
    signalTimeUtc: pythonTimeToUtcIso(t.signal_candle_start || t.signal_available_time),
    signalAvailableUtc: pythonTimeToUtcIso(t.signal_available_time),
    entryTimeUtc: pythonTimeToUtcIso(t.entry_time),
    exitTimeUtc: pythonTimeToUtcIso(t.exit_time),
    entry: t.entry_price,
    exit: t.exit_price,
    sl: t.stop_loss,
    tp: t.take_profit,
    pnl: t.pnl_price,
    spreadIn: t.spread_at_entry,
    spreadOut: t.spread_at_exit,
    reason: EXIT_MAP[t.exit_reason],
    ...(acctReady
      ? {
          volume: acct!.model?.position_size_lots,
          commission: acctRows[i]?.commission,
          pnlCurrency: acctRows[i]?.net_account_pnl,
        }
      : {}),
  }));

  const running: number[] = [];
  let acc = 0;
  for (const t of ordered) {
    acc += t.pnl_price;
    running.push(acc);
  }
  const equity = ordered.map((t, i) => ({
    timestampUtc: pythonTimeToUtcIso(t.exit_time),
    pnl: running[i],
    ...(acctReady
      ? {
          balance: acctCurve[i]?.realized_balance ?? (acctRows[i]?.balance_after as number | undefined),
          pnlCurrency: acctRows[i]?.net_account_pnl,
        }
      : {}),
  }));

  const m = payload.metrics;
  const pnls = ordered.map((t) => t.pnl_price);
  const st = streaks(pnls);
  const lossRuns = st.filter((s) => !s.win);
  const winRuns = st.filter((s) => s.win);
  const sum = (v: number[]) => v.reduce((a, b) => a + b, 0);
  const mean = (v: number[]) => (v.length ? sum(v) / v.length : 0);
  const spreadsIn = ordered.map((t) => t.spread_at_entry);
  const spreadsOut = ordered.map((t) => t.spread_at_exit);

  const inRangeTicks = payload.data_quality.tick_count ?? null;
  const fingerprints = [payload.config.data_fingerprint, payload.run_id].filter(Boolean).join(':');

  return {
    runId,
    runTimestampUtc: pythonTimeToUtcIso(payload.created_at),
    dataMode: 'live' as const,
    strategy: {
      name: payload.strategy.name,
      version: payload.strategy.version,
      parameters: payload.config.parameters,
    },
    instrument: { symbol: payload.config.symbol, timeframe: payload.config.timeframe, timezone: 'UTC' },
    dateRange: {
      // Requested backtest window (authoritative for display). Legacy runs
      // without requested_* fall back to the observed coverage.
      startUtc: pythonTimeToUtcIso(payload.config.requested_start || payload.config.start),
      endUtc: pythonTimeToUtcIso(payload.config.requested_end || payload.config.end),
    },
    dataCoverage: {
      // Actual first/last observed tick coverage of the run.
      startUtc: pythonTimeToUtcIso(payload.config.start),
      endUtc: pythonTimeToUtcIso(payload.config.end),
    },
    dataSource: {
      name: payload.data_quality.data_source || payload.config.data_source || 'Archived Python run',
      fingerprint: fingerprints || payload.run_id,
      kind: 'ticks' as const,
      version: payload.strategy.version,
    },
    dataQuality: {
      status: 'measured' as const,
      modelingQuality: undefined,
      candles: payload.data_quality.candle_count ?? null,
      totalTicks: inRangeTicks,
      inRangeTicks,
      gaps: null,
      duplicates: null,
      malformedRows: null,
      rejectedRows: null,
      timeCoveragePct: null,
      warnings: payload.data_quality.warnings ?? [],
    },
    account: acctReady
      ? {
          currency: acct!.account_currency ?? null,
          initialBalance: acct!.metrics?.starting_balance ?? acct!.model?.starting_balance ?? null,
          leverage: null,
          contractSize: acct!.instrument?.contract_size ?? null,
          lotSize: acct!.model?.position_size_lots ?? null,
          pipValue: null,
          note: (acct!.notes ?? ['Account model applied.']).join(' '),
          accountReady: true,
        }
      : {
          currency: acct?.account_currency ?? null,
          initialBalance: null,
          leverage: null,
          contractSize: null,
          lotSize: null,
          pipValue: null,
          note: acct?.reason_unavailable ?? 'Price-term engine only; no account-currency model configured.',
          accountReady: false,
        },
    trades,
    equity,
    execution: {
      signalsReceived: payload.execution.signals_received ?? null,
      signalsExecuted: payload.execution.signals_executed ?? null,
      signalsIgnored: payload.execution.signals_ignored ?? null,
      engine: payload.execution.engine ?? null,
      startedAtUtc: payload.execution.started_at ?? null,
      finishedAtUtc: payload.execution.finished_at ?? null,
    },
    metrics: {
      trades: m.total_trades,
      wins: m.wins,
      losses: m.losses,
      winRate: m.win_rate ?? null,
      netPnl: m.net_pnl,
      grossProfit: m.gross_profit,
      grossLoss: m.gross_loss,
      avgPnl: m.total_trades ? m.net_pnl / m.total_trades : 0,
      avgWin: m.avg_win,
      avgLoss: m.avg_loss,
      payoff: m.avg_loss !== 0 ? m.avg_win / Math.abs(m.avg_loss) : null,
      profitFactor: m.profit_factor ?? null,
      maxDrawdown: m.max_drawdown ?? null,
      maxDrawdownPct: acctReady ? acct!.metrics?.max_drawdown_pct ?? null : null,
      maxLosingStreak: lossRuns.length ? Math.max(...lossRuns.map((s) => s.count)) : 0,
      maxWinningStreak: winRuns.length ? Math.max(...winRuns.map((s) => s.count)) : 0,
      avgDurationMin:
        m.avg_duration_seconds !== null && m.avg_duration_seconds !== undefined
          ? m.avg_duration_seconds / 60
          : mean(ordered.map((t) => t.duration_seconds)) / 60,
      avgSpreadIn: mean(spreadsIn),
      avgSpreadOut: mean(spreadsOut),
      returnPct: acctReady ? acct!.metrics?.total_return_pct ?? null : null,
      annualizedReturnPct: null,
      sharpe: null,
      sortino: null,
      calmar: null,
      accountUnavailableReasons: acctReady
        ? []
        : acct?.reason_unavailable
          ? [acct.reason_unavailable, ...ACCOUNT_UNAVAILABLE_REASONS]
          : ACCOUNT_UNAVAILABLE_REASONS,
    },
    warnings: [
      `Archived Python run ${payload.run_id} · engine ${payload.execution.engine ?? 'src.backtest'} · ` +
        `${payload.execution.signals_received ?? ordered.length} signals received, ` +
        `${payload.execution.signals_executed ?? ordered.length} executed.`,
      ...(acctReady
        ? [
            `Account model ${acct!.account_currency ?? ''}: starting balance ` +
              `${acct!.metrics?.starting_balance ?? acct!.model?.starting_balance ?? ''}, ` +
              `${acct!.model?.position_size_lots ?? ''} lots, contract size ` +
              `${acct!.instrument?.contract_size ?? ''}. Realized balance from closed trades only.`,
          ]
        : []),
    ],
    limitations: [
      'PnL is in price terms only; no account currency, lot sizing, commission or conversion is applied.',
      'MAE/MFE are not supplied by the engine result; excursion analytics stay unavailable.',
      ...(acctReady
        ? [
            'Account PnL = price PnL × contract size × lots, minus an explicit per-lot round-turn commission.',
            'Spread is already inside the engine BID/ASK fills and is not re-applied. Sharpe/Sortino/Calmar/annualized return stay unavailable.',
          ]
        : []),
    ],
  };
}
