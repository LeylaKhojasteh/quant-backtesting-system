/**
 * Quant Backtest Lab — UTC time contract.
 *
 * Every timestamp in the product is ISO-8601 UTC with a trailing `Z`. There is
 * no local time anywhere in the data path:
 *
 *   - the run model stores UTC ISO strings (`datetime({ offset: false })` in the
 *     Zod schema rejects anything that is not a plain UTC instant);
 *   - the date-range inputs are `datetime-local` fields whose meaning is
 *     defined here: the user picks a UTC wall-clock time, and this module is
 *     the single place that turns it into an ISO-8601 `Z` string.
 */

export const TIMEZONE = 'UTC' as const;

/** An instant we can prove is UTC, ISO-8601, `Z`-terminated. */
export type UtcIso = `${string}Z`;

/** ISO-8601 instant with trailing Z; rejects offsets like `+00:00` and zone-less strings. */
export const UTC_ISO_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d{1,9})?)?Z$/;

export function isUtcIso(value: unknown): value is UtcIso {
  return typeof value === 'string' && UTC_ISO_RE.test(value) && !Number.isNaN(Date.parse(value));
}

/**
 * Convert an epoch-millisecond timestamp into the canonical UTC ISO string.
 * This is the only place the codebase may produce a run timestamp.
 */
export function toUtcIso(epochMs: number): UtcIso {
  const t = Number.isFinite(epochMs) ? new Date(epochMs).toISOString() : new Date(NaN).toISOString();
  if (!Number.isFinite(epochMs) || !t.endsWith('Z')) {
    throw new Error(`toUtcIso: ${epochMs} is not a finite timestamp`);
  }
  return t as UtcIso;
}

export type DateTimeInputError =
  | { code: 'empty'; field: string }
  | { code: 'malformed'; field: string; raw: string }
  | { code: 'invalid-date'; field: string; raw: string }
  | { code: 'not-utc-comparable'; field: string; raw: string }
  | { code: 'start-after-end'; start: string; end: string };

/**
 * `datetime-local` value, e.g. `2025-01-31T22:00`. The input has no timezone
 * of its own — by contract in this product it is a UTC wall-clock time. This
 * function is the single conversion point; it refuses anything it cannot
 * prove maps to a UTC instant.
 */
export function parseUtcDateTimeLocal(raw: string, field: string): { ok: true; utc: UtcIso } | { ok: false; error: DateTimeInputError } {
  const value = (raw ?? '').trim();
  if (!value) return { ok: false, error: { code: 'empty', field } };
  // Accept `YYYY-MM-DDTHH:mm[:ss]` (the browser's datetime-local shape).
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?$/.exec(value);
  if (!m) return { ok: false, error: { code: 'malformed', field, raw: value } };
  const [, y, mo, d, h, mi, s = '0'] = m;
  const date = Date.UTC(Number(y), Number(mo) - 1, Number(d), Number(h), Number(mi), Number(s));
  if (Number.isNaN(date)) return { ok: false, error: { code: 'invalid-date', field, raw: value } };
  // `Date.UTC` normalises out-of-range fields (e.g. month 13 -> next year);
  // verify the fields round-trip so we never accept "2025-13-40".
  const iso = new Date(date).toISOString();
  if (
    !iso.startsWith(`${m[1]}-${m[2]}-${m[3]}`)
    || iso.slice(11, 13) !== m[4]
    || iso.slice(14, 16) !== m[5]
  ) {
    return { ok: false, error: { code: 'invalid-date', field, raw: value } };
  }
  return { ok: true, utc: iso as UtcIso };
}

export type RangeValidation =
  | { ok: true; startUtc: UtcIso; endUtc: UtcIso; spanMs: number }
  | { ok: false; error: DateTimeInputError };

/**
 * Validate a start/end pair entered by the user:
 * both parse, both are UTC-instant, and start < end.
 */
export function validateUtcRange(startRaw: string, endRaw: string): RangeValidation {
  const start = parseUtcDateTimeLocal(startRaw, 'start');
  if (!start.ok) return { ok: false, error: start.error };
  const end = parseUtcDateTimeLocal(endRaw, 'end');
  if (!end.ok) return { ok: false, error: end.error };
  const span = Date.parse(end.utc) - Date.parse(start.utc);
  if (span <= 0) return { ok: false, error: { code: 'start-after-end', start: startRaw, end: endRaw } };
  return { ok: true, startUtc: start.utc, endUtc: end.utc, spanMs: span };
}

/** Human-readable display of a UTC instant, always tagged UTC. */
export function formatUtc(iso: UtcIso, opts: { withDate?: boolean; withSeconds?: boolean; withMs?: boolean } = {}): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  const hh = String(d.getUTCHours()).padStart(2, '0');
  const mm = String(d.getUTCMinutes()).padStart(2, '0');
  const ss = String(d.getUTCSeconds()).padStart(2, '0');
  const datePart = opts.withDate
    ? `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}-${String(d.getUTCDate()).padStart(2, '0')} `
    : '';
  let out = `${datePart}${hh}:${mm}`;
  if (opts.withSeconds) out += `:${ss}`;
  if (opts.withMs) out += `.${String(d.getUTCMilliseconds()).padStart(3, '0')}`;
  return out + ' UTC';
}

/** Format an epoch-millisecond timestamp through the shared UTC display rules. */
export function formatUtcMs(epochMs: number, opts?: { withDate?: boolean; withSeconds?: boolean; withMs?: boolean }): string {
  return formatUtc(toUtcIso(epochMs), opts);
}
