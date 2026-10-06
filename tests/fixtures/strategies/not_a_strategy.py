"""Invalid fixture: exposes no Strategy and no generate_signals(candles).

Used to prove the loader rejects a file that does not satisfy the strategy
interface, before any engine execution.
"""

NOT_A_STRATEGY = True
