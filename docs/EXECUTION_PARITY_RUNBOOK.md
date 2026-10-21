# Execution Parity Runbook — Manual Reference Run

This runbook explains how to reproduce the execution fixture in an independent
reference engine and record the result for comparison. It complements:

- the contract: `docs/EXECUTION_PARITY.md`
- the reference fixture: `tests/fixtures/execution_parity/`
- the automated test: `tests/test_execution_parity.py`

The automated test is authoritative for the repository. This runbook is for a
manual, independent cross-check.

---

## 1. What you need

Use **exactly** the fixture inputs — no substitutions, no extra candles, no
additional signals, no different date, no different time zone.

| Input | Requirement |
|---|---|
| Ticks | `tests/fixtures/execution_parity/ticks.csv` — the same 15 rows, same order, **UTC** |
| Signals | `tests/fixtures/execution_parity/signals.csv` — the same 4 rows and their `signal_available_time` in **UTC** |
| Stop distance | **0.50** (price units) |
| Take-profit distance | **1.00** (price units) |
| Positions | one at a time; no trailing, no partial closes, no pyramiding |
| Signals while a position is open | dropped (not queued) |
| Signal availability | the first tick with `timestamp >= signal_available_time` (inclusive) |

Do not modify the fixture or `expected.json`.

## 2. Print the scenario

```
python scripts/print_execution_fixture.py
```

The script only reads the fixture. It prints:

- every tick with BID, ASK and spread, in order and in UTC;
- every signal with its availability time and direction;
- `stop_distance` and `take_profit_distance`;
- the reference trades;
- an empty recording table.

## 3. Run the reference engine

Configure the reference engine with the printed ticks and signals and the two
distances above. Two rules matter most for a comparable result:

1. an exit is evaluated before consuming signals on the same tick;
2. exits fill at the observed tick price (BID for a long, ASK for a short), which
   also applies when price gaps through a level.

The full rule set is in `docs/EXECUTION_PARITY.md`.

## 4. Reference result (expected)

Recorded in `tests/fixtures/execution_parity/expected.json` and documented in
`docs/EXECUTION_PARITY.md`:

| trade id | direction | entry time (UTC) | entry price | exit time (UTC) | exit price | exit reason | pnl | spread entry | spread exit |
|---:|---|---|---:|---|---:|---|---:|---:|---:|
| 1 | LONG | 00:00:10 | 100.30 | 00:01:20 | 99.80 | SL | −0.50 | 0.20 | 0.20 |
| 2 | SHORT | 00:01:30 | 100.50 | 00:02:00 | 99.50 | TP | +1.00 | 0.20 | 0.20 |
| 3 | LONG | 00:02:10 | 99.90 | 00:02:20 | 99.60 | END_OF_DATA | −0.30 | 0.20 | 0.20 |

Signal accounting: **received 4 · executed 3 · ignored 1** (signal 2 becomes
available while the first position is still open and is dropped).

## 5. Record your run

Fill this table from the reference engine. Use the exact UTC timestamps and the
prices the engine reports.

| trade id | direction | entry time (UTC) | entry price | exit time (UTC) | exit price | exit reason | pnl | spread entry | spread exit |
|---:|---|---|---:|---|---:|---|---:|---:|---:|
|  |  |  |  |  |  |  |  |  |  |
|  |  |  |  |  |  |  |  |  |  |
|  |  |  |  |  |  |  |  |  |  |
|  |  |  |  |  |  |  |  |  |  |

Also note the signal counts you observed (received / executed / ignored), for
example: `4 / 3 / 1`.

## 6. How to report

Send back:

1. the filled table;
2. the observed signal counts;
3. any deviation, with the tick index where it occurs.

A match requires identical trade count, direction, entry/exit **times**,
entry/exit **prices**, exit reasons, PnL sign and value, and entry/exit spreads.
Formatting differences (e.g. two vs five decimals) are not a discrepancy;
different times, prices, reasons or trade counts are.

If the reference engine cannot reproduce the fixture, report the difference
instead of adjusting the fixture. The fixture and its `expected.json` are the
reference and are not edited to match an external result.
