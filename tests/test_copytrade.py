import os
import tempfile
import time
import unittest

from tt.broker import PaperBroker, save_json
from tt.copytrade import (CopyEngine, FeedLeader, FollowerConfig, plan_orders,
                          reduction_only, scale_targets)
from tt.notify import Notifier
from tt.risk import RiskConfig

PX = {"BTC": 50_000.0, "ETH": 2_500.0}


class SizingTests(unittest.TestCase):
    def test_scales_by_equity_and_multiplier(self):
        cfg = FollowerConfig("f", multiplier=2.0, max_leverage=10)
        t = scale_targets({"BTC": 0.2}, 10_000, 1_000, PX, cfg)
        self.assertAlmostEqual(t["BTC"], 0.04)

    def test_leverage_cap_and_whitelist(self):
        cfg = FollowerConfig("f", max_leverage=1.0, symbols=["BTC", "ETH"])
        t = scale_targets({"BTC": 1.0, "ETH": 8.0, "DOGE": 5}, 10_000, 10_000, PX, cfg)
        self.assertNotIn("DOGE", t)
        gross = sum(abs(q) * PX[s] for s, q in t.items())
        self.assertAlmostEqual(gross, 10_000)

    def test_no_shorts_on_long_only(self):
        t = scale_targets({"BTC": -0.1}, 1000, 1000, PX, FollowerConfig("f", allow_short=False))
        self.assertEqual(t["BTC"], 0.0)

    def test_plan_reduces_first_and_flips(self):
        orders = plan_orders({"BTC": 0.1, "ETH": -1.0}, {"BTC": 0.2, "ETH": 2.0}, PX, 10)
        self.assertEqual(orders[0], ("ETH", 1.0, True))      # close the short first
        self.assertEqual(set(orders[1:]), {("BTC", 0.1, False), ("ETH", 2.0, False)})

    def test_plan_skips_dust_but_always_closes(self):
        self.assertEqual(plan_orders({"BTC": 0.1}, {"BTC": 0.10001}, PX, 10), [])
        self.assertEqual(plan_orders({"BTC": 0.0001}, {}, PX, 10), [("BTC", -0.0001, True)])

    def test_reduction_only(self):
        self.assertEqual(reduction_only(1.0, 0.5), 0.5)
        self.assertEqual(reduction_only(1.0, 2.0), 1.0)
        self.assertEqual(reduction_only(-1.0, 0.0), 0.0)
        self.assertEqual(reduction_only(-1.0, 1.0), -1.0)
        self.assertEqual(reduction_only(0.0, 1.0), 0.0)


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.feed = os.path.join(self.dir, "leader.json")
        self.n = Notifier(os.path.join(self.dir, "log.txt"))
        self.n.termux = None
        self.n.tg_token = ""

    def publish(self, equity, positions, ts=None):
        save_json(self.feed, {"ts": ts or int(time.time() * 1000), "equity": equity,
                              "positions": positions})

    def engine(self, followers, dry_run=False):
        return CopyEngine(FeedLeader(self.feed), followers, lambda s: PX[s], self.n,
                          dry_run=dry_run, state_path=os.path.join(self.dir, "copy.json"))

    def test_mirror_then_leader_exits(self):
        f = PaperBroker(lambda s: PX[s], equity=1_000, fee=0, slippage=0)
        e = self.engine([(FollowerConfig("f", max_leverage=5, min_notional=1), f)])
        self.publish(10_000, {"BTC": 0.1, "ETH": -2.0})
        e.sync_once()
        self.assertAlmostEqual(f.position("BTC"), 0.01)
        self.assertAlmostEqual(f.position("ETH"), -0.2)
        self.publish(10_000, {})                      # leader closed everything
        e.sync_once()
        self.assertEqual(f.positions(), {})

    def test_dry_run_sends_nothing(self):
        f = PaperBroker(lambda s: PX[s], equity=1_000)
        self.publish(10_000, {"BTC": 0.1})
        orders = self.engine([(FollowerConfig("f", min_notional=1), f)], dry_run=True).sync_once()
        self.assertEqual(len(orders["f"]), 1)
        self.assertEqual(f.positions(), {})

    def test_stale_feed_is_refused(self):
        self.publish(10_000, {"BTC": 0.1}, ts=int(time.time() * 1000) - 3_600_000)
        with self.assertRaises(RuntimeError):
            FeedLeader(self.feed, max_age_s=900).snapshot()

    def test_halted_follower_flattens(self):
        px = dict(PX)
        f = PaperBroker(lambda s: px[s], equity=1_000, fee=0, slippage=0)
        cfg = FollowerConfig("f", max_leverage=5, min_notional=1,
                             risk=RiskConfig(max_drawdown=0.2, max_daily_loss=1.0))
        e = CopyEngine(FeedLeader(self.feed), [(cfg, f)], lambda s: px[s], self.n,
                       dry_run=False, state_path=os.path.join(self.dir, "c.json"))
        self.publish(1_000, {"BTC": 0.08})            # 4x leveraged long
        e.sync_once()
        px["BTC"] = 40_000                            # -20% move -> -80% equity
        e.sync_once()
        self.assertEqual(f.positions(), {})
        self.assertTrue(e.guards["f"].halted)


if __name__ == "__main__":
    unittest.main()
