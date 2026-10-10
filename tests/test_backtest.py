import unittest

from tt import backtest as bt
from tt.data import synthetic
from tt.risk import RiskConfig
from tt.strategies import FLAT, LONG, SHORT, Signal, TrendBreakout, make_strategy
from tests.helpers import Scripted, bars

NO_LIMITS = RiskConfig(risk_per_trade=0.01, max_leverage=1.0, max_daily_loss=1, max_drawdown=1)


class BacktestTests(unittest.TestCase):
    def test_no_lookahead(self):
        """Running on a prefix gives the same equity path as the full run."""
        c = synthetic(1500)
        full = bt.backtest(c, TrendBreakout(), risk=NO_LIMITS)
        part = bt.backtest(c[:900], TrendBreakout(), risk=NO_LIMITS)
        # the prefix run force-closes on its last bar, so compare before it
        self.assertEqual(part.equity[:-1], full.equity[:len(part.equity) - 1])

    def test_fill_next_open_and_costs(self):
        c = bars([100, 100, 110, 120, 130])
        s = Scripted({1: Signal(LONG, 90), 3: Signal(FLAT)})
        free = bt.backtest(c, s, fee=0, slippage=0, risk=NO_LIMITS)
        t = free.trades[0]
        self.assertEqual((t.entry, t.exit, t.reason), (100, 120, "signal"))  # bar2 open, bar4 open
        self.assertAlmostEqual(t.qty, 1.0)    # risk 1% of 1000 / stop distance 10
        self.assertAlmostEqual(t.pnl, 20.0)
        paid = bt.backtest(c, Scripted({1: Signal(LONG, 90), 3: Signal(FLAT)}),
                           fee=0.001, slippage=0.001, risk=NO_LIMITS)
        self.assertLess(paid.trades[0].pnl, 20.0)

    def test_stop_gap_fills_at_open(self):
        c = bars([100, 100, 100, 80, 85])          # bar 3 opens at 100, trades down to 80
        c[3] = c[3].__class__(c[3].ts, 85, 86, 80, 82, 1)   # gap open below the 95 stop
        s = Scripted({1: Signal(LONG, 95)})
        r = bt.backtest(c, s, fee=0, slippage=0, risk=NO_LIMITS)
        self.assertEqual(r.trades[0].reason, "stop")
        self.assertEqual(r.trades[0].exit, 85)     # the open, worse than the stop

    def test_short_profit(self):
        c = bars([100, 100, 90, 80, 70])
        s = Scripted({1: Signal(SHORT, 110), 3: Signal(FLAT)})
        r = bt.backtest(c, s, fee=0, slippage=0, risk=NO_LIMITS)
        self.assertGreater(r.trades[0].pnl, 0)

    def test_stop_on_wrong_side_skips_entry(self):
        c = bars([100, 100, 100, 100])
        r = bt.backtest(c, Scripted({1: Signal(LONG, 105)}), risk=NO_LIMITS)
        self.assertEqual(r.trades, [])

    def test_drawdown_halt(self):
        prices = [100] * 3 + [100 - i for i in range(1, 60)]
        # every bar: go long with a stop 0.5 below, which the next bar always hits
        s = Scripted({i: Signal(LONG, p - 0.5) for i, p in enumerate(prices)}, hold=False)
        risk = RiskConfig(risk_per_trade=0.05, max_leverage=50, max_daily_loss=1, max_drawdown=0.2)
        r = bt.backtest(bars(prices), s, fee=0, slippage=0, risk=risk)
        self.assertIn("max drawdown", r.halted)
        self.assertGreater(r.end_equity, 1000 * 0.7)

    def test_metrics_and_tools_run(self):
        c = synthetic(1200)
        for name in ("trend", "ema", "rsi"):
            m = bt.backtest(c, make_strategy(name)).metrics()
            self.assertIn("sharpe", m)
        wf = bt.walk_forward(c, TrendBreakout, {"entry": [20, 55]}, folds=2)
        self.assertEqual(len(wf["folds"]), 2)
        mc = bt.monte_carlo(bt.backtest(c, TrendBreakout()).trades, 1000, runs=200)
        self.assertLessEqual(mc["dd_median"], mc["dd_95"])


if __name__ == "__main__":
    unittest.main()
