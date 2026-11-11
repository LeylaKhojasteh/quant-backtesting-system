'use client';

import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { fmtPnl } from './format';

export const MAE_EXPLANATION =
  'Maximum Adverse Excursion is not supplied by this archive (legacy run). New runs measure it tick by tick.';
export const MFE_EXPLANATION =
  'Maximum Favorable Excursion is not supplied by this archive (legacy run). New runs measure it tick by tick.';
const MAE_DEFINITION =
  'Maximum Adverse Excursion: worst unrealized movement against the position (negative, price terms).';
const MFE_DEFINITION =
  'Maximum Favorable Excursion: best unrealized movement in favor of the position (positive, price terms).';

/**
 * MAE/MFE value with a hover/focus explanation. When the run does not provide
 * intratrade excursion data the value stays N/A and explains why; an available
 * value explains its meaning. It never invents a number.
 */
export function ExcursionValue({
  kind,
  value,
  digits,
}: {
  kind: 'MAE' | 'MFE';
  value: number | null | undefined;
  digits?: number | null;
}) {
  const unavailable = value === null || value === undefined;
  const text = unavailable ? `${kind} N/A` : `${kind} ${fmtPnl(value, digits)}`;
  const help = unavailable
    ? kind === 'MAE'
      ? MAE_EXPLANATION
      : MFE_EXPLANATION
    : kind === 'MAE'
      ? MAE_DEFINITION
      : MFE_DEFINITION;
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className="excursion-value" tabIndex={0} aria-label={help}>
          {text}
        </span>
      </TooltipTrigger>
      <TooltipContent className="max-w-80">{help}</TooltipContent>
    </Tooltip>
  );
}
