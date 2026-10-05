'use client';

import { Info } from 'lucide-react';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import { fmt } from './demo';
import { performance, risk, type EnrichedTrade } from './analytics';

type Metric = {
  label: string;
  value: string;
  note: string;
  tone?: 'good' | 'bad';
};

function Card({ m }: { m: Metric }) {
  return (
    <div>
      <span>
        {m.label}
        <Tooltip>
          <TooltipTrigger asChild>
            <button className="help" aria-label={`About ${m.label}`}>
              <Info size={12} />
            </button>
          </TooltipTrigger>
          <TooltipContent className="max-w-80">{m.note}</TooltipContent>
        </Tooltip>
      </span>
      <strong className={m.tone ? `${m.tone}-text` : undefined}>{m.value}</strong>
      <small>{m.note}</small>
    </div>
  );
}

/**
 * Grouped performance metrics.
 * Every value is derived from the trade set; the demo balance preview stays
 * labelled as a preview because no account-currency model exists.
 */
export function PerformanceMetricsBar({
  rows,
  balancePreview,
  aside,
}: {
  rows: readonly EnrichedTrade[];
  balancePreview: string;
  aside?: React.ReactNode;
}) {
  const p = performance(rows);
  const r = risk(rows);
  const longs = rows.filter((t) => t.direction === 'LONG').length;
  const shorts = rows.filter((t) => t.direction === 'SHORT').length;

  const groups: { title: string; items: Metric[] }[] = [
    {
      title: 'Performance',
      items: [
        { label: 'Total Trades', value: String(p.trades), note: 'Executed reference trades' },
        { label: 'Win Rate', value: `${p.winRate.toFixed(2)}%`, note: `${p.wins} win / ${p.losses} losses` },
        { label: 'Total PnL', value: fmt(p.netPnl), note: 'PnL is expressed in price terms, not account currency.', tone: p.netPnl < 0 ? 'bad' : 'good' },
        { label: 'Average PnL', value: fmt(p.avgPnl), note: 'Mean PnL per trade · price terms', tone: p.avgPnl < 0 ? 'bad' : 'good' },
        { label: 'Profit Factor', value: p.profitFactor.toFixed(2), note: 'Gross profit / absolute gross loss' },
        {
          label: 'Expectancy',
          value: fmt(p.expectancy),
          note: 'Average expected PnL per trade based on historical results.',
          tone: p.expectancy < 0 ? 'bad' : 'good',
        },
        {
          label: 'Sharpe Ratio',
          value: r.sharpe.toFixed(2),
          note: 'Mean trade PnL / standard deviation of trade PnL. Per-trade, not annualized.',
          tone: r.sharpe < 0 ? 'bad' : 'good',
        },
        {
          label: 'Return %',
          value: `${p.returnPct.toFixed(2)}%`,
          note: 'Net PnL against the 10,000 demo account base.',
          tone: p.returnPct < 0 ? 'bad' : 'good',
        },
      ],
    },
    {
      title: 'Risk',
      items: [
        { label: 'Max Drawdown', value: r.maxDrawdown.toFixed(2), note: 'Largest peak-to-trough decline of the closed-trade equity curve', tone: 'bad' },
        { label: 'Recovery Factor', value: r.recoveryFactor.toFixed(2), note: 'Net PnL divided by maximum closed-trade drawdown', tone: r.recoveryFactor < 0 ? 'bad' : 'good' },
        { label: 'Risk Reward', value: p.payoff.toFixed(2), note: 'Average win divided by average loss' },
        { label: 'Max Losing Streak', value: String(r.maxLosingStreak), note: 'Longest run of consecutive losing trades', tone: 'bad' },
        {
          label: 'Volatility',
          value: r.volatility.toFixed(2),
          note: 'Standard deviation of per-trade PnL · price terms',
        },
      ],
    },
    {
      title: 'Trade Statistics',
      items: [
        { label: 'Long Trades', value: String(longs), note: 'Executed LONG trades' },
        { label: 'Short Trades', value: String(shorts), note: 'Executed SHORT trades' },
        { label: 'Average Win', value: fmt(p.avgWin), note: 'Mean PnL of winning trades', tone: 'good' },
        { label: 'Average Loss', value: fmt(p.avgLoss), note: 'Mean PnL of losing trades', tone: 'bad' },
        { label: 'Best Trade', value: fmt(r.bestTrade), note: 'Largest single-trade PnL', tone: 'good' },
        { label: 'Worst Trade', value: fmt(r.worstTrade), note: 'Largest single-trade loss', tone: 'bad' },
        { label: 'Avg Duration', value: `${r.avgDurationMin.toFixed(0)} min`, note: 'Mean time in trade, entry to exit' },
        { label: 'Win Streak', value: String(r.maxWinningStreak), note: 'Longest run of consecutive wins', tone: 'good' },
        { label: 'Loss Streak', value: String(r.maxLosingStreak), note: 'Longest run of consecutive losses', tone: 'bad' },
        {
          label: 'Balance',
          value: balancePreview,
          note: 'Demo balance = 10,000 + cumulative price-term PnL. Lot sizing and account-currency PnL are not implemented in the real engine yet.',
        },
      ],
    },
  ];

  return (
    <TooltipProvider delayDuration={200}>
      <div className="metrics-grouped">
        <div className="metrics-groups">
          {groups.map((g) => (
            <div className="metrics-group" key={g.title}>
              <span className="metrics-group-title">{g.title}</span>
              <div className="metrics-group-cells">
                {g.items.map((m) => (
                  <Card key={m.label} m={m} />
                ))}
              </div>
            </div>
          ))}
        </div>
        {aside}
      </div>
    </TooltipProvider>
  );
}