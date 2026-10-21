# Execution Parity Contract

**Status:** v1 (reference contract for the verified engine)
**Engine:** `src/backtest.py` (not modified by this document)
**Reference fixture:** `tests/fixtures/execution_parity/`
**Deterministic test:** `tests/test_execution_parity.py`

This document records the execution contract the engine implements today. It is
the reference for any future engine change: a change that alters these rules is a
contract change, not a refactor, and must be reviewed and recorded here first.

---

## 1. Scope

The engine converts a normalized signal table plus real BID/ASK ticks into a list
of closed trades. It never builds candles, never generates signals, never sizes
positions in account currency.

Input (ticks): `timestamp`, `bid`, `ask`
Input (signals): `signal_candle_start`, `signal_available_time`, `direction`
Input (execution): `stop_distance`, `take_profit_distance` — price distances.

Out of scope: candle construction (upstream), account-currency conversion,
commission and volume accounting (downstream), strategy behaviour.

---

## 2. Contract

| # | Rule | Behaviour |
|---|---|---|
| 1 | **Tick order** | Ticks are sorted by `timestamp` with a stable sort. Equal timestamps keep their input order. Input order is otherwise irrelevant. |
| 2 | **Timezone / timestamp** | All timestamps must be UTC-aware. The engine does not convert time zones; comparisons are direct. Naive timestamps are not validated and fail at comparison time. |
| 3 | **Candle construction** | Performed upstream. The engine never sees candles; candle semantics are recorded in the data-engine documentation. |
| 4 | **Signal availability** | A signal becomes eligible at the first tick where `tick.timestamp >= signal_available_time` (**inclusive**). The runner convention is `signal_available_time = bar close`. |
| 5 | **First executable tick** | The first eligible tick at which no position is open. The entry price is that tick's side price (rule 6). |
| 6 | **Entry price** | `LONG` enters at **ASK**; `SHORT` enters at **BID**. |
| 7 | **SL / TP order** | On every tick, an open position is evaluated **before** signals are consumed. For `LONG`: SL if `bid <= stop_loss`, else TP if `bid >= take_profit`. For `SHORT`: SL if `ask >= stop_loss`, else TP if `ask <= take_profit`. SL is therefore checked first; for valid levels (`SL < entry < TP` on LONG, `TP < entry < SL` on SHORT) both cannot be true on the same tick. |
| 8 | **Exit price** | Exits use the **observed tick price**: `LONG` exits at BID, `SHORT` exits at ASK. A gap through a level fills at the observed price, not at the level itself. |
| 9 | **Spread** | Recorded as `ask - bid` on the entry tick (`spread_at_entry`) and on the exit tick (`spread_at_exit`). No separate spread charge exists: the BID/ASK side pricing already includes it. |
| 10 | **End of data** | An open position is closed on the last tick with reason `END_OF_DATA` (mapped to `EOD` by the result contract). LONG closes at the last BID, SHORT at the last ASK. |
| 11 | **Precision / rounding** | All arithmetic is float64. The engine performs **no rounding**; values are rounded/formatted only when presented. Parity comparisons use a `1e-9` tolerance for prices. |
| 12 | **Volume / commission** | The engine is unitless and price-termed: no position volume, no commission, no slippage model. Account-currency results, if any, are derived downstream from price PnL and a run-level account model. |

### Additional invariants

- **One position at a time.** A signal that becomes eligible while a position is
  open is **dropped**, not queued. No pyramiding, no hedging, no partial closes.
- **Exit before entry on the same tick.** If a position is closed on a tick, an
  eligible signal may open a new position on that same tick at that tick's price.
- **Signals after the last tick** are never evaluated.
- Multiple signals with the same availability keep their input order (stable
  sort); once a position is open, the remaining ones are dropped.
- A strategy cannot influence entry price, SL/TP levels, or exit reason: those
  are engine rules driven by the run's execution settings.

---

## 3. Reference fixture

A small, human-readable fixture pins the contract. All times are UTC.

`tests/fixtures/execution_parity/ticks.csv`

