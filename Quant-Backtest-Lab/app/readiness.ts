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
 * Derives the five readiness steps from a BacktestRun.
 * The demo run (dataMode seeded-demo, dataQuality demo, accountReady false)
 * never yields a `ready` tone and never uses the word `Verified`.
 * A future live run (dataMode live, dataQuality measured, accountReady true)
 * yields `ready` for the steps it satisfies.
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
      loading('validation', 'Data validation'),
      loading('engine', 'Engine connection'),
      loading('account', 'Account model'),
      loading('reliability', 'Result reliability'),
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

  const account: ReadinessItem = accountReady
    ? {
        key: 'account',
        label: 'Account model',
        status: `Ready${run.account.currency ? ` · ${run.account.currency}` : ''}`,
        tone: 'ready',
        detail: 'Account currency, sizing and cost model configured.',
        readyHint: 'Account model is configured.',
      }
    : {
        key: 'account',
        label: 'Account model',
        status: 'Not available',
        tone: 'unavailable',
        detail: run.account.note ?? 'No account-currency model configured.',
        readyHint: 'Configure account currency, lot sizing, contract size, commission, swap and slippage.',
      };

  const reliable = isLive && measured && accountReady;
  const reliability: ReadinessItem = reliable
    ? {
        key: 'reliability',
        label: 'Result reliability',
        status: 'Ready for review',
        tone: 'ready',
        detail: 'Live data, measured quality and account model present.',
        readyHint: 'All readiness steps are satisfied.',
      }
    : isLive
      ? {
          key: 'reliability',
          label: 'Result reliability',
          status: 'Account model missing',
          tone: 'pending',
          detail: 'Backtested data with measured validation, but no account model — account-currency metrics stay unavailable.',
          readyHint: 'Configure account currency, lot sizing, contract size, commission, swap and slippage to complete result reliability.',
        }
      : {
          key: 'reliability',
          label: 'Result reliability',
          status: 'Not a real run',
          tone: 'pending',
          detail: 'Seeded demo fixture — not a live or backtested-with-data result.',
          readyHint: 'Requires a real archived run with a configured account model.',
        };

  return [source, validation, engine, account, reliability];
}
