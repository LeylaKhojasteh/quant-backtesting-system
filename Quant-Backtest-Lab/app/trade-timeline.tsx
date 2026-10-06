'use client';

import { useState } from 'react';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import { fmt } from './demo';
import { tradeTimelineEvents, type TimelineTone, type TimelineTrade } from './timeline-events';

const TONE_STYLE: Record<TimelineTone, { color: string; label: string }> = {
  signal: { color: 'var(--warning)', label: 'Signal' },
  entry: { color: 'var(--primary)', label: 'Entry' },
  tp: { color: 'var(--success)', label: 'Take profit' },
  sl: { color: 'var(--danger)', label: 'Stop loss' },
  eod: { color: 'var(--info)', label: 'End of data' },
  neutral: { color: 'var(--muted-foreground)', label: 'Info' },
};

/**
 * Visual execution lifecycle for one trade, built only from that trade's own
 * data (BacktestRun trade or demo adapter record). Missing SL/TP renders as
 * "Not supplied by run" — never invented. Timestamps are UTC ISO instants
 * rendered with a UTC tag; LONG/SHORT and TP/SL/EOD are text labels, never
 * colour alone. Events are keyboard-focusable buttons with an internal
 * selected state; chart cross-highlighting is intentionally not wired to
 * avoid a chart rewrite (hover selects the event in this timeline only).
 */
export function TradeTimeline({
  trade,
  onHighlight,
}: {
  trade: TimelineTrade;
  onHighlight?: (key: string | null) => void;
}) {
  const events = tradeTimelineEvents(trade);
  const [selected, setSelected] = useState<string | null>(null);

  const select = (key: string | null) => {
    setSelected(key);
    onHighlight?.(key);
  };

  return (
    <TooltipProvider delayDuration={180}>
      <ol className="trade-timeline" aria-label={`Execution timeline for trade ${trade.id} ${trade.direction}`}>
        {events.map((event, index) => {
          const tone = TONE_STYLE[event.tone];
          const active = selected === event.key;
          return (
            <li key={event.key} className={`tt-event tt-tone-${event.tone}${active ? ' tt-active' : ''}`}>
              <button
                type="button"
                className="tt-node"
                onClick={() => select(active ? null : event.key)}
                onMouseEnter={() => select(event.key)}
                onMouseLeave={() => select(null)}
                onFocus={() => select(event.key)}
                onBlur={() => select(null)}
                aria-label={`Step ${index + 1} of ${events.length}: ${event.title} at ${event.timestampUtc}${
                  event.side ? `, ${event.side}` : ''
                }${event.price !== undefined ? ` ${fmt(event.price)}` : ''}`}
                aria-pressed={active}
              >
                <span className="tt-dot" style={{ background: tone.color }} aria-hidden />
                <span className="tt-body">
                  <span className="tt-title">
                    {event.title}
                    <span className="tt-tone-tag" style={{ color: tone.color, borderColor: tone.color }}>
                      {tone.label}
                    </span>
                  </span>
                  <span className="tt-time">{event.timestampUtc}</span>
                  <span className="tt-note">{event.missing ? 'Not supplied by run' : event.note}</span>
                  {(event.side || event.price !== undefined) && (
                    <span className="tt-price">
                      {event.side && <span className="tt-side">{event.side}</span>}
                      {event.price !== undefined && <span className="tt-value">{fmt(event.price)}</span>}
                    </span>
                  )}
                </span>
              </button>
              {index < events.length - 1 && <span className="tt-link" aria-hidden />}
              <Tooltip>
                <TooltipTrigger asChild>
                  <span className="tt-hint" tabIndex={0}>
                    {index + 1}/{events.length}
                  </span>
                </TooltipTrigger>
                <TooltipContent className="max-w-72">
                  <strong>{event.title}</strong>
                  <p>{event.timestampUtc}</p>
                  <p className="meta">{event.missing ? 'Not supplied by run — omitted from charts.' : event.note}</p>
                </TooltipContent>
              </Tooltip>
            </li>
          );
        })}
      </ol>
    </TooltipProvider>
  );
}
