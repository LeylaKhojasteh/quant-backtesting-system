"""External fixture WITHOUT declared PARAMETERS: must be rejected."""


def run_strategy(data, ctx):
    return {"trades": []}
