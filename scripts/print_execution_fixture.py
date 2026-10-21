"""Print the execution-parity fixture as a readable, reviewable scenario.

Read-only: this script never runs a backtest and never writes files. It renders
the reference fixture (ticks, signals, execution settings, expected trades) and
prints an empty recording table for an external reference run.

Usage:
    python scripts/print_execution_fixture.py
"""

import csv
import json
from pathlib import Path

FIXTURE = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "execution_parity"


def _read_csv(name: str) -> list[dict]:
    with (FIXTURE / name).open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _read_expected() -> dict:
    return json.loads((FIXTURE / "expected.json").read_text(encoding="utf-8"))


def _table(headers: list[str], rows: list[list[str]]) -> None:
    widths = [len(h) for h in headers]
    for row in rows:
        widths = [max(w, len(str(cell))) for w, cell in zip(widths, row)]
    line = "  ".join(h.ljust(w) for h, w in zip(headers, widths))
    print(line)
    print("  ".join("-" * w for w in widths))
    for row in rows:
        print("  ".join(str(cell).ljust(w) for cell, w in zip(row, widths)))


def main() -> None:
    ticks = _read_csv("ticks.csv")
    signals = _read_csv("signals.csv")
    expected = _read_expected()

    print("=" * 78)
    print("EXECUTION PARITY FIXTURE - REFERENCE SCENARIO")
    print("=" * 78)
    print(f"fixture: {FIXTURE}")
    print("all times are UTC; use these rows verbatim")
    print()
    print(
        "execution settings: "
        f"stop_distance={expected['stop_distance']}  "
        f"take_profit_distance={expected['take_profit_distance']}"
    )
    print("one position at a time; no trailing, no partial closes, no extra signals")
    print()

    print("-" * 78)
    print(f"TICKS ({len(ticks)})")
    print("-" * 78)
    _table(
        ["#", "timestamp (UTC)", "bid", "ask", "spread"],
        [
            [
                str(i),
                row["timestamp"],
                row["bid"],
                row["ask"],
                f"{float(row['ask']) - float(row['bid']):.2f}",
            ]
            for i, row in enumerate(ticks, start=1)
        ],
    )
    print()

    print("-" * 78)
    print(f"SIGNALS ({len(signals)})")
    print("-" * 78)
    _table(
        ["#", "signal_candle_start (UTC)", "signal_available_time (UTC)", "direction"],
        [
            [str(i), row["signal_candle_start"], row["signal_available_time"], row["direction"]]
            for i, row in enumerate(signals, start=1)
        ],
    )
    print()

    print("-" * 78)
    print(f"EXPECTED TRADES ({len(expected['trades'])})")
    print("-" * 78)
    _table(
        [
            "trade id", "direction", "entry time (UTC)", "entry price",
            "exit time (UTC)", "exit price", "exit reason", "pnl",
            "spread entry", "spread exit",
        ],
        [
            [
                str(trade["trade_id"]),
                trade["direction"],
                trade["entry_time"],
                f"{trade['entry_price']:.2f}",
                trade["exit_time"],
                f"{trade['exit_price']:.2f}",
                trade["exit_reason"],
                f"{trade['pnl_price']:+.2f}",
                f"{trade['spread_at_entry']:.2f}",
                f"{trade['spread_at_exit']:.2f}",
            ]
            for trade in expected["trades"]
        ],
    )
    print()
    counts = expected["signals"]
    print(
        f"signals: received={counts['received']}  "
        f"executed={counts['executed']}  ignored={counts['ignored']}"
    )
    print()

    print("-" * 78)
    print("RECORDING TABLE - fill this from the external reference run")
    print("-" * 78)
    _table(
        [
            "trade id", "direction", "entry time", "entry price",
            "exit time", "exit price", "exit reason", "pnl",
            "spread entry", "spread exit",
        ],
        [["", "", "", "", "", "", "", "", "", ""] for _ in range(len(expected["trades"]))],
    )
    print()
    print("Record exact UTC timestamps and prices. Do not adjust the fixture.")
    print("Return the filled table with any deviation notes.")


if __name__ == "__main__":
    main()
