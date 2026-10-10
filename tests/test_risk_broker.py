import unittest

from tt.broker import CCXTBroker, PaperBroker
from tt.risk import DAY_MS, RiskConfig, RiskGuard, position_size


class RiskTests(unittest.TestCase):
    def test_position_size(self):
        cfg = RiskConfig(risk_per_trade=0.01, max_leverage=1.0)
        self.assertAlmostEqual(position_size(1000, 100, 90, cfg), 1.0)
        self.assertAlmostEqual(position_size(1000, 100, 99.9, cfg), 10.0)  # leverage cap
        self.assertEqual(position_size(1000, 100, None, cfg), 0.0)
        self.assertAlmostEqual(position_size(1000, 100, 90, cfg, qty_step=0.3), 0.9)

    def test_daily_loss_resets_next_day(self):
        g = RiskGuard(RiskConfig(max_daily_loss=0.03, max_drawdown=0.5))
        g.update(1000, 0)
        g.update(960, 1000)
        self.assertFalse(g.allow_entries(960)[0])
        g.update(960, DAY_MS + 1)
        self.assertTrue(g.allow_entries(960)[0])

    def test_drawdown_latches(self):
        g = RiskGuard(RiskConfig(max_drawdown=0.2))
        g.update(1000, 0)
        g.update(790, 1)
        g.update(1000, 2)
        self.assertFalse(g.allow_entries(1000)[0])
        self.assertTrue(RiskGuard(g.cfg, g.state()).halted)  # survives restart


class PaperBrokerTests(unittest.TestCase):
    def setUp(self):
        self.px = {"X": 100.0}
        self.b = PaperBroker(lambda s: self.px[s], equity=1000, fee=0, slippage=0)

    def test_long_round_trip(self):
        self.b.market_order("X", 2)
        self.px["X"] = 110
        self.assertAlmostEqual(self.b.equity(), 1020)
        self.b.market_order("X", -1)
        self.assertAlmostEqual(self.b.cash, 1010)
        self.b.market_order("X", -5, reduce_only=True)   # clamps to the remaining 1
        self.assertEqual(self.b.position("X"), 0)
        self.assertAlmostEqual(self.b.equity(), 1020)

    def test_flip_and_short(self):
        self.b.market_order("X", 1)
        self.b.market_order("X", -3)                     # flip to -2 at 100
        self.assertEqual(self.b.position("X"), -2)
        self.px["X"] = 90
        self.assertAlmostEqual(self.b.equity(), 1020)

    def test_long_only(self):
        b = PaperBroker(lambda s: 100.0, allow_short=False)
        with self.assertRaises(ValueError):
            b.market_order("X", -1)


class FakeExchange:
    """Implements the slice of the ccxt API that CCXTBroker uses."""

    def __init__(self):
        self.orders = []

    def load_markets(self):
        pass

    def market(self, s):
        return {"base": "BTC", "contractSize": 0.001,
                "limits": {"amount": {"min": 1}, "cost": {"min": 5}}}

    def fetch_ticker(self, s):
        return {"last": 50_000.0}

    def fetch_balance(self):
        return {"total": {"USDT": 1000.0, "BTC": 0.0}}

    def fetch_positions(self, symbols=None):
        return [{"symbol": "BTC/USDT:USDT", "contracts": 20, "contractSize": 0.001,
                 "side": "short", "unrealizedPnl": -12.5}]

    def amount_to_precision(self, s, amount):
        return str(int(amount))  # whole contracts

    def create_order(self, symbol, type_, side, amount, price, params):
        self.orders.append((symbol, type_, side, amount, params))
        return {"id": "1"}


class CCXTBrokerTests(unittest.TestCase):
    def test_swap_positions_and_orders(self):
        ex = FakeExchange()
        b = CCXTBroker("fake", market_type="swap", symbols=["BTC/USDT:USDT"], exchange=ex)
        self.assertAlmostEqual(b.position("BTC/USDT:USDT"), -0.02)
        self.assertAlmostEqual(b.equity(), 987.5)
        self.assertAlmostEqual(b.round_qty("BTC/USDT:USDT", 0.0157), 0.015)
        b.market_order("BTC/USDT:USDT", 0.02, reduce_only=True)
        self.assertEqual(ex.orders[-1], ("BTC/USDT:USDT", "market", "buy", 20.0, {"reduceOnly": True}))
        self.assertEqual(b.market_order("BTC/USDT:USDT", 0.0004), {"skipped": "rounds to zero"})
        self.assertAlmostEqual(b.min_notional("BTC/USDT:USDT"), 50.0)


if __name__ == "__main__":
    unittest.main()
