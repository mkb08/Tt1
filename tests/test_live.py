import os
import tempfile
import unittest

from tt.broker import PaperBroker
from tt.live import Bot
from tt.notify import Notifier
from tt.strategies import FLAT, LONG, Signal
from tests.helpers import H, Scripted, bars


class BotTests(unittest.TestCase):
    def setUp(self):
        d = tempfile.mkdtemp()
        self.px = {"BTC/USDT": 100.0}
        self.broker = PaperBroker(lambda s: self.px[s], equity=1000, fee=0, slippage=0)
        self.cfg = {"symbol": "BTC/USDT", "timeframe": "1h", "strategy": "trend",
                    "market_type": "spot", "state_file": os.path.join(d, "bot.json"),
                    "publish_file": os.path.join(d, "leader.json"),
                    "risk": {"risk_per_trade": 0.01, "max_leverage": 1.0}}
        self.n = Notifier(os.path.join(d, "log.txt"))
        self.n.termux, self.n.tg_token = None, ""

    def bot(self, script):
        return Bot(self.cfg, self.broker, self.n, Scripted(script))

    def test_open_trail_stop_out(self):
        c = bars([100] * 5)
        bot = self.bot({4: Signal(LONG, 90), 5: Signal(LONG, 95)})
        bot.step(c, now_ms=5 * H)
        self.assertAlmostEqual(self.broker.position("BTC/USDT"), 1.0)
        bot.step(c, now_ms=5 * H)                       # same bar again: no new action
        self.assertAlmostEqual(self.broker.position("BTC/USDT"), 1.0)
        bot.step(bars([100] * 6), now_ms=6 * H)         # new bar trails the stop to 95
        self.assertEqual(bot.state["stop"], 95)
        self.px["BTC/USDT"] = 94
        bot.step(bars([100] * 6), now_ms=6 * H + 1)     # intrabar poll hits the stop
        self.assertEqual(self.broker.position("BTC/USDT"), 0)
        self.assertEqual(bot.state["side"], FLAT)

    def test_short_ignored_on_spot_and_restart_safe(self):
        bot = self.bot({4: Signal(-1, 110)})
        bot.step(bars([100] * 5), now_ms=5 * H)
        self.assertEqual(self.broker.position("BTC/USDT"), 0)
        again = self.bot({})
        self.assertEqual(again.state["last_bar_ts"], 4 * H)

    def test_reconciles_manual_close(self):
        bot = self.bot({4: Signal(LONG, 90)})
        bot.step(bars([100] * 5), now_ms=5 * H)
        self.broker.market_order("BTC/USDT", -1.0)      # closed by hand on the exchange
        bot.step(bars([100] * 5), now_ms=5 * H + 1)
        self.assertEqual(bot.state["side"], FLAT)
        self.assertIsNone(bot.state["stop"])


if __name__ == "__main__":
    unittest.main()
