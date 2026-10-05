/**
 * Quant Backtest Lab — canonical result contract.
 *
 * A BacktestRun is the single source of truth the UI reads. It is produced by
 * `loadBacktestRun()` and validated on entry; the UI never reads `trades`,
 * `cumulative` or scattered constants directly.
 *
 * Two families of numbers live side by side and must never be conflated:
 *
 *   - price terms  — PnL expressed as instrument price points (the only thing
 *                    the seeded demo and the price-term engine actually produce).
 *   - account terms — PnL expressed in the account currency, derived from
 *                    contract size, lot sizing, commission, swap, slippage and
 *                    FX conversion.
 *
 * Price-term numbers are always present for closed trades. Account-term
 * numbers are present only when `account.accountReady === true`. Every UI
 * element that would otherwise be read as an account result is Pending when
 * account data is absent, so price terms can never be misread as account money.
 */
import { z } from 'zod';

export const DATA_MODE = ['seeded-demo', 'live'] as const;
export type DataMode = (typeof DATA_MODE)[number];

export const DataQualityStatus = ['pending', 'measured', 'demo'] as const;
export type DataQualityStatus = (typeof DataQualityStatus)[number];

export const TradeSchema = z.object({
  id: z.number().int().positive(),
  direction: z.enum(['LONG', 'SHORT']),
  /** ISO-8601 UTC timestamp the signal became available. */
  signalTimeUtc: z.string().datetime({ offset: false }),
  entryTimeUtc: z.string().datetime({ offset: false }),
  exitTimeUtc: z.string().datetime({ offset: false }),
  /** Price terms only — entry/exit fills and raw PnL. Never account currency. */
  entry: z.number(),
  exit: z.number(),
  sl: z.number().optional(),
  tp: z.number().optional(),
  /** Per-trade price PnL (price terms). Always present for closed trades. */
  pnl: z.number(),
  spreadIn: z.number(),
  spreadOut: z.number(),
  /** Account-side fields. Present only when accountReady. */
  volume: z.number().optional(),
  commission: z.number().optional(),
  swap: z.number().optional(),
  slippage: z.number().optional(),
  /** PnL converted to account currency, if and only if accountReady. */
  pnlCurrency: z.number().optional(),
  reason: z.enum(['TP', 'SL', 'EOD']),
});
export type Trade = z.infer<typeof TradeSchema>;

export const EquityPointSchema = z.object({
  /** ISO-8601 UTC timestamp, e.g. a trade exit. */
  timestampUtc: z.string().datetime({ offset: false }),
  /** Cumulative PnL in price terms. Always present. */
  pnl: z.number(),
  /** Account balance (account currency). Present only when accountReady. */
  balance: z.number().optional(),
  /** Account equity (account currency). Present only when accountReady. */
  equity: z.number().optional(),
  /** Cumulative PnL in account currency. Present only when accountReady. */
  pnlCurrency: z.number().optional(),
});
export type EquityPoint = z.infer<typeof EquityPointSchema>;

export const AccountSchema = z.object({
  /**
   * ISO-4217 currency code when an account model exists, otherwise null.
   * Null here is what makes accountReady false for the demo.
   */
  currency: z.string().nullable(),
  initialBalance: z.number().nullish(),
  leverage: z.string().nullish(),
  contractSize: z.number().nullish(),
  lotSize: z.number().nullish(),
  pipValue: z.number().nullish(),
  /** Human-readable reason the account model is absent / incomplete. */
  note: z.string().nullish(),
  /**
   * True iff currency + per-trade currency PnL + cost model are all available.
   * Derived; the UI uses this as the gate for every account-level metric.
   */
  accountReady: z.boolean(),
});
export type Account = z.infer<typeof AccountSchema>;

export const StrategySchema = z.object({
  name: z.string(),
  version: z.string(),
  /** Snapshot of the exact parameters this run used. */
  parameters: z.record(z.string(), z.unknown()),
});
export type Strategy = z.infer<typeof StrategySchema>;

export const InstrumentSchema = z.object({
  symbol: z.string(),
  timeframe: z.string(),
  timezone: z.string(),
});
export type Instrument = z.infer<typeof InstrumentSchema>;

