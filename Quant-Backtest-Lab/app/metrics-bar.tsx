'use client';

import { Info } from 'lucide-react';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import { fmt } from './demo';
import { useRun } from './run-context';
import { accountDrawdown } from './analytics';
import { PendingMetric } from './pending-metric';

type Metric = {
  label: string;
  value: string;
  note: string;
  /** Optional short supporting value shown under the number (not a sentence). */
  sub?: string;
  tone?: 'good' | 'bad';
  pending?: boolean;
  reason?: string | string[];
  required?: string[];
};

const NOT_AVAILABLE = 'Not available';

function Card({ m }: { m: Metric }) {
  if (m.pending) {
    return (
      <PendingMetric
        label={m.label}
        reason={m.reason ?? m.note}
        required={m.required}
        compact
        status="Pending"
      />
    );
  }
  return (
    <div className="metric-cell">
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
      {m.sub && <small>{m.sub}</small>}
    </div>
  );
}

function stdev(v: number[]) {
  if (v.length < 2) return 0;
  const m = v.reduce((a, b) => a + b, 0) / v.length;
  return Math.sqrt(v.reduce((a, x) => a + (x - m) ** 2, 0) / (v.length - 1));
}

/**
 * Disciplined two-tier metric system fed by the single BacktestRun.
 *
 * Tier 1 (primary) carries the headline outcome; tier 2 (detail) carries the
 * supporting metrics. Price-term statistics render as numbers. Account-level
 * statistics (Return %, Sharpe, Balance) render Pending with the exact reason,
 * because a price-term run has no account-currency model. No zero is faked.
 */
