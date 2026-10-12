/**
 * Data-readiness derivation — pure logic, no React.
 *
 * Kept in an alias-free `.ts` module (no `@/` imports) so the Node test
 * runner can bundle and execute it. `data-readiness.tsx` renders these items.
 */
import type { BacktestRun } from './schema';

export type ReadinessTone = 'ready' | 'pending' | 'unavailable' | 'error';

export type ReadinessItem = {
  key: string;
  label: string;
  status: string;
  tone: ReadinessTone;
  detail: string;
  readyHint: string;
};

/**
 * Derives the four readiness steps from a BacktestRun.
 * The demo run (dataMode seeded-demo, dataQuality demo, accountReady false)
 * never yields a `ready` tone and never uses the word `Verified`.
 * A future live run (dataMode live, dataQuality measured, accountReady true)
 * yields `ready` for the steps it satisfies.
 *
 * Price-term runs report `Account: Off` (neutral) with a Validated run
 * result — a missing Account Model never implies unreliability.
 */
export function readinessItems(run: BacktestRun | null | undefined): ReadinessItem[] {
  if (!run) {
    const loading = (key: string, label: string): ReadinessItem => ({
      key,
      label,
      status: 'Loading…',
      tone: 'pending',
      detail: 'Reference run is loading.',
      readyHint: 'Wait for the run to load.',
    });
    return [
      loading('source', 'Data source'),
      loading('validation', 'Validation'),
      loading('engine', 'Run result'),
      loading('account', 'Account'),
    ];
  }

  const isLive = run.dataMode === 'live';
  const measured = run.dataQuality.status === 'measured';
  const accountReady = run.account.accountReady === true;

  const source: ReadinessItem = isLive
    ? {
        key: 'source',
        label: 'Data source',
        status: 'Archived dataset',
        tone: 'ready',
        detail: run.dataSource.name,
        readyHint: 'Dataset resolved from an archived real run.',
      }
    : {
        key: 'source',
        label: 'Data source',
        status: 'Seeded demo',
        tone: 'unavailable',
        detail: run.dataSource.name,
        readyHint: 'Connect a real data adapter (tick/candle file or live feed) to leave demo mode.',
      };

  const validation: ReadinessItem = measured
    ? {
        key: 'validation',
        label: 'Data validation',
        status: 'Measured',
        tone: 'ready',
        detail: 'Gaps, duplicates, coverage and modeling quality measured.',
        readyHint: 'Validation engine output is attached to this run.',
      }
    : run.dataQuality.status === 'pending'
      ? {
          key: 'validation',
          label: 'Data validation',
          status: 'Pending',
          tone: 'pending',
          detail: 'Validation has not run yet.',
          readyHint: 'Attach the data-validation engine (gaps, duplicates, coverage, modeling quality).',
        }
      : {
          key: 'validation',
          label: 'Data validation',
          status: 'Not measured',
          tone: 'pending',
          detail: 'Seeded example — no validation engine attached.',
          readyHint: 'Attach the data-validation engine (gaps, duplicates, coverage, modeling quality).',
        };

  const engine: ReadinessItem = isLive
    ? {
        key: 'engine',
        label: 'Run result',
        status: 'Validated',
        tone: 'ready',
        detail: 'Produced by the verified Python tick engine (archived result).',
        readyHint: 'Archived result validated against the engine contract.',
      }
    : {
        key: 'engine',
        label: 'Run result',
        status: 'Not available',
        tone: 'unavailable',
        detail: 'No archived engine result.',
        readyHint: 'Run a real backtest to produce a validated result.',
      };

  // A missing Account Model never makes a price-term run unreliable: price
  // terms are the validated result. "Off" is a neutral mode, not a warning.
  const account: ReadinessItem = accountReady
    ? {
        key: 'account',
        label: 'Account',
        status: 'Ready',
        tone: 'ready',
        detail: `Account model applied${run.account.currency ? ` · ${run.account.currency}` : ''}.`,
        readyHint: 'Account model is configured.',
      }
    : isLive
      ? {
          key: 'account',
          label: 'Account',
          status: 'Off',
          tone: 'unavailable',
          detail: 'Price-term results are available. Enable Account Model for account-currency PnL, balance and return.',
          readyHint: 'Price-term results are available. Enable Account Model for account-currency PnL, balance and return.',
        }
      : {
          key: 'account',
          label: 'Account',
          status: 'Not available',
          tone: 'unavailable',
          detail: run.account.note ?? 'No account-currency model configured.',
          readyHint: 'Run a real backtest to produce a validated result.',
        };

  return [source, validation, engine, account];
}