export const DataSourceSchema = z.object({
  name: z.string(),
  /** Hash/fingerprint of the raw source so a result is traceable. */
  fingerprint: z.string(),
  kind: z.enum(['ticks', 'candles', 'derived']),
  version: z.string(),
});
export type DataSource = z.infer<typeof DataSourceSchema>;

export const DataQualitySchema = z.object({
  status: z.enum(DataQualityStatus),
  /**
   * Demo value only, and only shown when the caller marks it as such.
   * A `demo` status must render as "Not measured" in the UI.
   */
  modelingQuality: z.number().nullish(),
  candles: z.number().int().nonnegative().nullish(),
  totalTicks: z.number().int().nonnegative().nullish(),
  inRangeTicks: z.number().int().nonnegative().nullish(),
  gaps: z.number().int().nonnegative().nullish(),
  duplicates: z.number().int().nonnegative().nullish(),
  malformedRows: z.number().int().nonnegative().nullish(),
  rejectedRows: z.number().int().nonnegative().nullish(),
  /** Fraction of the requested range actually covered by data, 0..1. */
  timeCoveragePct: z.number().nullish(),
  warnings: z.array(z.string()).default([]),
});
export type DataQuality = z.infer<typeof DataQualitySchema>;

export const RunIdSchema = z.string().uuid();
export type RunId = z.infer<typeof RunIdSchema>;

/**
 * Aggregated results. Only price-term fields are guaranteed. Any account-term
 * field is present only when `account.accountReady === true`; otherwise it must
 * be omitted or null so the UI can render a Pending state.
 */
export const MetricsSchema = z.object({
  trades: z.number().int().nonnegative(),
  wins: z.number().int().nonnegative(),
  losses: z.number().int().nonnegative(),
  winRate: z.number().nullish(),
  /** Cumulative price-term PnL. Always present. */
  netPnl: z.number(),
  grossProfit: z.number(),
  grossLoss: z.number(),
  avgPnl: z.number(),
  avgWin: z.number(),
  avgLoss: z.number(),
  payoff: z.number().nullish(),
  profitFactor: z.number().nullish(),
  /** Price terms. Present always (derived from the price PnL series). */
  maxDrawdown: z.number().nullish(),
  /**
   * Account-currency drawdown, relative to peak balance, as a percent.
   * Present only when accountReady.
   */
  maxDrawdownPct: z.number().nullish(),
  maxLosingStreak: z.number().int().nonnegative().nullish(),
  maxWinningStreak: z.number().int().nonnegative().nullish(),
  avgDurationMin: z.number().nullish(),
  avgSpreadIn: z.number().nullish(),
  avgSpreadOut: z.number().nullish(),
  /**
   * Account-level statistics. Present only when accountReady; null until an
   * account-currency return series exists. A null value must render as
   * Pending / "Not available" in the UI.
   */
  returnPct: z.number().nullish(),
  annualizedReturnPct: z.number().nullish(),
  sharpe: z.number().nullish(),
  sortino: z.number().nullish(),
  calmar: z.number().nullish(),
  /**
   * Standardised set of reasons why account-level metrics are not available.
   * Rendered verbatim as the Pending explanation.
   */
  accountUnavailableReasons: z.array(z.string()).optional(),
});
export type Metrics = z.infer<typeof MetricsSchema>;

export const BacktestRunSchema = z.object({
  runId: RunIdSchema,
  runTimestampUtc: z.string().datetime({ offset: false }),
  dataMode: z.enum(DATA_MODE),
  strategy: StrategySchema,
  instrument: InstrumentSchema,
  /** ISO-8601 UTC boundaries, inclusive. */
  dateRange: z.object({ startUtc: z.string().datetime({ offset: false }), endUtc: z.string().datetime({ offset: false }) }),
  dataSource: DataSourceSchema,
  dataQuality: DataQualitySchema,
  account: AccountSchema,
  trades: z.array(TradeSchema),
  /** Ordered by exit time. Always length >= trades.length (one point per exit). */
  equity: z.array(EquityPointSchema),
  metrics: MetricsSchema,
  /** Non-fatal notes surfaced to the user, e.g. "demo data — not measured". */
  warnings: z.array(z.string()).default([]),
  /** Explicit limitations so nothing is overstated. */
  limitations: z.array(z.string()).default([]),
});
export type BacktestRun = z.infer<typeof BacktestRunSchema>;

export type { BacktestRunSchema as Schema };
