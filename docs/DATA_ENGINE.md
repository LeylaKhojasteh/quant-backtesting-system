# Data Engine v1 (Phase 1)

Immutable raw market data + a derived, rebuildable Parquet tick store.

## Layers

```
RAW SOURCE                         data/*.csv          (READ-ONLY, source of truth)
    │
    │  canonical converter (bounded memory, atomic, idempotent)
    ▼
DERIVED TICK STORE                 cache/market_data/  (Parquet, monthly partitions)
    │
    │  reader (partition pruning, [start, end])
    ▼
canonical tick frame  →  existing candle construction / strategy / engine
    │
    └── FUTURE: Candle Cache (derived from ticks; never authoritative for execution)
```

- **RAW** — the CSV files in `data/` are never written, renamed, moved, sorted,
  deduplicated, or cleaned. They are the permanent source of truth.
- **DERIVED TICKS** — Parquet, monthly partitioned, rebuildable and disposable.
  Deleting the cache never destroys research source data.
- **FUTURE CANDLES** — cached separately and derived from ticks. Execution keeps
  using tick-level BID/ASK; a candle cache never replaces the tick store.

## Storage layout

```
cache/
  market_data/
    ticks/
      provider=DUKAS/
        symbol=XAUUSD/
          year=2025/
            month=01/
              ticks.parquet
              metadata.json
            month=02/ …
    catalog.json          # logical dataset identities + actual coverage
    manifest.json         # source fingerprints ↔ derived partitions
    raw_inventory.json    # full read-only source inventory
```

`cache/` is git-ignored; Parquet is never committed.

## Canonical derived schema

| column       | type                    | notes |
|--------------|-------------------------|-------|
| timestamp    | timestamp[ns, UTC]      | Dukascopy wall-clock treated as UTC, never shifted |
| bid          | float64                 | authoritative |
| ask          | float64                 | authoritative (mid is never derived) |
| bid_volume   | float64                 | preserved, no coercion |
| ask_volume   | float64                 | preserved, no coercion |
| source_row   | int64                   | internal; preserves equal-timestamp source order |

`source_row` is internal ordering metadata only and does not change trading
semantics; the engine reader drops it.

## Verified data semantics (preserved exactly)

- Dukascopy `date,time` wall-clock is interpreted as UTC **without shifting**.
- BID and ASK remain separate authoritative prices.
- Natural weekends / holidays / market gaps are **not** corruption.
- No synthetic ticks, no gap filling, no dedup, no interpolation, no rounding,
  no spread correction, no resampling, no candle generation during conversion.
- Stable chronological order; equal timestamps keep source order.
- Existing candle boundaries and execution behavior remain authoritative.

## Cache versioning & invalidation

- `DATA_CACHE_SCHEMA_VERSION` (currently `1`) identifies the derived schema.
- A partition is current when: raw `sha256` unchanged **and** schema version
  unchanged **and** the partition is present and valid → reported as `CACHE HIT`.
- If a source filename already exists in the manifest but its `sha256` changed,
  the build **fails** (`raw_source_changed`) because `data/` is immutable.

## Logical dataset identity & source roles

- The logical dataset id is **year-independent**: `provider + symbol + data_type`
  → `xauusd_dukas_ticks`. Years/months are partition dimensions, not identity.
- Source roles (`raw_inventory.json`):
  - monthly CSVs → `source_role: monthly_partition`, `included_in_logical_dataset: true`
  - `XAUUSD_DUKAS_ALL_ticks.csv` → `source_role: aggregate_reference`,
    `included_in_logical_dataset: false`, `derived_partition: none`
- The **monthly files are the authoritative source family**. The `ALL` aggregate
  is a read-only reference and is **never** used for range loading, never
  concatenated with monthlies, never a fallback, and never counts toward logical
  totals or the partition count.

## Verified `ALL` discrepancy (no repair)

