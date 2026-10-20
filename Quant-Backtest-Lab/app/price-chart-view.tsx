'use client';
import { useMemo, useState } from 'react';
import { Activity, ChevronLeft, ChevronRight } from 'lucide-react';
import { CandleChart, PriceChartLegend } from './charts';
import { FullscreenPanel } from './fullscreen-panel';
import { useCandleWindow } from './run-market';
import { viewportKey } from './viewport-key';
import { useRunSource } from './run-context';
import { candles as demoCandles } from './demo';
import type { Trade } from './types';

/**
 * Standalone Price Chart view.
 *
 * Nothing is fetched or mounted until the user clicks **Load Chart**: the
 * component derives its candle request from `loaded`, and `useCandleWindow`
 * performs no request while the run id is null. Pan, zoom, reset, trade
 * centering, edge loading and fullscreen are provided by CandleChart.
 */
export function PriceChartView({
  trades,
  symbol,
  timeframe,
}: {
  trades: Trade[];
  symbol: string;
  timeframe: string;
}) {
  const { source } = useRunSource();
  const isArchived = source.mode === 'archived';
  const runId = isArchived ? source.runId : null;

  const [loaded, setLoaded] = useState(false);
  const [overlay, setOverlay] = useState('Signals + Trades');
  const [index, setIndex] = useState(0);
  const [jump, setJump] = useState('1');
  const [centerTarget, setCenterTarget] = useState<{ t: number; nonce: number } | null>(null);

  // No request while `loaded` is false (runId passed as null).
  const win = useCandleWindow(loaded ? runId : null);
  const candles = isArchived ? win.candles : demoCandles;
  const status = isArchived ? win.status : 'ready';

  const total = trades.length;
  const safeIndex = total ? Math.max(0, Math.min(index, total - 1)) : 0;
  const selected = total ? trades[safeIndex] : undefined;

  const goto = (i: number) => {
    if (!total) return;
    const next = Math.max(0, Math.min(total - 1, i));
    setIndex(next);
    setJump(String(trades[next].id));
    setCenterTarget({ t: trades[next].entryTime, nonce: Date.now() });
  };

  const jumpTo = () => {
    const id = Number(jump);
    if (!Number.isFinite(id)) return;
    const found = trades.findIndex((t) => t.id === id);
    if (found >= 0) goto(found);
  };

  const centerOptions = useMemo(
    () => (selected ? `Trade #${selected.id} · ${selected.direction} · ${selected.reason}` : 'No trade'),
    [selected],
  );

  if (!loaded) {
    return (
      <section className="chart-load panel" aria-label="Load price chart">
        <div className="panel-head">
          <h2><Activity size={16} /> Price Chart</h2>
          <span className="meta">{symbol || '—'} · {timeframe}</span>
        </div>
        <div className="chart-load-body">
          <p>
            Real BID candles for the loaded run are only requested when you ask for them.
            Nothing is fetched or mounted while this view is closed.
          </p>
          <button className="primary" onClick={() => setLoaded(true)}>Load Chart</button>
          <p className="meta">
            Loads a bounded window (about 250 candles) from the run-scoped candle API — never the
            full dataset.
          </p>
        </div>
      </section>
    );
  }

  return (
    <FullscreenPanel
      title={`Price Chart · ${symbol || '—'} · ${timeframe}`}
      icon={<Activity size={16} />}
      actions={
        <>
          <span className="meta">BID Candles</span>
          <select
            className="chart-overlay-select"
            aria-label="Price chart overlays"
            value={overlay}
            onChange={(e) => setOverlay(e.target.value)}
          >
            {['Candles only', 'Signals', 'Trades', 'Signals + Trades'].map((o) => (
              <option key={o} value={o}>{o}</option>
            ))}
          </select>
          <span className="meta chart-center-label">{centerOptions}</span>
          <button
            aria-label="Previous trade"
            disabled={!total || safeIndex === 0}
            onClick={() => goto(safeIndex - 1)}
          >
            <ChevronLeft size={13} />
          </button>
          <input
            className="chart-jump"
            aria-label="Jump to trade id"
            value={jump}
            onChange={(e) => setJump(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') jumpTo(); }}
          />
          <button aria-label="Center on trade" onClick={jumpTo}>Center</button>
          <button
            aria-label="Next trade"
            disabled={!total || safeIndex === total - 1}
            onClick={() => goto(safeIndex + 1)}
          >
            <ChevronRight size={13} />
          </button>
        </>
      }
    >
      <div className="chart-stage">
        <CandleChart
          candles={candles}
          trades={trades}
          status={status}
          overlay={overlay}
          centerTarget={centerTarget}
          onSelect={(i) => setIndex(i)}
          onLoadMore={isArchived ? win.loadMore : undefined}
          hasMoreBefore={win.hasBefore}
          hasMoreAfter={win.hasAfter}
          viewportKey={viewportKey(isArchived ? runId : null)}
        />
      </div>
      <PriceChartLegend overlay={overlay} />
    </FullscreenPanel>
  );
}