| time | bid | ask |
|---|---:|---:|
| 00:00:00 | 100.00 | 100.20 |
| 00:00:10 | 100.10 | 100.30 |
| 00:00:20 | 100.40 | 100.60 |
| 00:00:30 | 100.80 | 101.00 |
| 00:00:40 | 101.20 | 101.40 |
| 00:00:50 | 100.90 | 101.10 |
| 00:01:00 | 100.50 | 100.70 |
| 00:01:10 | 100.10 | 100.30 |
| 00:01:20 | 99.80 | 100.00 |
| 00:01:30 | 100.50 | 100.70 |
| 00:01:40 | 100.10 | 100.30 |
| 00:01:50 | 99.60 | 99.80 |
| 00:02:00 | 99.30 | 99.50 |
| 00:02:10 | 99.70 | 99.90 |
| 00:02:20 | 99.60 | 99.80 |

(date `2025-01-06`; execution: `stop_distance = 0.50`, `take_profit_distance = 1.00`)

`tests/fixtures/execution_parity/signals.csv`

| signal candle start | available | direction |
|---|---|---|
| 00:00:00 | 00:00:05 | LONG |
| 00:00:20 | 00:00:25 | SHORT |
| 00:01:20 | 00:01:25 | SHORT |
| 00:02:00 | 00:02:05 | LONG |

### Reference result (`expected.json`)

| id | dir | entry | price | SL | TP | exit | price | reason | pnl | spread in/out |
|---:|---|---|---:|---:|---:|---|---:|---|---:|---|
| 1 | LONG | 00:00:10 | 100.30 | 99.80 | 101.30 | 00:01:20 | 99.80 | SL | -0.50 | 0.20 / 0.20 |
| 2 | SHORT | 00:01:30 | 100.50 | 101.00 | 99.50 | 00:02:00 | 99.50 | TP | +1.00 | 0.20 / 0.20 |
| 3 | LONG | 00:02:10 | 99.90 | 99.40 | 100.90 | 00:02:20 | 99.60 | END_OF_DATA | -0.30 | 0.20 / 0.20 |

Signal accounting: **received 4 · executed 3 · ignored 1** (the second signal is
dropped because the first position is still open).

The second test in `tests/test_execution_parity.py` additionally pins:

- a signal available **exactly** on a tick time is eligible on that tick;
- the exit is evaluated before signals, so a new entry can occur on the same tick
  as an exit;
- end-of-data closes at the last observed BID for a LONG.

Run it with:

```
python -m pytest tests/test_execution_parity.py -q
```

It needs only the repository and pandas: no network, no server, no browser, no
randomness, and no external market data.

---

## 4. Current limitations and observations (reported, not changed)

These are recorded as-is. None of them was modified by this task.

1. **Ignored count includes never-evaluated signals.** The runner computes
   `ignored = received - executed`. A signal whose availability is after the last
   tick is never evaluated but still counts as ignored. This is reporting-level
   only; trade semantics are unaffected.
2. **No timezone validation.** Naive timestamps fail at comparison time rather
   than with an explicit contract error.
3. **Gap-through fills.** Exits and entries use the observed price, so realised
   PnL can exceed the nominal SL/TP distance (e.g. a gap through the stop). This
   is intentional and pinned by the fixture design, not by a dedicated gap case.
4. **SL/TP precedence is only observable for malformed levels.** For valid levels
   the two conditions are mutually exclusive on one tick.
5. **No volume, commission, slippage or margin** inside the engine; those belong
   to the downstream account model, so this fixture pins price-term mechanics
   only.
6. **Single position, no order types.** Limit/stop entries, modifying SL/TP,
   trailing, partial closes and multiple concurrent positions are not expressible
   by the engine contract.

---

## 5. Change policy

Any change to rules 1–12 or to the additional invariants requires:

1. updating this document,
2. updating the fixture and its `expected.json` from the documented rules,
3. a full regression run, and
4. explicit review approval.

Never update `expected.json` to match a changed engine output: the reference
result defines the contract, not the other way round.
