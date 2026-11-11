'use client';

import { useState } from 'react';

/** ISO (YYYY-MM-DD) -> display value (DD/MM/YYYY). */
function isoToDmy(iso: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  return m ? `${m[3]}/${m[2]}/${m[1]}` : '';
}

/** DD/MM/YYYY -> ISO (YYYY-MM-DD), or null when the date is not real. */
function dmyToIso(text: string): string | null {
  const m = /^(\d{1,2})\/(\d{1,2})\/(\d{4})$/.exec(text.trim());
  if (!m) return null;
  const day = Number(m[1]);
  const month = Number(m[2]);
  const year = Number(m[3]);
  const dt = new Date(Date.UTC(year, month - 1, day));
  if (dt.getUTCFullYear() !== year || dt.getUTCMonth() !== month - 1 || dt.getUTCDate() !== day) {
    return null;
  }
  return `${String(year).padStart(4, '0')}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
}

/**
 * Keeps only digits, then lays them out as DD/MM/YYYY. A segment that
 * overflows (2 digits for day/month, 4 for year) spills into the next one, so
 * both digit-only typing (`02012025`) and explicit slashes (`2/1/2025`) work.
 */
function formatDmy(raw: string): string {
  const clean = raw.replace(/[^\d/]/g, '').slice(0, 10);
  const parts = clean.split('/');
  const segments = Math.min(parts.length, 3);
  const caps = [2, 2, 4];
  const out = ['', '', ''];
  for (let s = 0; s < 3; s++) {
    const incoming = s < segments ? parts[s] : '';
    const value = out[s] + incoming;
    out[s] = value.slice(0, caps[s]);
    const overflow = value.slice(caps[s]);
    if (overflow && s + 1 < 3) out[s + 1] += overflow;
  }
  let result = out[0];
  if (segments >= 2 || out[1] !== '') result += '/' + out[1];
  if (segments >= 3 || out[2] !== '') result += '/' + out[2];
  return result;
}

/**
 * Text date field with an explicit DD/MM/YYYY display order. Native
 * `<input type="date">` follows the browser locale (often MM/DD/YYYY), so the
 * value is kept as ISO for the API while the visible text stays day-first.
 */
export function DateField({
  value,
  onChange,
  label,
}: {
  value: string;
  onChange: (iso: string) => void;
  label: string;
}) {
  const [text, setText] = useState(() => isoToDmy(value));
  const [lastValue, setLastValue] = useState(value);

  if (value !== lastValue) {
    setLastValue(value);
    setText(isoToDmy(value));
  }

  const commit = (raw: string) => {
    const trimmed = raw.trim();
    if (/^\d{4}-\d{2}-\d{2}/.test(trimmed)) {
      const iso = trimmed.slice(0, 10);
      setText(isoToDmy(iso));
      setLastValue(iso);
      onChange(iso);
      return;
    }
    const next = formatDmy(raw);
    setText(next);
    const iso = dmyToIso(next) ?? '';
    setLastValue(iso);
    onChange(iso);
  };

  return (
    <input
      type="text"
      inputMode="numeric"
      autoComplete="off"
      placeholder="DD/MM/YYYY"
      value={text}
      onChange={(e) => commit(e.target.value)}
      aria-label={label}
      aria-invalid={text !== '' && dmyToIso(text) === null}
    />
  );
}