export function PerformanceMetricsBar({ aside }: { aside?: React.ReactNode }) {
  const state = useRun();
  if (state.status !== 'ready') {
    return (
      <div className="metrics-grouped">
        <div className="metric-tier primary">
          <div className="metric-tier-head"><span className="metric-tier-title">Performance</span></div>
          <div className="metric-tier-cells primary">
            <div className="metric-cell">
              <span>{state.status === 'loading' ? 'Loading run…' : 'Load error'}</span>
              <strong className="muted">{NOT_AVAILABLE}</strong>
              <small>{state.status === 'error' ? state.error : state.status === 'malformed' ? state.message : ''}</small>
            </div>
          </div>
        </div>
      </div>
    );
  }
  const run = state.run;
  const p = run.metrics;
  const t = run.trades;
  const pnls = t.map((x) => x.pnl);
  const longs = t.filter((x) => x.direction === 'LONG').length;
  const shorts = t.length - longs;
  const reasons = p.accountUnavailableReasons ?? ['No account model configured.'];
  const volatility = stdev(pnls);

  // Account terms are present only when the account model is ready.
  const accountReady = run.account.accountReady === true;
  const balances = accountReady
    ? run.equity.map((e) => e.balance).filter((v): v is number => v !== null && v !== undefined)
    : [];
  const acctNet = accountReady ? t.reduce((a, x) => a + (x.pnlCurrency ?? 0), 0) : null;
  const acctCommission = accountReady ? t.reduce((a, x) => a + (x.commission ?? 0), 0) : null;
  const endingBalance = accountReady
    ? balances.length
      ? balances[balances.length - 1]
      : run.account.initialBalance != null && acctNet != null
        ? run.account.initialBalance + acctNet
        : null
    : null;
  const dd = accountReady ? accountDrawdown(balances) : null;

  const returnItem: Metric =
    accountReady && p.returnPct !== null && p.returnPct !== undefined
      ? { label: 'Return %', value: `${p.returnPct.toFixed(2)}%`, note: `Total account return · ${run.account.currency ?? ''}`, tone: p.returnPct < 0 ? 'bad' : 'good' }
      : {
          label: 'Return %',
          value: NOT_AVAILABLE,
          note: `Return % requires an account-currency balance series. ${reasons[0]}`,
          pending: true,
          reason: reasons,
          required: ['Account currency', 'Balance series in account currency'],
        };
  const balanceItem: Metric =
    accountReady && endingBalance !== null
      ? { label: 'Balance', value: fmt(endingBalance), note: `Ending realized balance · ${run.account.currency ?? ''}` }
      : {
          label: 'Balance',
          value: NOT_AVAILABLE,
          note: `Balance requires an account currency and account-currency PnL. ${reasons[0]} Price PnL is never added to a base balance.`,
          pending: true,
          reason: reasons,
          required: ['Account currency', 'Initial balance', 'Account-currency PnL'],
        };

  // Tier 1 — the headline outcome. These visually dominate.
  const primary: Metric[] = [
    { label: 'Total PnL', value: fmt(p.netPnl), note: 'Net closed-trade PnL · price terms, not account currency.', tone: p.netPnl < 0 ? 'bad' : 'good' },
    { label: 'Total Trades', value: String(p.trades), note: 'Executed reference trades' },
    { label: 'Win Rate', value: `${(p.winRate ?? 0).toFixed(2)}%`, note: 'Winning closed trades divided by total closed trades.', sub: `${p.wins} wins / ${p.losses} losses` },
    { label: 'Profit Factor', value: p.profitFactor !== null && p.profitFactor !== undefined ? p.profitFactor.toFixed(2) : NOT_AVAILABLE, note: 'Gross profit / absolute gross loss · price terms' },
    { label: 'Max Drawdown', value: p.maxDrawdown !== null && p.maxDrawdown !== undefined ? p.maxDrawdown.toFixed(2) : NOT_AVAILABLE, note: 'Peak-to-trough decline of the closed-trade PnL curve · price terms', tone: 'bad' },
  ];

  // Tier 2 — supporting metrics, denser and quieter.
  const detail: Metric[] = [
    { label: 'Average PnL', value: fmt(p.avgPnl), note: 'Mean PnL per trade · price terms', tone: p.avgPnl < 0 ? 'bad' : 'good' },
    { label: 'Expectancy', value: fmt(p.avgPnl), note: 'Average expected PnL per trade · price terms.', tone: p.avgPnl < 0 ? 'bad' : 'good' },
    { label: 'Recovery Factor', value: p.maxDrawdown ? fmt(p.netPnl / p.maxDrawdown) : NOT_AVAILABLE, note: 'Net price PnL divided by maximum closed-trade PnL drawdown · price terms', tone: p.netPnl < 0 ? 'bad' : 'good' },
    { label: 'Risk Reward', value: p.payoff !== null && p.payoff !== undefined ? p.payoff.toFixed(2) : NOT_AVAILABLE, note: 'Average win divided by average loss · price terms' },
    { label: 'Max Losing Streak', value: String(p.maxLosingStreak ?? 0), note: 'Longest run of consecutive losing trades', tone: 'bad' },
    { label: 'Volatility', value: volatility.toFixed(2), note: 'Standard deviation of per-trade PnL · price terms' },
    { label: 'Long / Short', value: `${longs} / ${shorts}`, note: 'Executed LONG and SHORT trades' },
    { label: 'Average Win', value: fmt(p.avgWin), note: 'Mean PnL of winning trades · price terms', tone: 'good' },
    { label: 'Average Loss', value: fmt(p.avgLoss), note: 'Mean PnL of losing trades · price terms', tone: 'bad' },
    { label: 'Best Trade', value: fmt(Math.max(...pnls)), note: 'Largest single-trade PnL · price terms', tone: 'good' },
    { label: 'Worst Trade', value: fmt(Math.min(...pnls)), note: 'Largest single-trade loss · price terms', tone: 'bad' },
    { label: 'Avg Duration', value: `${(p.avgDurationMin ?? 0).toFixed(0)} min`, note: 'Mean time in trade, entry to exit · UTC' },
    ...(accountReady
      ? [
          { label: 'Net Account PnL', value: fmt(acctNet ?? 0), note: `Realized account PnL · ${run.account.currency ?? ''} (after commission)`, tone: (acctNet ?? 0) < 0 ? 'bad' as const : 'good' as const },
          { label: 'Commission', value: fmt(acctCommission ?? 0), note: `Total round-turn commission · ${run.account.currency ?? ''}` },
          ...(dd
            ? [{ label: 'Realized DD', value: dd.maxDrawdown !== null ? fmt(dd.maxDrawdown) : NOT_AVAILABLE, note: `Max realized-balance drawdown${dd.maxDrawdownPct !== null ? ` · ${dd.maxDrawdownPct.toFixed(2)}%` : ''} · account currency`, tone: 'bad' as const }]
            : []),
        ]
      : []),
    {
      label: 'Sharpe Ratio',
      value: NOT_AVAILABLE,
      note: 'Standard Sharpe requires a valid account return series (periodic returns). Seven price-term trades are not a return series.',
      pending: true,
      reason: 'Standard Sharpe requires a valid account return series (periodic returns). Seven price-term trades are not a return series.',
      required: ['Account return series per period', 'Risk-free rate', 'Account currency'],
    },
    returnItem,
    balanceItem,
  ];

  return (
    <TooltipProvider delayDuration={200}>
      <div className="metrics-grouped">
        <div className="metrics-groups">
          <div className="metric-tier primary">
            <div className="metric-tier-head">
              <span className="metric-tier-title">Performance</span>
              <span className="meta">price terms · returned metrics only</span>
            </div>
            <div className="metric-tier-cells primary">
              {primary.map((m) => <Card key={m.label} m={m} />)}
            </div>
          </div>
          <div className="metric-tier secondary">
            <div className="metric-tier-head">
              <span className="metric-tier-title">Detail</span>
              <span className="meta">supporting metrics · pending stays truthful</span>
            </div>
            <div className="metric-tier-cells secondary">
              {detail.map((m) => <Card key={m.label} m={m} />)}
            </div>
          </div>
        </div>
        {aside}
      </div>
    </TooltipProvider>
  );
}
