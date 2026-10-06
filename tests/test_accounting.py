"""Tests for the account / trading-economics layer (``src.accounting``).

Pure math tests use synthetic trade records; integration tests run the existing
single-run pipeline with the deterministic ``threshold_direction`` fixture and a
small synthetic tick CSV (no ``data/`` dependency).
"""

import json
import math
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import accounting, run_request, run_backtest  # noqa: E402

THRESHOLD = ROOT / "tests" / "fixtures" / "strategies" / "threshold_direction.py"
USD = accounting.AccountModel("USD", 10000.0, 1.0, 7.0)
GOLD = accounting.InstrumentSpec("XAUUSD", "USD", 100.0, 0.01)


def _trade(tid, pnl, exit_time=None):
    return {"trade_id": tid, "pnl_price": pnl, "exit_time": exit_time or f"2025-01-01T00:0{tid}:00Z"}


class AccountMathTest(unittest.TestCase):
    def test_winning_and_losing_long_and_short(self):
        self.assertEqual(accounting.trade_account_pnl(5.0, GOLD, USD), (500.0, 7.0, 493.0))
        self.assertEqual(accounting.trade_account_pnl(-5.0, GOLD, USD), (-500.0, 7.0, -507.0))
        # pnl_price already carries direction sign (short profit is positive)
        self.assertEqual(accounting.trade_account_pnl(3.0, GOLD, USD), (300.0, 7.0, 293.0))

    def test_zero_pnl_only_loses_commission(self):
        gross, commission, net = accounting.trade_account_pnl(0.0, GOLD, USD)
        self.assertEqual((gross, commission, net), (0.0, 7.0, -7.0))

    def test_contract_size_application(self):
        small = accounting.InstrumentSpec("XAUUSD", "USD", 10.0)
        self.assertEqual(accounting.trade_account_pnl(5.0, small, USD), (50.0, 7.0, 43.0))

    def test_commission_is_per_lot_round_turn(self):
        two_lots = accounting.AccountModel("USD", 10000.0, 2.0, 7.0)
        gross, commission, net = accounting.trade_account_pnl(5.0, GOLD, two_lots)
        self.assertEqual(gross, 5.0 * 100.0 * 2.0)
        self.assertEqual(commission, 14.0)  # 7 per lot, applied once per trade
        self.assertEqual(net, gross - 14.0)

    def test_spread_is_not_double_counted(self):
        # gross is exactly pnl_price * contract * lots; no spread term exists.
        gross, _, _ = accounting.trade_account_pnl(2.5, GOLD, USD)
        self.assertEqual(gross, 2.5 * 100.0 * 1.0)

    def test_incompatible_currency_is_unavailable(self):
        eur = accounting.AccountModel("EUR", 10000.0, 1.0, 0.0)
        report = accounting.build_account_report([_trade(1, 5.0)], eur, GOLD)
        self.assertFalse(report["available"])
        self.assertIn("FX conversion unavailable", report["reason_unavailable"])
        self.assertIsNone(report["metrics"])


class BalanceTest(unittest.TestCase):
    def test_balance_sequence_and_ending(self):
        trades = [_trade(1, 5.0), _trade(2, -5.0), _trade(3, 0.0)]
        report = accounting.build_account_report(trades, USD, GOLD)
        self.assertTrue(report["available"])
        rows = report["trades"]
        self.assertEqual(rows[0]["net_account_pnl"], 493.0)
        self.assertEqual(rows[0]["balance_after"], 10493.0)
        self.assertEqual(rows[1]["net_account_pnl"], -507.0)
        self.assertEqual(rows[1]["balance_after"], 9986.0)
        self.assertEqual(rows[2]["balance_after"], 9979.0)
        self.assertEqual(report["metrics"]["starting_balance"], 10000.0)
        self.assertEqual(report["metrics"]["ending_balance"], 9979.0)
        self.assertEqual([p["realized_balance"] for p in report["balance_curve"]],
                         [10493.0, 9986.0, 9979.0])

    def test_realized_drawdown_absolute_and_percent(self):
        # 10000 -> +493 = 10493 (peak) -> -507 = 9986 -> -507 = 9479
        trades = [_trade(1, 5.0), _trade(2, -5.0), _trade(3, -5.0)]
        report = accounting.build_account_report(trades, USD, GOLD)
        m = report["metrics"]
        self.assertEqual(m["max_drawdown"], 10493.0 - 9479.0)
        self.assertAlmostEqual(m["max_drawdown_pct"], (m["max_drawdown"] / 10493.0) * 100.0, places=4)
        self.assertAlmostEqual(m["recovery_factor"], m["net_pnl"] / m["max_drawdown"], places=4)

    def test_unauthorized_drawdown_metrics_stay_none_when_no_loss(self):
        report = accounting.build_account_report([_trade(1, 5.0)], USD, GOLD)
        self.assertEqual(report["metrics"]["max_drawdown"], 0.0)
        self.assertEqual(report["metrics"]["max_drawdown_pct"], 0.0)
        self.assertIsNone(report["metrics"]["recovery_factor"])


def _write_ticks(path: Path) -> None:
    start = pd.Timestamp("2025-01-02T00:00:00Z")
    rows = []
    for i in range(240):
        t = start + pd.Timedelta(minutes=i)
        price = 2600.0 + 40.0 * math.sin(i / 7.0)
        rows.append({"timestamp": t, "bid": round(price, 2), "ask": round(price + 0.5, 2)})
    pd.DataFrame(rows).to_csv(path, index=False)


class AccountIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.data = self.root / "ticks.csv"
        _write_ticks(self.data)

    def tearDown(self):
        self.tmp.cleanup()

    def _request(self, account=None, instrument=None):
        return run_request.BacktestRunRequest(
            strategy=run_request.StrategyRequest(str(THRESHOLD), {"side": "both", "threshold": 0.0}),
            market=run_request.MarketRequest("XAUUSD", "15min", "2025-01-02", "2025-01-02", str(self.data)),
            execution=run_request.ExecutionRequest(5, 10),
            account=account,
            instrument=instrument,
        )

    def test_engine_price_term_unchanged_by_account_layer(self):
        plain = run_backtest.execute_run(self._request(), out=str(self.root / "p1"), report=False, public=str(self.root / "pub"))
        acct = run_backtest.execute_run(
            self._request(USD, GOLD), out=str(self.root / "p2"), report=False, public=str(self.root / "pub"))
        self.assertEqual(plain["result"].metrics, acct["result"].metrics)
        self.assertEqual(
            [(t.trade_id, t.entry_price, t.exit_price, t.pnl_price) for t in plain["result"].trades],
            [(t.trade_id, t.entry_price, t.exit_price, t.pnl_price) for t in acct["result"].trades],
        )
        self.assertIsNone(plain["result"].account)
        self.assertTrue(acct["result"].account["available"])

    def test_archived_config_and_result_carry_account(self):
        info = run_backtest.execute_run(
            self._request(USD, GOLD), out=str(self.root / "runs"), report=True, public=str(self.root / "pub"))
        run_dir = Path(info["directory"])
        config = json.loads((run_dir / "config.json").read_text(encoding="utf-8"))
        result = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
        self.assertEqual(config["account"]["account_currency"], "USD")
        self.assertEqual(config["account"]["commission_per_lot_round_turn"], 7.0)
        self.assertEqual(config["instrument"]["contract_size"], 100.0)
        self.assertTrue(result["account"]["available"])
        self.assertEqual(result["account"]["metrics"]["total_commission"],
                         round(7.0 * result["metrics"]["total_trades"], 6))
        # report renders account sections
        report = (run_dir / "report.html").read_text(encoding="utf-8")
        self.assertIn("Account configuration", report)
        self.assertIn("Instrument economics", report)
        self.assertIn("Realized account balance", report)

    def test_no_account_run_has_null_account_and_old_config_loads(self):
        info = run_backtest.execute_run(self._request(), out=str(self.root / "runs2"), report=True, public=str(self.root / "pub"))
        result = json.loads((Path(info["directory"]) / "result.json").read_text(encoding="utf-8"))
        self.assertIsNone(result["account"])
        # legacy config without account/instrument parses and builds a request fine
        req = run_request.parse_request_payload({
            "strategy": {"selector": "candle_direction"},
            "market": {"symbol": "XAUUSD", "data_source": "x.csv", "start": "2025-01-01", "end": "2025-01-02"},
        })
        self.assertIsNone(req.account)
        self.assertIsNone(req.instrument)

    def test_account_payload_validation(self):
        base = {
            "strategy": {"selector": "candle_direction"},
            "market": {"symbol": "XAUUSD", "data_source": "x.csv", "start": "2025-01-01", "end": "2025-01-02"},
        }
        req = run_request.parse_request_payload({
            **base,
            "account": {"account_currency": "USD", "starting_balance": 5000, "position_size_lots": 0.5,
                        "commission_per_lot_round_turn": 3.5},
            "instrument": {"symbol": "XAUUSD", "quote_currency": "USD", "contract_size": 100},
        })
        self.assertEqual(req.account.starting_balance, 5000.0)
        self.assertEqual(req.instrument.contract_size, 100.0)
        with self.assertRaises(run_request.InvalidParametersError):
            run_request.parse_request_payload({**base, "account": {"starting_balance": -1}})
        with self.assertRaises(run_request.InvalidParametersError):
            run_request.parse_request_payload({**base, "instrument": {"symbol": "XAUUSD"}})
        with self.assertRaises(run_request.InvalidParametersError):
            run_request.parse_request_payload({**base, "account": {"unknown": 1}})

    def test_explicit_instrument_overrides_catalog_and_catalog_fallback(self):
        # explicit spec wins (contract size 10 not the catalog 100)
        info = run_backtest.execute_run(
            self._request(USD, accounting.InstrumentSpec("XAUUSD", "USD", 10.0)),
            out=str(self.root / "runs3"), report=False, public=str(self.root / "pub"))
        self.assertEqual(info["result"].account["instrument"]["contract_size"], 10.0)
        # no explicit instrument -> catalog resolves by symbol
        info2 = run_backtest.execute_run(
            self._request(USD, None), out=str(self.root / "runs4"), report=False, public=str(self.root / "pub"))
        self.assertEqual(info2["result"].account["instrument"]["contract_size"], 100.0)

    def test_missing_instrument_is_unavailable(self):
        # symbol not in catalog and no explicit spec -> unavailable with reason
        req = self._request(USD, None)
        req.market.symbol = "ZZZUNKNOWN"
        info = run_backtest.execute_run(req, out=str(self.root / "runs6"), report=False, public=str(self.root / "pub"))
        self.assertFalse(info["result"].account["available"])
        self.assertIn("no instrument economics", info["result"].account["reason_unavailable"])


if __name__ == "__main__":
    unittest.main()
