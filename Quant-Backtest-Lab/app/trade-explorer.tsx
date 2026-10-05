'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { ArrowUpDown, Search, X } from 'lucide-react';
import { fmt, time } from './demo';
import type { EnrichedTrade } from './analytics';

type DirectionFilter = 'ALL' | 'LONG' | 'SHORT';
type SortKey = 'id' | 'direction' | 'reason' | 'pnl' | 'durationMin' | 'mae' | 'mfe';

const ROW_HEIGHT = 46;
const OVERSCAN = 6;
/** Hard ceiling on mounted rows so the DOM stays bounded even if the viewport
 *  has not been measured yet (e.g. during first paint or inside a collapsed panel). */
const MAX_RENDERED = 80;

const REASON_LABEL: Record<string, string> = { SL: 'Stop loss', TP: 'Take profit', EOD: 'End of data' };

function pad3(n: number) {
  return String(n).padStart(3, '0');
}

/**
 * Searchable / sortable / filterable trade list.
 * Rows are windowed so the list stays responsive with 5,000+ trades.
 */
export function TradeExplorer({
  rows,
  selectedId,
  onSelect,
}: {
  rows: readonly EnrichedTrade[];
  selectedId: number;
  onSelect: (id: number) => void;
}) {
  const [query, setQuery] = useState('');
  const [direction, setDirection] = useState<DirectionFilter>('ALL');
  const [reason, setReason] = useState('ALL');
  const [sortKey, setSortKey] = useState<SortKey>('id');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc');
  const [scrollTop, setScrollTop] = useState(0);
  const [viewportHeight, setViewportHeight] = useState(420);
  const viewport = useRef<HTMLDivElement | null>(null);

  // Track the list height outside render so only the visible window is drawn.
  useEffect(() => {
    const el = viewport.current;
    if (!el) return;
    const apply = () => setViewportHeight(el.clientHeight || 420);
    apply();
    const observer = new ResizeObserver(apply);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    const out = rows.filter((t) => {
      if (direction !== 'ALL' && t.direction !== direction) return false;
      if (reason !== 'ALL' && t.reason !== reason) return false;
      if (!q) return true;
      return (
        String(t.id).includes(q) ||
        pad3(t.id).includes(q) ||
        t.direction.toLowerCase().includes(q) ||
        t.reason.toLowerCase().includes(q) ||
        REASON_LABEL[t.reason].toLowerCase().includes(q) ||
        fmt(t.pnl).includes(q)
      );
    });
    const factor = sortDir === 'asc' ? 1 : -1;
    return out.sort((a, b) => {
      const av = a[sortKey];
      const bv = b[sortKey];
      if (typeof av === 'string' || typeof bv === 'string') {
        return String(av).localeCompare(String(bv)) * factor;
      }
      return ((av as number) - (bv as number)) * factor;
    });
  }, [rows, query, direction, reason, sortKey, sortDir]);

  const reasons = useMemo(() => Array.from(new Set(rows.map((t) => t.reason))), [rows]);

  const toggleSort = (key: SortKey) => {
    if (key === sortKey) setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    else {
      setSortKey(key);
      setSortDir('asc');
    }
    if (viewport.current) viewport.current.scrollTop = 0;
    setScrollTop(0);
  };

  const height = viewportHeight;
  const start = Math.max(0, Math.min(filtered.length - 1, Math.floor(scrollTop / ROW_HEIGHT) - OVERSCAN));
  const visible = Math.ceil(height / ROW_HEIGHT) + OVERSCAN * 2;
  const end = Math.min(filtered.length, start + Math.min(visible, MAX_RENDERED));
  const window = filtered.slice(start, end);

  const sortButton = (key: SortKey, label: string) => (
    <button
      className={'trade-sort' + (sortKey === key ? ' active' : '')}
      onClick={() => toggleSort(key)}
      aria-label={`Sort by ${label}`}
    >
      {label}
      {sortKey === key && <span className="sort-dir">{sortDir === 'asc' ? '▲' : '▼'}</span>}
    </button>
  );

  return (
    <section className="trade-explorer panel" aria-label="Trade Explorer">
      <div className="panel-head">
        <h2>Trade Explorer</h2>
        <span className="meta">{filtered.length} / {rows.length}</span>
      </div>

      <div className="explorer-controls">
        <div className="search-box">
          <Search size={13} aria-hidden />
          <input
            value={query}
            onChange={(e) => { setQuery(e.target.value); setScrollTop(0); if (viewport.current) viewport.current.scrollTop = 0; }}
            placeholder="Search id / direction / reason"
            aria-label="Search trades"
          />
          {query && (
            <button className="search-clear" onClick={() => setQuery('')} aria-label="Clear search">
              <X size={12} />
            </button>
          )}
        </div>

        <div className="filter-row" role="group" aria-label="Filter by direction">
          {(['ALL', 'LONG', 'SHORT'] as const).map((d) => (
            <button
              key={d}
              className={'filter-chip' + (direction === d ? ' active' : '')}
              onClick={() => { setDirection(d); setScrollTop(0); if (viewport.current) viewport.current.scrollTop = 0; }}
              aria-pressed={direction === d}
            >
              {d === 'ALL' ? 'All' : d === 'LONG' ? 'Long' : 'Short'}
            </button>
          ))}
        </div>

        <div className="filter-row" role="group" aria-label="Filter by exit reason">
          {['ALL', ...reasons].map((r) => (
            <button
              key={r}
              className={'filter-chip' + (reason === r ? ' active' : '')}
              onClick={() => { setReason(r); setScrollTop(0); if (viewport.current) viewport.current.scrollTop = 0; }}
              aria-pressed={reason === r}
            >
              {r === 'ALL' ? 'Any exit' : REASON_LABEL[r] ?? r}
            </button>
          ))}
        </div>

        <div className="sort-row">
          <span className="sort-label"><ArrowUpDown size={12} /> Sort</span>
          {sortButton('id', 'ID')}
          {sortButton('pnl', 'PnL')}
          {sortButton('durationMin', 'Duration')}
          {sortButton('mae', 'MAE')}
          {sortButton('mfe', 'MFE')}
        </div>
      </div>

      <div className="trade-list-head">
        <span>Trade</span>
        <span>Dir</span>
        <span>Exit</span>
        <span>PnL</span>
      </div>

      <div
        className="trade-list"
        ref={viewport}
        onScroll={(e) => setScrollTop((e.target as HTMLDivElement).scrollTop)}
      >
        {filtered.length === 0 ? (
          <p className="trade-list-empty">No trades match the current filters.</p>
        ) : (
          <div style={{ height: filtered.length * ROW_HEIGHT, position: 'relative' }}>
            <div style={{ transform: `translateY(${start * ROW_HEIGHT}px)` }}>
              {window.map((t) => {
                const selected = t.id === selectedId;
                return (
                  <button
                    key={t.id}
                    className={'trade-row' + (selected ? ' selected' : '')}
                    onClick={() => onSelect(t.id)}
                    aria-current={selected}
                    style={{ height: ROW_HEIGHT }}
                  >
                    <span className="trade-row-id">Trade #{pad3(t.id)}</span>
                    <span className={'trade-row-dir ' + (t.direction === 'LONG' ? 'good' : 'bad')}>{t.direction}</span>
                    <span className={'trade-row-reason ' + (t.reason === 'TP' ? 'good' : t.reason === 'SL' ? 'bad' : 'neutral')}>
                      {t.reason}
                    </span>
                    <span className={'trade-row-pnl ' + (t.pnl > 0 ? 'good' : t.pnl < 0 ? 'bad' : '')}>{fmt(t.pnl)}</span>
                    <span className="trade-row-meta">{time(t.entryTime)} UTC · MAE {fmt(t.mae)} · MFE {fmt(t.mfe)}</span>
                  </button>
                );
              })}
            </div>
          </div>
        )}
      </div>
    </section>
  );
}