"""Tests for the research-platform foundation (contract/interface/run store).

Covers only the new modules: ``src/result_contract.py``,
``src/strategies/base.py``, ``src/run_store.py``. The verified engine,
strategies, notebooks, and data are never modified — only read.
"""

import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import backtest as engine
from src import run_store
from src.result_contract import (
    BacktestResult,
    DataQuality,
    ExecutionInfo,
    RunConfig,
    StrategyInfo,
    compute_metrics,
    equity_from_trades,
    trades_from_engine,
)
from src.strategies import base as strategy_base


def _demo_trades_df():
    ticks = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(
                ["2025-01-01T00:00:00Z", "2025-01-01T00:00:01Z", "2025-01-01T00:00:02Z"]
            ),
            "bid": [100.0, 110.0, 90.0],
            "ask": [100.5, 110.5, 90.5],
        }
    )
    signals = pd.DataFrame(
        {
            "signal_candle_start": pd.to_datetime(["2024-12-31T23:45:00Z"]),
            "signal_available_time": pd.to_datetime(["2025-01-01T00:00:00Z"]),
            "direction": ["LONG"],
        }
    )
    return engine.run_backtest(ticks, signals, 5, 10)


def _demo_result(run_id="run_test"):
    records = trades_from_engine(_demo_trades_df())
    metrics = compute_metrics(records)
    return BacktestResult(
        contract_version="1.0",
        run_id=run_id,
        created_at="2025-01-01T00:00:00Z",
        strategy=StrategyInfo(name="Candle Direction", version="1.0"),
        config=RunConfig(
            symbol="XAUUSD", timeframe="15min", start="s", end="e", parameters={"stop_distance": 5}
        ),
        trades=records,
        equity=equity_from_trades(records),
        metrics=metrics,
        data_quality=DataQuality(data_source="demo"),
        execution=ExecutionInfo(),
    )


def test_contract_has_all_required_sections():
    result = _demo_result()
    assert result.validate() == []
    payload = json.loads(result.to_json())
    for key in (
        "run_id",
        "strategy",
        "config",
        "trades",
        "equity",
        "metrics",
        "data_quality",
        "execution",
    ):
        assert key in payload
    assert payload["strategy"]["version"] == "1.0"
    assert payload["config"]["symbol"] == "XAUUSD"
    assert payload["config"]["timeframe"] == "15min"
    assert payload["config"]["parameters"]["stop_distance"] == 5


def test_engine_exit_reason_normalized_to_contract():
    records = trades_from_engine(_demo_trades_df())
    assert records, "engine should produce a trade on this fixture"
    assert all(r.exit_reason in ("TP", "SL", "EOD") for r in records)


def test_metrics_and_equity_consistent_without_account_synthesis():
    result = _demo_result()
    assert result.metrics.total_trades == len(result.trades)
    assert len(result.equity) >= len(result.trades)
    for point in result.equity:
        assert point.balance is None
        assert point.equity is None


def test_strategy_interface_contract():
    assert strategy_base.SIGNAL_COLUMNS == [
        "signal_candle_start",
        "signal_available_time",
        "direction",
    ]
    bad_signals = pd.DataFrame({"direction": ["LONG"]})
    assert strategy_base.validate_signals(bad_signals) != []
    good_signals = pd.DataFrame(
        {
            "signal_candle_start": pd.to_datetime(["2024-12-31T23:45:00Z"]),
            "signal_available_time": pd.to_datetime(["2025-01-01T00:00:00Z"]),
            "direction": ["LONG"],
        }
    )
    assert strategy_base.validate_signals(good_signals) == []


def test_run_store_roundtrip(tmp_path):
    result = _demo_result()
    directory = run_store.save_run(
        tmp_path,
        result.run_id,
        config={"run_id": result.run_id},
        result=result.to_dict(),
        trades=[r.__dict__ for r in result.trades],
        equity=[e.__dict__ for e in result.equity],
        strategy_source="# snapshot",
    )
    for name in ("config.json", "result.json", "trades.csv", "equity.csv", "strategy.py"):
        assert (directory / name).is_file()
    assert run_store.list_runs(tmp_path) == [result.run_id]
    assert run_store.load_config(tmp_path, result.run_id)["run_id"] == result.run_id
    reloaded = BacktestResult.from_dict(run_store.load_result(tmp_path, result.run_id))
    assert reloaded.run_id == result.run_id
    assert len(reloaded.trades) == len(result.trades)