- 2025 monthly source family: **71,413,977** ticks.
- `XAUUSD_DUKAS_ALL_ticks.csv`: **71,413,966** ticks.
- `ALL` is missing **11** records: the **first tick of each month Feb–Dec 2025**
  (January's first tick is present).
- No extra rows, no duplicate rows, no ordering discrepancy were found.
- This is **not corruption of the authoritative dataset**: the monthly files are
  intact and complete, and every derived partition verified exactly against its
  monthly parent. No repair is performed; `ALL` stays read-only reference/provenance.

## Partition coverage truthfulness

Each partition/metadata retains actual `first_timestamp`, `last_timestamp`,
`row_count` (observed data), plus an explicit `completeness` state that comes
from **source provenance only** — never from timestamp span / calendar coverage:

- `complete` — explicitly declared complete for this source.
- `partial` — explicitly declared partial.
- `unknown` — no completeness declared.

Completeness is declared in `SOURCE_COMPLETENESS` (`src/data_engine.py`) keyed by
source filename. Natural weekends / holidays / market closures are expected and
are not relevant to completeness. A new/future monthly source is **`unknown`**
until its completeness is explicitly declared — it never becomes `complete`
merely because it spans much of a month.

Current catalog semantics:

- 2025: `2025-01 … 2025-12` → **complete** (12/12 complete monthly partitions).
- 2026: `2026-01` → **partial** (only `2026-01-01`, `7,245` rows, coverage
  `2026-01-01 23:00:00.596 UTC → 2026-01-01 23:59:58.401 UTC`).

`2026-01` is never presented as a full January.

## Internal date-range semantics

The reader contract is half-open: `[start, end_exclusive)`.

- User selects inclusive calendar dates.
- `start` → `YYYY-MM-DD 00:00:00 UTC`.
- inclusive `end` → **next calendar day 00:00:00 UTC** exclusive
  (`2025-03-17 → 2025-05-08` reads `[2025-03-17 00:00, 2025-05-09 00:00)`).
- No `23:59:59.999999` hacks; user-facing date semantics are unchanged.

## Catalog totals (distinct concepts)

- raw inventory files: `14`
- authoritative monthly partitions: `13` (12 × 2025 + 1 partial 2026-01)
- 2025 authoritative rows: `71,413,977`
- 2026-01 authoritative rows: `7,245`
- total logical rows: `71,421,222`
- `ALL` aggregate rows: `71,413,966` (**not included** in logical totals)

## Source fingerprint protection

Every conversion records `source_sha256`, `source_size_bytes`, `source_rows`,
`first_timestamp`, `last_timestamp`, `symbol`, `provider` in the manifest, and a
per-partition `metadata.json`. Pre-build and post-build raw fingerprints must be
identical.

## Correctness verification

`verify` performs an **exact, full-partition** comparison between the CSV parse
and the Parquet load for: row count, timestamps, bid/ask/bid_volume/ask_volume,
and deterministic order. Both sides are streamed in identical 500k-row blocks
and hashed (sha256); no sampling, no tolerance.

## Reading

`src.data_engine.load_ticks_frame("cache:XAUUSD", symbol=..., start=..., end=...)`
opens only the monthly partitions overlapping `[start, end]` (external end date
is inclusive, matching the existing `BacktestRunRequest` semantics), filters by
timestamp, and returns the canonical tick frame. Wide ranges never scan
unrelated months.

## CLI

```
python -m src.data_engine inventory     # read-only source inventory
python -m src.data_engine build --all   # build/update all monthly partitions
python -m src.data_engine build --month 2025-01
python -m src.data_engine verify        # exact CSV<->Parquet verification
python -m src.data_engine inspect       # logic dataset identity + coverage
python -m src.data_engine golden        # January golden CSV vs Parquet parity
python -m src.data_engine bench         # honest load-time benchmark
# or: python scripts/build_data_cache.py <command>
```

## Next phase (design only)

Candle cache (derived from canonical ticks):

```
cache/market_data/candles/
  provider=DUKAS/symbol=XAUUSD/timeframe=15min/year=2025/month=01/candles.parquet
```

Candle rule to preserve: `[start, start + 15 minutes)`, left-labeled,
left-closed; a signal becomes available only after candle completion; no
look-ahead; execution continues to use tick-level BID/ASK.

---

# Phase 2 — Candle cache + real dataset integration

## Candle cache (derived from canonical ticks only)

```
cache/market_data/candles/
  provider=DUKAS/symbol=XAUUSD/timeframe=15min/year=2025/month=01/candles.parquet (+ metadata.json)
```

- `CANDLE_CACHE_SCHEMA_VERSION = 1`, `CANDLE_BUILDER_VERSION = 1`.
- Supported timeframes: `1min, 5min, 15min, 30min, 1h, 4h` (must equal the
  engine-supported set).
- Columns: `timestamp` + BID `open/high/low/close` + derived ASK
  `ask_open/ask_high/ask_low/ask_close` (ASK derived from authoritative tick ASK;
  MID is never used). float64 prices.
- Built **from Parquet ticks**, never from raw CSV. Bounded memory, atomic
  (temp in cache → validate → `os.replace`), idempotent (`CACHE HIT` when the
  tick partition fingerprint + schema + builder version + timeframe are
  unchanged). Missing candle cache → structured `data_not_prepared`.

## Runner integration

- New runs use a first-class logical dataset: `market.dataset_id` →
  `cache:<dataset_id>` internally. Legacy `market.data_source` (CSV path) still
  works unchanged (legacy runs isolated).
- For logical runs: **execution uses canonical Parquet ticks**; **strategy
  candles come from the candle cache** (not on-the-fly). Legacy path keeps
  on-the-fly resampling.
- Provenance is archived in `config.json` under `data_provenance`
  (dataset_id, provider, symbol, timeframe, coverage, per-partition tick +
  candle fingerprints, cache schema/builder versions). The archived
  `data_source` is the friendly logical id, not the internal `cache:` key.
- Missing tick/candle cache during a run → `data_not_prepared` (never a silent
  rebuild, never an ALL fallback).

## Bridge & product

- `GET /datasets` exposes logical datasets (`dataset_id`, symbol, provider,
  coverage, complete/partial, supported + prepared timeframes, default range) —
  never monthly CSV files or filesystem paths.
- `GET /datasets/<id>/ticks?start&end&limit` → read-only raw-tick preview from the
  canonical logical dataset.
- Run Setup: dataset selector = `XAUUSD · Dukascopy`; symbol derived/read-only;
  controlled timeframe dropdown; real date range with a default complete-year
  (2025-01-01 → 2025-12-31, configuration only — not auto-run); prepared state
  ("Data Ready" / "Preparation required for <tf>").

## CLI

```
python -m src.data_engine build-candles [--timeframe 15min] [--force]
python -m src.data_engine verify-candles [--timeframe 15min]
python -m src.data_engine inspect
```

## Verification

- Exact candle parity: cached candles vs on-the-fly resampling from the same
  tick partition, for every partition × timeframe (13 × 6 = 78), full data.
- Month-boundary regression: no duplicate/missing/truncated/shifted candle at
  Jan→Feb, Feb→Mar, Dec→Jan (incl. 4h).
- Golden: M15 reference candle OHLC (BID/ASK) and the 7-trade / −15.82 January
  result reproduce through the cached-candle + Parquet-tick path.

