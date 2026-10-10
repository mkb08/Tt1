import contextlib
import io
import sys
import types
import unittest
from unittest import mock

from tt import backtest as bt
from tt import bybit
from tt.broker import CCXTBroker, PaperBroker, exchange_leverage, make_broker
from tt.check import check_bybit_key, min_equity, Report
from tt.risk import RiskConfig
from tt.strategies import LONG, SHORT, Signal
from tests.helpers import H, Scripted, bars
from tests.test_risk_broker import FakeExchange

NO_LIMITS = RiskConfig(risk_per_trade=0.01, max_leverage=1.0, max_daily_loss=1, max_drawdown=1)


class PublicApiTests(unittest.TestCase):
    def test_split_symbol(self):
        self.assertEqual(bybit.split_symbol("BTC/USDT:USDT"), ("linear", "BTCUSDT"))
        self.assertEqual(bybit.split_symbol("eth/usdt"), ("spot", "ETHUSDT"))

    def test_klines_newest_first_is_reversed(self):
        rows = {"list": [["7200000", "2", "3", "1", "2.5", "10", "x"],
                         ["3600000", "1", "2", "0.5", "2", "5", "x"]]}
        with mock.patch.object(bybit, "_get", return_value=rows) as g:
            c = bybit.klines("BTC/USDT:USDT", "1h")
        self.assertEqual([x.ts for x in c], [3600000, 7200000])
        self.assertEqual(g.call_args[0][1]["interval"], "60")
        self.assertEqual(g.call_args[0][1]["category"], "linear")

    def test_history_pages_backwards(self):
        now = 100 * H
        calls = []

        def fake_klines(symbol, tf, end=None, limit=1000):
            calls.append(end)
            top = (now if end is None else end) // H
            return [c for c in bars([1.0] * 40, start=(top - 39) * H)]

        with mock.patch.object(bybit, "klines", side_effect=fake_klines), \
                mock.patch.object(bybit.time, "time", return_value=now / 1000), \
                mock.patch.object(bybit.time, "sleep"):
            out = bybit.history("BTC/USDT", "1h", days=90 * H / 86_400_000)
        self.assertEqual(len(calls), 3)
        self.assertEqual(out[0].ts, 10 * H)
        self.assertEqual(len({c.ts for c in out}), len(out))

    def test_ticker_and_instrument(self):
        t = {"list": [{"lastPrice": "83000.5", "fundingRate": "0.0001", "nextFundingTime": "1"}]}
        with mock.patch.object(bybit, "_get", return_value=t):
            self.assertEqual(bybit.ticker("BTC/USDT:USDT")["funding_rate"], 0.0001)
        info = {"list": [{"status": "Trading", "fundingInterval": 480,
                          "leverageFilter": {"maxLeverage": "100.00"},
                          "lotSizeFilter": {"minOrderQty": "0.001", "qtyStep": "0.001",
                                            "minNotionalValue": "5"}}]}
        with mock.patch.object(bybit, "_get", return_value=info):
            r = bybit.instrument("BTC/USDT:USDT")
        self.assertEqual((r["min_qty"], r["min_notional"], r["funding_interval_h"]), (0.001, 5.0, 8))

    def test_funding_history_sorted_and_paged(self):
        now = 10 * 8 * H
        def fake_get(path, params):
            end = params["endTime"]
            rows = [{"fundingRateTimestamp": str(t), "fundingRate": "0.0001"}
                    for t in range(end - end % (8 * H), max(end - 3 * 8 * H, -1), -8 * H)]
            return {"list": rows}
        with mock.patch.object(bybit, "_get", side_effect=fake_get), \
                mock.patch.object(bybit.time, "time", return_value=now / 1000), \
                mock.patch.object(bybit.time, "sleep"):
            rows = bybit.funding_history("BTC/USDT:USDT", days=5 * 8 * H / 86_400_000)
        stamps = [t for t, _ in rows]
        self.assertEqual(stamps, sorted(stamps))
        self.assertEqual(stamps[0], 5 * 8 * H)
        self.assertEqual(stamps[-1], now)


class FundingTests(unittest.TestCase):
    def test_constant_funding_charges_longs_and_pays_shorts(self):
        c = bars([100.0] * 12)   # flat price: only fees and funding move equity
        long_ = bt.backtest(c, Scripted({1: Signal(LONG, 90)}), fee=0, slippage=0,
                            funding_8h=0.0008, risk=NO_LIMITS)
        short = bt.backtest(c, Scripted({1: Signal(SHORT, 110)}), fee=0, slippage=0,
                            funding_8h=0.0008, risk=NO_LIMITS)
        t = long_.trades[0]
        # held from bar 2 to bar 11 = 10 one-hour bars = 10/8 of a funding period
        self.assertAlmostEqual(t.funding, 1.0 * 100 * 0.0008 * 10 / 8)
        self.assertAlmostEqual(long_.end_equity, 1000 - t.funding)
        self.assertAlmostEqual(short.end_equity, 1000 + t.funding)

    def test_funding_events(self):
        c = bars([100.0] * 12)
        events = [(0, 0.5), (3 * H, 0.001), (3 * H + 1, 0.001), (20 * H, 0.5)]
        r = bt.backtest(c, Scripted({1: Signal(LONG, 90)}), fee=0, slippage=0,
                        funding_events=events, risk=NO_LIMITS)
        self.assertAlmostEqual(r.trades[0].funding, 1.0 * 100 * 0.002)

    def test_paper_broker_accrues_funding(self):
        now = [0.0]
        b = PaperBroker(lambda s: 100.0, equity=1000, fee=0, slippage=0,
                        funding_8h=0.0001, clock=lambda: now[0])
        b.market_order("X", 2)
        now[0] += 8 * 3600
        self.assertAlmostEqual(b.equity(), 1000 - 2 * 100 * 0.0001)
        b.market_order("X", -4)                         # flip to short 2
        now[0] += 8 * 3600
        b.equity()
        self.assertAlmostEqual(b.funding_paid, 0.02 - 0.02)   # paid as long, received as short


