'use client';

import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import { useRun } from './run-context';
import { readinessItems, type ReadinessTone } from './readiness';

const TONE_STYLE: Record<ReadinessTone, { color: string; dot: string }> = {
  ready: { color: 'var(--success)', dot: 'var(--success)' },
  pending: { color: 'var(--warning)', dot: 'var(--warning)' },
  unavailable: { color: 'var(--muted-foreground)', dot: 'var(--muted-foreground)' },
  error: { color: 'var(--danger)', dot: 'var(--danger)' },
};

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
                    <span
                      className="readiness-chip"
                      tabIndex={0}
                      aria-label={`${item.label}: ${item.status}. ${item.readyHint}`}
                    >
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
