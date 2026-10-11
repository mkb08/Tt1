"""Regenerate site/data/results.json from the backtester.

Needs the 4h CSVs from `python -m tt fetch` in data/ (btc_4h.csv, eth_4h.csv,
sol_4h.csv). Run from the repo root:  python scripts/build_site.py
Uses the same settings as the README tables, so the site and docs agree.

If 5-minute CSVs are present too (btc_5m.csv, ...), it also writes
site/data/scalp.json: the scalper's backtest at the live demo's costs.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tt import backtest as bt  # noqa: E402
from tt.__main__ import GRIDS  # noqa: E402
from tt.data import load_csv  # noqa: E402
from tt.risk import RiskConfig  # noqa: E402
from tt.strategies import STRATEGIES, EmaCross, Scalp, TrendBreakout  # noqa: E402

DAY = 86_400_000
RISK = RiskConfig(risk_per_trade=0.01, max_leverage=1.0, max_daily_loss=1.0, max_drawdown=1.0)
SPOT = {"equity": 1000.0, "fee": 0.001, "slippage": 0.0005, "risk": RISK}
PERP = {"equity": 1000.0, "fee": 0.00055, "slippage": 0.0005, "funding_8h": 0.0001, "risk": RISK}
KEEP = ("trades", "total_return", "cagr", "max_drawdown", "sharpe", "win_rate",
        "profit_factor", "exposure", "funding_paid")


def daily(points: list[tuple[int, float]]) -> list[list]:
    """First equity value of each UTC day, as [day number, value]."""
    out, last = [], None
    for ts, v in points:
        d = ts // DAY
        if d != last:
            out.append([d, round(v, 2)])
            last = d
    return out


def metrics(m: dict) -> dict:
    return {k: (round(m[k], 4) if isinstance(m[k], float) else m[k]) for k in KEEP}


def market(path: str) -> dict:
    c = load_csv(path)
    out = {"curves": {}, "metrics": {}, "walkforward": {}, "perp": {}}
    for name in ("trend", "ema", "rsi"):
        res = bt.backtest(c, STRATEGIES[name](), **SPOT)
        m = res.metrics()
        out["metrics"][name] = metrics(m)
        if name != "rsi":
            out["curves"][name] = daily(res.equity)
        if name == "trend":  # same buy & hold baseline as `tt compare`
            first = next(i for i, x in enumerate(c) if x.ts == res.equity[0][0])
            base = c[first].open
            out["curves"]["bh"] = daily([(x.ts, 1000 * x.close / base) for x in c[first:]])
            out["metrics"]["bh"] = {"total_return": round(m["buy_hold_return"], 4),
                                    "max_drawdown": round(m["buy_hold_mdd"], 4)}
    for name in ("trend", "ema", "rsi"):
        wf = bt.walk_forward(c, STRATEGIES[name], GRIDS[name], 4, **SPOT)
        out["walkforward"][name] = {
            "compounded": round(wf["oos_compounded_return"], 4),
            "profitable": wf["oos_profitable_folds"], "folds": len(wf["folds"])}
    for label, strat in (("trend_long", TrendBreakout()), ("trend_ls", TrendBreakout(allow_short=True)),
                         ("ema_long", EmaCross()), ("ema_ls", EmaCross(allow_short=True))):
        out["perp"][label] = metrics(bt.backtest(c, strat, **PERP).metrics())
    out["start"] = dt.datetime.fromtimestamp(c[0].ts / 1000, dt.timezone.utc).date().isoformat()
    out["end"] = dt.datetime.fromtimestamp(c[-1].ts / 1000, dt.timezone.utc).date().isoformat()
    return out


def scalp_report() -> dict | None:
    """Scalper (bounce) on 5-minute candles at the "Your bot" demo's costs:
    Bybit spot taker fee 0.1% and 0.01% slippage per fill; and at a 0.02%
    limit-order fee for comparison. Risk limits off, to show the raw strategy."""
    out = {}
    risk = RiskConfig(risk_per_trade=0.02, max_leverage=1.0, max_daily_loss=1.0, max_drawdown=1.0)
    for sym in ("btc", "eth", "sol"):
        path = f"data/{sym}_5m.csv"
        if not os.path.exists(path):
            return None
        c = load_csv(path)
        row = {"start": dt.datetime.fromtimestamp(c[0].ts / 1000, dt.timezone.utc).date().isoformat(),
               "end": dt.datetime.fromtimestamp(c[-1].ts / 1000, dt.timezone.utc).date().isoformat()}
        for label, fee, slip in (("spot", 0.001, 0.0001), ("limit", 0.0002, 0.0)):
            m = bt.backtest(c, Scalp(), equity=10.0, risk=risk, fee=fee, slippage=slip).metrics()
            row[label] = {k: round(m[k], 6) for k in ("total_return", "max_drawdown", "win_rate", "avg_gross", "avg_cost", "avg_net")}
            row[label]["trades"] = m["trades"]
        out[sym.upper()] = row
    return out


def main():
    data = {"generated": dt.date.today().isoformat(),
            "settings": {"timeframe": "4h", "risk_per_trade": 0.01, "max_leverage": 1.0,
                         "spot_fee": SPOT["fee"], "perp_fee": PERP["fee"],
                         "slippage": SPOT["slippage"], "funding_8h": PERP["funding_8h"],
                         "start_equity": 1000},
            "markets": {}}
    for sym in ("btc", "eth", "sol"):
        print("building", sym, flush=True)
        data["markets"][sym.upper()] = market(f"data/{sym}_4h.csv")
    os.makedirs("site/data", exist_ok=True)
    with open("site/data/results.json", "w") as f:
        json.dump(data, f, separators=(",", ":"))
    print("wrote site/data/results.json", os.path.getsize("site/data/results.json"), "bytes")
    scalp = scalp_report()
    if scalp:
        with open("site/data/scalp.json", "w") as f:
            json.dump(scalp, f, separators=(",", ":"))
        print("wrote site/data/scalp.json")


if __name__ == "__main__":
    main()
