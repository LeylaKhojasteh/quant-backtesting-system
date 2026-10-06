'use client';

import { Lock, Clock, AlertTriangle } from 'lucide-react';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';

/**
 * Standardised card for every metric that does not have a real value yet.
 *
 * Replaces scattered "Pending" / "Not available" text so the UI always says
 * *why* a number is missing and *what* it would need. No zero, no placeholder
 * number and no fabricated currency unit is ever shown.
 *
 * `reason` comes from `metrics.accountUnavailableReasons` or the run metadata,
 * never invented by the component.
 */
export type PendingMetricProps = {
  label: string;
  reason: string | string[];
  required?: string[];
  compact?: boolean;
  className?: string;
  /** Override the status label; defaults to "Pending". */
  status?: 'Pending' | 'Not available';
  /** Short note rendered under the label in full mode. */
  note?: string;
  /** Hide the repeated label (for table cells that already show it). Defaults to true. */
  showLabel?: boolean;
};

const STATUS_CONFIG: Record<
  'Pending' | 'Not available',
  { icon: typeof Clock; color: string; label: string; title: string }
> = {
  Pending: { icon: Clock, color: 'var(--warning)', label: 'Pending', title: 'Value pending — not yet computed' },
  'Not available': {
    icon: Lock,
    color: 'var(--muted-foreground)',
    label: 'Not available',
    title: 'Not available — the run does not supply this value',
  },
};

function ReasonList({ reasons }: { reasons: string[] }) {
  if (reasons.length <= 1) return <p className="pm-reason">{reasons[0]}</p>;
  return (
    <ul className="pm-reason-list">
      {reasons.map((r, i) => (
        <li key={i}>{r}</li>
      ))}
    </ul>
  );
}

export function PendingMetric({
  label,
  reason,
  required,
  compact = false,
  className = '',
  status = 'Pending',
  note,
  showLabel = true,
}: PendingMetricProps) {
  const cfg = STATUS_CONFIG[status];
  const Icon = cfg.icon;
  const reasons = Array.isArray(reason) ? reason : [reason];
  const hasRequired = Array.isArray(required) && required.length > 0;

  if (compact) {
    return (
      <TooltipProvider delayDuration={180}>
        <div className={`pm pm-compact ${className}`.trim()} role="status" aria-label={`${label}: ${cfg.label}`}>
          <span className="pm-compact-icon" style={{ color: cfg.color }} aria-hidden>
            <Icon size={12} />
          </span>
          {showLabel && <span className="pm-compact-label">{label}</span>}
          <Tooltip>
            <TooltipTrigger asChild>
              <span className="pm-compact-status" style={{ color: cfg.color }}>
                {cfg.label}
              </span>
            </TooltipTrigger>
            <TooltipContent className="max-w-72">
              <strong>{label}</strong>
              <ReasonList reasons={reasons} />
              {hasRequired && (
                <>
                  <span className="pm-required-title">Required for calculation</span>
                  <ul className="pm-required-list">
                    {required!.map((r, i) => (
                      <li key={i}>{r}</li>
                    ))}
                  </ul>
                </>
              )}
            </TooltipContent>
          </Tooltip>
        </div>
      </TooltipProvider>
    );
  }

  return (
    <TooltipProvider delayDuration={180}>
      <div className={`pm ${className}`.trim()} role="status" aria-label={`${label}: ${cfg.label}`}>
        <div className="pm-head">
          <span className="pm-icon" style={{ color: cfg.color }} aria-hidden>
            <Icon size={15} />
          </span>
          <div className="pm-title">
            <span className="pm-label">{label}</span>
            <Tooltip>
              <TooltipTrigger asChild>
                <span className="pm-status" style={{ color: cfg.color }}>
                  {cfg.label}
                </span>
              </TooltipTrigger>
              <TooltipContent className="max-w-72">
                <strong>{cfg.title}</strong>
                <ReasonList reasons={reasons} />
                {hasRequired && (
                  <>
                    <span className="pm-required-title">Required for calculation</span>
                    <ul className="pm-required-list">
                      {required!.map((r, i) => (
                        <li key={i}>{r}</li>
                      ))}
                    </ul>
                  </>
                )}
              </TooltipContent>
            </Tooltip>
          </div>
        </div>
        <ReasonList reasons={reasons} />
        {note && <p className="pm-note">{note}</p>}
        {hasRequired && (
          <div className="pm-required">
            <span className="pm-required-title">
              <AlertTriangle size={11} aria-hidden /> Required for calculation
            </span>
            <ul className="pm-required-list">
              {required!.map((r, i) => (
                <li key={i}>{r}</li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </TooltipProvider>
  );
}

/** Shared reasons for every account-level metric on a price-term run. */
export function accountPendingReasons(run: { metrics?: { accountUnavailableReasons?: string[] } }): string[] {
  return run?.metrics?.accountUnavailableReasons?.length
    ? run.metrics.accountUnavailableReasons
    : ['No account model configured.'];
}