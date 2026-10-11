"""The website's live demo runs a JavaScript port of the trading engine
(site/js/engine.js). This checks it makes exactly the same trades as the
Python backtester. Skipped when Node.js isn't installed (e.g. on Termux)."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest

from tt import backtest as bt
from tt.data import load_csv, synthetic
from tt.risk import RiskConfig
from tt.strategies import EmaCross, Scalp, TrendBreakout

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NODE = shutil.which("node")

CASES = [
    # (strategy, python class, risk settings as in the site backtests / the demo)
    ("trend", TrendBreakout, {"riskPerTrade": 0.01, "maxLeverage": 1, "maxDailyLoss": 1, "maxDrawdown": 1}),
    ("ema", EmaCross, {"riskPerTrade": 0.01, "maxLeverage": 1, "maxDailyLoss": 1, "maxDrawdown": 1}),
    ("trend", TrendBreakout, {"riskPerTrade": 0.02, "maxLeverage": 1, "maxDailyLoss": 0.03, "maxDrawdown": 0.2}),
    ("ema", EmaCross, {"riskPerTrade": 0.02, "maxLeverage": 1, "maxDailyLoss": 0.03, "maxDrawdown": 0.2}),
    ("trend", TrendBreakout, {"riskPerTrade": 0.08, "maxLeverage": 3, "maxDailyLoss": 0.03, "maxDrawdown": 0.1}),
    ("scalp", Scalp, {"riskPerTrade": 0.02, "maxLeverage": 1, "maxDailyLoss": 1, "maxDrawdown": 1}),
    ("scalp", Scalp, {"riskPerTrade": 0.02, "maxLeverage": 1, "maxDailyLoss": 0.03, "maxDrawdown": 0.2}),
    ("scalp_break", lambda: Scalp(mode="break"), {"riskPerTrade": 0.02, "maxLeverage": 1, "maxDailyLoss": 1, "maxDrawdown": 1}),
]


def python_run(candles, cls, risk, start):
    cfg = RiskConfig(risk_per_trade=risk["riskPerTrade"], max_leverage=risk["maxLeverage"],
                     max_daily_loss=risk["maxDailyLoss"], max_drawdown=risk["maxDrawdown"])
    res = bt.backtest(candles, cls(), equity=10.0, risk=cfg, fee=0.001, slippage=0.0005, start=start)
    trades = [t for t in res.trades if t.reason != "end"]   # JS never force-closes
    return trades, res.equity[:-1], res.halted


@unittest.skipUnless(NODE, "node not installed")
class SiteEngineParity(unittest.TestCase):
    def compare(self, candles, start):
        fixture = {"candles": [[c.ts, c.open, c.high, c.low, c.close] for c in candles],
                   "cases": [{"strategy": name.split("_")[0], "params": {"mode": "break"} if name == "scalp_break" else {},
                              "cfg": {"equity": 10, "fee": 0.001, "slippage": 0.0005,
                                                         "start": candles[start].ts, "risk": risk}}
                             for name, _, risk in CASES]}
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(fixture, f)
        try:
            out = subprocess.run([NODE, os.path.join(ROOT, "tests/js/parity.mjs"), f.name],
                                 capture_output=True, text=True, check=True).stdout
        finally:
            os.unlink(f.name)
        js = json.loads(out)
        for (name, cls, risk), got in zip(CASES, js):
            with self.subTest(strategy=name, risk=risk):
                trades, equity, halted = python_run(candles, cls, risk, start)
                self.assertGreater(len(trades), 0)
                self.assertEqual(len(got["trades"]), len(trades))
                for t, g in zip(trades, got["trades"]):
                    self.assertEqual((t.entry_ts, t.exit_ts, t.reason), (g[0], g[3], g[5]))
                    for a, b in ((t.entry, g[1]), (t.qty, g[2]), (t.exit, g[4]), (t.pnl, g[6])):
                        self.assertAlmostEqual(a, b, delta=1e-9 * max(1.0, abs(a)))
                js_eq = got["equity"][:len(equity)]
                self.assertEqual(len(js_eq), len(equity))
                for (ts, e), (jts, je) in zip(equity, js_eq):
                    self.assertEqual(ts, jts)
                    self.assertAlmostEqual(e, je, delta=1e-9 * max(1.0, abs(e)))
                self.assertEqual(bool(halted), bool(got["halted"]))

    def test_synthetic(self):
        self.compare(synthetic(2500, seed=11), start=300)

    def test_real_btc_if_downloaded(self):
        path = os.path.join(ROOT, "data", "btc_4h.csv")
        if not os.path.exists(path):
            self.skipTest("no data/btc_4h.csv")
        self.compare(load_csv(path)[-4000:], start=300)

    def test_real_btc_5m_if_downloaded(self):
        path = os.path.join(ROOT, "data", "btc_5m.csv")
        if not os.path.exists(path):
            self.skipTest("no data/btc_5m.csv")
        self.compare(load_csv(path)[-6000:], start=300)


if __name__ == "__main__":
    unittest.main()