class BybitBrokerTests(unittest.TestCase):
    def test_swap_setup_one_way_and_leverage(self):
        ex = FakeExchange()
        ex.calls = []
        ex.set_position_mode = lambda hedged, s: ex.calls.append(("mode", hedged, s))
        def set_lev(lev, s):
            raise RuntimeError("leverage not modified")
        ex.set_leverage = set_lev
        b = CCXTBroker("bybit", market_type="swap", symbols=["BTC/USDT:USDT"],
                       exchange=ex, leverage=2)
        self.assertEqual(ex.calls, [("mode", False, "BTC/USDT:USDT")])
        self.assertIn("leverage not modified", b.setup_notes[0])

    def test_demo_mode_uses_demo_trading(self):
        made = []

        class FakeBybit(FakeExchange):
            def __init__(self, opts):
                super().__init__()
                self.opts, self.demo, self.sandbox = opts, False, False
                made.append(self)

            def enable_demo_trading(self, on):
                self.demo = on

            def set_sandbox_mode(self, on):
                self.sandbox = on

        fake_ccxt = types.SimpleNamespace(bybit=FakeBybit)
        cfg = {"exchange": "bybit", "mode": "demo", "market_type": "spot", "symbol": "BTC/USDT",
               "api_key_env": "K", "secret_env": "S"}
        with mock.patch.dict(sys.modules, {"ccxt": fake_ccxt}), \
                mock.patch.dict("os.environ", {"K": "k", "S": "s"}):
            make_broker(cfg)
        self.assertTrue(made[0].demo)
        self.assertFalse(made[0].sandbox)
        self.assertEqual(made[0].opts["options"]["defaultType"], "spot")

    def test_exchange_leverage(self):
        self.assertEqual(exchange_leverage({"risk": {"max_leverage": 1.0}}), 2)
        self.assertEqual(exchange_leverage({"max_leverage": 2.5}), 4)
        self.assertEqual(exchange_leverage({"exchange_leverage": 5}), 5)

    def test_paper_uses_configured_price_source(self):
        with mock.patch("tt.data.fetch_price", return_value=50.0) as fp:
            b = make_broker({"mode": "paper", "exchange": "bybit", "market_type": "swap",
                             "funding_8h": 0.0001})
            self.assertEqual(b.price("BTC/USDT:USDT"), 50.0)
        fp.assert_called_with("BTC/USDT:USDT", "bybit")
        self.assertTrue(b.allow_short)
        self.assertEqual(b.funding_8h, 0.0001)


class CheckTests(unittest.TestCase):
    def test_min_equity(self):
        # BTC perp: 0.001 BTC min at 80k = 80 USDT order; 1% risk / 4% stop = 25% of equity
        order, need = min_equity(80_000, 0.04, {"min_qty": 0.001, "min_notional": 5},
                                 RiskConfig(risk_per_trade=0.01, max_leverage=1))
        self.assertAlmostEqual(order, 80)
        self.assertAlmostEqual(need, 320)

    def run_key_check(self, result, trading=True):
        ex = types.SimpleNamespace(private_get_v5_user_query_api=lambda: {"result": result})
        rep = Report()
        with contextlib.redirect_stdout(io.StringIO()) as out:
            check_bybit_key(ex, rep, trading)
        return rep.problems, out.getvalue()

    def test_key_with_withdraw_fails(self):
        problems, out = self.run_key_check({"readOnly": 0, "uta": 1, "ips": ["1.2.3.4"],
                                            "permissions": {"Wallet": ["AccountTransfer", "Withdraw"]}})
        self.assertEqual(problems, 1)
        self.assertIn("WITHDRAW", out)

    def test_safe_key_without_ip_warns_expiry(self):
        problems, out = self.run_key_check({"readOnly": 0, "uta": 1, "ips": [], "deadlineDay": 42,
                                            "permissions": {"ContractTrade": ["Order", "Position"]}})
        self.assertEqual(problems, 0)
        self.assertIn("42 days", out)

    def test_read_only_key_cannot_trade(self):
        problems, _ = self.run_key_check({"readOnly": 1, "uta": 1, "ips": ["1.1.1.1"], "permissions": {}})
        self.assertEqual(problems, 1)
        problems, _ = self.run_key_check({"readOnly": 1, "uta": 1, "ips": ["1.1.1.1"], "permissions": {}},
                                         trading=False)
        self.assertEqual(problems, 0)


if __name__ == "__main__":
    unittest.main()
