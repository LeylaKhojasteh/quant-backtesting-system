'use client';

import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import { useRun } from './run-context';
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

const TONE_STYLE: Record<ReadinessTone, { color: string; dot: string }> = {
  ready: { color: 'var(--success)', dot: 'var(--success)' },
  pending: { color: 'var(--warning)', dot: 'var(--warning)' },
  unavailable: { color: 'var(--muted-foreground)', dot: 'var(--muted-foreground)' },
  error: { color: 'var(--danger)', dot: 'var(--danger)' },
};

/**
 * Pure derivation of the five readiness steps from a BacktestRun.
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
        status: 'Live',
        tone: 'ready',
        detail: run.dataSource.name,
        readyHint: 'Live data adapter connected.',
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
        label: 'Engine connection',
        status: 'Connected',
        tone: 'ready',
        detail: 'Python backtest engine supplied this run.',
        readyHint: 'Engine adapter is wired.',
      }
    : {
        key: 'engine',
        label: 'Engine connection',
        status: 'Not connected',
        tone: 'unavailable',
        detail: 'Python engine not wired; seeded reference replay.',
        readyHint: 'Wire the Python backtest engine adapter to execute real backtests.',
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
    : {
        key: 'reliability',
        label: 'Result reliability',
        status: 'Visual preview only',
        tone: 'pending',
        detail: 'Seeded demo replay — not a live or backtested-with-data result.',
        readyHint: 'Requires live data, measured validation and a configured account model.',
      };

  return [source, validation, engine, account, reliability];
}

/**
 * Compact readiness strip. Reads the single BacktestRun via useRun();
 * no scattered constants. Responsive via flex-wrap; meaning is carried
 * by text labels, never by colour alone.
 */
export function DataReadinessBar({ className = '' }: { className?: string }) {
  const state = useRun();
  if (state.status === 'error' || state.status === 'malformed') {
    const message = state.status === 'error' ? state.error : state.message;
    return (
      <section className={`readiness-bar ${className}`.trim()} aria-label="Data readiness" role="status">
        <span className="readiness-title">Data readiness</span>
        <span className="readiness-error" style={{ color: TONE_STYLE.error.color }}>
          Load error — {message}
        </span>
      </section>
    );
  }
  const items = readinessItems(state.status === 'ready' ? state.run : null);
  return (
    <TooltipProvider delayDuration={180}>
      <section className={`readiness-bar ${className}`.trim()} aria-label="Data readiness">
        <span className="readiness-title">Data readiness</span>
        <ul className="readiness-items">
          {items.map((item) => {
            const tone = TONE_STYLE[item.tone];
            return (
              <li key={item.key} className={`readiness-item tone-${item.tone}`}>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <span className="readiness-chip" tabIndex={0} aria-label={`${item.label}: ${item.status}. ${item.readyHint}`}>
                      <span className="readiness-dot" style={{ background: tone.dot }} aria-hidden />
                      <span className="readiness-label">{item.label}</span>
                      <span className="readiness-status" style={{ color: tone.color }}>
                        {item.status}
                      </span>
                    </span>
                  </TooltipTrigger>
                  <TooltipContent className="max-w-72">
                    <strong>
                      {item.label} — {item.status}
                    </strong>
                    <p>{item.detail}</p>
                    <p className="meta">To become ready: {item.readyHint}</p>
                  </TooltipContent>
                </Tooltip>
              </li>
            );
          })}
        </ul>
      </section>
    </TooltipProvider>
  );
}
