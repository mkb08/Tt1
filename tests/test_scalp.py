import os
import statistics
import tempfile
import unittest

from tt import backtest as bt
from tt import indicators as ind
from tt.broker import PaperBroker
from tt.data import Candle
from tt.live import Bot
from tt.notify import Notifier
from tt.risk import RiskConfig
from tt.strategies import FLAT, LONG, SHORT, Scalp, Signal
from tests.helpers import H, Scripted, bars

NO_LIMITS = RiskConfig(risk_per_trade=0.01, max_leverage=1.0, max_daily_loss=1, max_drawdown=1)


class TakeProfitTests(unittest.TestCase):
    def test_target_fills_at_target(self):
        c = bars([100, 100, 100, 104, 111, 112])
        r = bt.backtest(c, Scripted({1: Signal(LONG, 95, 110)}), fee=0, slippage=0, risk=NO_LIMITS)
        t = r.trades[0]
        self.assertEqual((t.reason, t.exit), ("target", 110))

    def test_gap_through_target_fills_at_open(self):
        c = bars([100, 100, 100, 104, 112])
        c[4] = Candle(c[4].ts, 115, 116, 114, 115, 1)   # opens above the 110 target
        r = bt.backtest(c, Scripted({1: Signal(LONG, 95, 110)}), fee=0, slippage=0, risk=NO_LIMITS)
        self.assertEqual((r.trades[0].reason, r.trades[0].exit), ("target", 115))

    def test_bar_touching_stop_and_target_counts_as_stop(self):
        c = bars([100, 100, 100, 100])
        c[3] = Candle(c[3].ts, 100, 111, 94, 100, 1)
        r = bt.backtest(c, Scripted({1: Signal(LONG, 95, 110)}), fee=0, slippage=0, risk=NO_LIMITS)
        self.assertEqual(r.trades[0].reason, "stop")

    def test_short_target(self):
        c = bars([100, 100, 100, 95, 89, 88])
        r = bt.backtest(c, Scripted({1: Signal(SHORT, 105, 90)}), fee=0, slippage=0, risk=NO_LIMITS)
        self.assertEqual((r.trades[0].reason, r.trades[0].exit), ("target", 90))
        self.assertGreater(r.trades[0].pnl, 0)

    def test_cost_metrics(self):
        c = bars([100, 100, 100, 104, 111, 112])
        m = bt.backtest(c, Scripted({1: Signal(LONG, 95, 110)}), fee=0.001, slippage=0,
                        risk=NO_LIMITS).metrics()
        self.assertAlmostEqual(m["avg_gross"], 0.10)
        self.assertAlmostEqual(m["avg_cost"], 0.001 + 0.001 * 1.1)
        self.assertAlmostEqual(m["avg_net"], m["avg_gross"] - m["avg_cost"])


class ScalpTests(unittest.TestCase):
    def test_rolling_std_matches_statistics(self):
        vals = [83000 + (i * 37 % 101) - 50 + i * 0.3 for i in range(300)]
        sd = ind.rolling_std(vals, 20)
        self.assertIsNone(sd[18])
        for i in (19, 150, 299):
            self.assertAlmostEqual(sd[i], statistics.pstdev(vals[i - 19:i + 1]), places=6)

    def uptrend_then(self, last_close):
        prices = [100 + i * 0.05 + (0.3 if i % 2 else -0.3) for i in range(150)]
        return bars(prices + [last_close], spread=0.2)

    def test_bounce_buys_a_dip_in_an_uptrend(self):
        c = self.uptrend_then(105.5)      # sharp dip below the lower band, still above the trend EMA
        s = Scalp(mode="bounce", trend_n=100)
        s.prepare(c)
        sig = s.signal(len(c) - 1, FLAT)
        self.assertEqual(sig.side, LONG)
        self.assertLess(sig.stop, c[-1].close)
        self.assertGreater(sig.take, c[-1].close)

    def test_break_buys_a_new_high(self):
        c = self.uptrend_then(110)
        s = Scalp(mode="break", trend_n=100)
        s.prepare(c)
        self.assertEqual(s.signal(len(c) - 1, FLAT).side, LONG)
        s2 = Scalp(mode="bounce", trend_n=100)
        s2.prepare(c)
        self.assertEqual(s2.signal(len(c) - 1, FLAT).side, FLAT)

    def test_time_limit_and_no_shorts_on_spot(self):
        s = Scalp(max_bars=6)
        s.prepare(self.uptrend_then(105.5))
        self.assertEqual(s.signal(140, LONG, bars_held=5).side, LONG)
        self.assertEqual(s.signal(140, LONG, bars_held=6).side, FLAT)
        with self.assertRaises(ValueError):
            Scalp(mode="yolo")


class BotTargetTests(unittest.TestCase):
    def test_bot_takes_profit(self):
        d = tempfile.mkdtemp()
        px = {"BTC/USDT": 100.0}
        broker = PaperBroker(lambda s: px[s], equity=1000, fee=0, slippage=0)
        n = Notifier(os.path.join(d, "log.txt"))
        n.termux, n.tg_token = None, ""
        cfg = {"symbol": "BTC/USDT", "timeframe": "1h", "strategy": "scalp", "market_type": "spot",
               "state_file": os.path.join(d, "bot.json"), "risk": {"risk_per_trade": 0.01}}
        bot = Bot(cfg, broker, n, Scripted({4: Signal(LONG, 95, 103)}))
        bot.step(bars([100] * 5), now_ms=5 * H)
        self.assertGreater(broker.position("BTC/USDT"), 0)
        px["BTC/USDT"] = 103.5
        bot.step(bars([100] * 5), now_ms=5 * H + 1)
        self.assertEqual(broker.position("BTC/USDT"), 0)
        self.assertIsNone(bot.state["take"])


if __name__ == "__main__":
    unittest.main()
