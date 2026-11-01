"""Minimal external (strategy-owned) fixture for loader tests.

Exposes ``run_strategy(data, ctx)`` plus declared PARAMETERS; no
generate_signals, no Strategy object.
"""

NAME = "External Minimal"
VERSION = "test-x1"
DESCRIPTION = "Loader fixture for strategy-owned execution."

PARAMETERS = {
    "warmup_bars": 2,
    "spread": 0.0,
}


def run_strategy(data, ctx):
    params = ctx.parameters if hasattr(ctx, "parameters") else {}
    warmup = int(params.get("warmup_bars", 2))
    candles = data.candles
    out = []
    for pos in range(warmup, len(candles)):
        out.append(("LONG", pos))
    return {"trades": [], "signals_seen": int(len(out))}
