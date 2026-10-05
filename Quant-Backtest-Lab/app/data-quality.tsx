'use client';

import { Check, Info } from 'lucide-react';
import { Progress } from '@/components/ui/progress';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';

/**
 * Dataset quality summary.
 * Modeling Quality stays labelled Demo / Preview because the production
 * definition will come from the real validation engine.
 */
export function DataQualityPanel() {
  const fields: [string, string][] = [
    ['Dataset', 'XAUUSD_DUKAS_2025_01_ticks.csv'],
    ['Total Ticks', '~8.4 M'],
    ['Ticks in Range', '322,548'],
    ['Candles', '88'],
    ['Signals', '88'],
    ['Executed Trades', '7'],
  ];

  return (
    <TooltipProvider delayDuration={200}>
      <section className="data-quality">
        <dl className="data-quality-grid">
          {fields.map(([k, v]) => (
            <div key={k}>
              <dt>{k}</dt>
              <dd>{v}</dd>
            </div>
          ))}
        </dl>

        <div className="quality quality-prominent">
          <div>
            <span>
              Modeling Quality
              <Tooltip>
                <TooltipTrigger asChild>
                  <button className="help" aria-label="About Modeling Quality">
                    <Info size={12} />
                  </button>
                </TooltipTrigger>
                <TooltipContent className="max-w-80">
                  99.12% is demo data. The production definition will be supplied by the real data-validation
                  engine.
                </TooltipContent>
              </Tooltip>
            </span>
            <b>99.12%</b>
          </div>
          <Progress value={99.12} />
        </div>

        <div className="baseline">
          <Check size={17} />
          <div>
            <strong>Baseline Match</strong>
            <span>Verified reference <span className="meta">· demo</span></span>
          </div>
        </div>
      </section>
    </TooltipProvider>
  );
}