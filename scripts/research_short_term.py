"""Pre-registered short-term strategy study. See docs/SHORT_TERM_RESEARCH.md.

    python scripts/research_short_term.py dev       # choose parameters on 2020-10..2024-10
    python scripts/research_short_term.py holdout   # test them once on 2024-10..2026-10

Data: data/{btc,eth,sol}_1h.csv and _4h.csv (python -m tt fetch) plus the
funding/perp files from scripts/fetch_binance_archive.py.

Development: 2020-10-12 .. 2024-10-10 (choose parameters, walk-forward)
Holdout:     2024-10-11 .. 2026-10-10 (evaluated once, at the end)
Costs: spot 0.10% fee + 0.02% slippage per fill; perps 0.055% + 0.02% + funding.
"""
import json, math, statistics, sys, time
ROOT = __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__)))
sys.path.insert(0, ROOT)
from tt import backtest as bt
from tt import indicators as ind
from tt.data import load_csv
from tt.risk import RiskConfig
import csv
from tt.strategies import FLAT, LONG, EmaCross, Signal, Strategy, TrendBreakout

DAY = 86_400_000
H1 = 3_600_000
SPLIT = 1728604800000            # 2024-10-11 00:00 UTC
RISK = RiskConfig(risk_per_trade=0.02, max_leverage=1.0, max_daily_loss=1.0, max_drawdown=1.0)
SPOT = dict(equity=1000.0, fee=0.001, slippage=0.0002, risk=RISK)
MARKETS = ("btc", "eth", "sol")


class VolBreakout(Strategy):
    """Daily volatility breakout on 1h candles (UTC days).
    Buy the first hourly close above today's open + k x yesterday's range;
    stop at today's open; sell at the next day's open. Optional trend
    filter: yesterday's close above the 20-day average of daily closes."""
    name = "volbreak"

    def __init__(self, k=0.5, trend_filter=False):
        self.k, self.trend_filter = k, trend_filter
        self.warmup = 24 * 22

    def prepare(self, c):
        n = len(c)
        self._c = [x.close for x in c]
        self._level = [None] * n
        self._open = [None] * n
        self._ok = [False] * n
        self._last_of_day = [((x.ts + H1) % DAY) == 0 for x in c]
        # daily aggregates
        days, cur = [], None
        for x in c:
            d = x.ts // DAY
            if cur is None or cur["d"] != d:
                cur = {"d": d, "o": x.open, "h": x.high, "l": x.low, "c": x.close}
                days.append(cur)
            else:
                cur["h"] = max(cur["h"], x.high); cur["l"] = min(cur["l"], x.low); cur["c"] = x.close
        idx = {dd["d"]: j for j, dd in enumerate(days)}
        closes = [dd["c"] for dd in days]
        sma20 = ind.sma(closes, 20)
        crossed_today = {}
        for i, x in enumerate(c):
            j = idx[x.ts // DAY]
            if j == 0:
                continue
            prev = days[j - 1]
            level = days[j]["o"] + self.k * (prev["h"] - prev["l"])
            self._level[i], self._open[i] = level, days[j]["o"]
            trend_ok = (not self.trend_filter) or (sma20[j - 1] is not None and prev["c"] > sma20[j - 1])
            first = not crossed_today.get(j, False)
            if x.close > level:
                crossed_today[j] = True
            self._ok[i] = trend_ok and first and x.close > level and not self._last_of_day[i]

    def signal(self, i, side, bars_held=0):
        if side == LONG:
            return Signal(FLAT) if self._last_of_day[i] else Signal(LONG)
        if self._ok[i] and self._open[i] < self._c[i]:
            return Signal(LONG, self._open[i])
        return Signal(FLAT)


FAMILIES = {
    "volbreak": (VolBreakout, {"k": [0.4, 0.6, 0.8], "trend_filter": [False, True]}),
    "trend1h": (TrendBreakout, {"entry": [48, 96, 168]}),      # exit = entry // 2 below
    "ema1h": (EmaCross, {"fast": [24, 48, 96]}),               # slow = 4 x fast below
}


def make(family, p):
    cls = FAMILIES[family][0]
    if family == "trend1h":
        return cls(entry=p["entry"], exit=p["entry"] // 2, atr_n=p["entry"] // 2, atr_mult=3.0)
    if family == "ema1h":
        return cls(fast=p["fast"], slow=4 * p["fast"], atr_n=14, atr_mult=4.0)
    return cls(**p)


def grid(g):
    return bt.grid(g)


def split_index(c):
    return next(i for i, x in enumerate(c) if x.ts >= SPLIT)


# ---------------- funding carry (delta neutral) ----------------
def carry_backtest(spot, perp, funding, lookback, threshold, start_ts, end_ts,
                   spot_fee=0.001, perp_fee=0.00055, slip=0.0002, equity=1000.0):
    """Long spot + short perp of equal notional while the trailing average
    funding rate (last `lookback` payments) is above `threshold`. Decisions
    at each funding time; fills at the next hour's open on both legs.
    Half the equity buys spot, half is perp margin (1x short, no liquidation
    risk modelled beyond that). Returns metrics and an equity curve."""
    sp = {x.ts: x for x in spot}
    pp = {x.ts: x for x in perp}
    times = sorted(t for t in funding if start_ts <= t < end_ts)
    rates = [funding[t] for t in sorted(funding)]
    allts = sorted(funding)
    pos = None  # dict(qty, spot_entry, perp_entry)
    cash = equity
    curve, trades, fund_total, fees_total = [], 0, 0.0, 0.0
    k_index = {t: i for i, t in enumerate(allts)}
    for t in times:
        bar = sp.get(t); pbar = pp.get(t)
        if bar is None or pbar is None:
            continue
        # funding payment at t on an open short perp (positive rate: shorts receive)
        if pos:
            f = pos["qty"] * pbar.open * funding[t]
            cash += f; fund_total += f
        i = k_index[t]
        avg = statistics.mean(rates[max(0, i - lookback + 1): i + 1])
        want = avg > threshold
        if want and pos is None:
            notional = cash / 2
            s_px = bar.open * (1 + slip); p_px = pbar.open * (1 - slip)
            qty = notional / s_px
            fee = qty * s_px * spot_fee + qty * p_px * perp_fee
            cash -= fee; fees_total += fee
            pos = {"qty": qty, "s": s_px, "p": p_px}
            trades += 1
        elif not want and pos is not None:
            s_px = bar.open * (1 - slip); p_px = pbar.open * (1 + slip)
            pnl = pos["qty"] * (s_px - pos["s"]) + pos["qty"] * (pos["p"] - p_px)
            fee = pos["qty"] * s_px * spot_fee + pos["qty"] * p_px * perp_fee
            cash += pnl - fee; fees_total += fee
            pos = None
        mtm = cash + (pos["qty"] * (bar.open - pos["s"]) + pos["qty"] * (pos["p"] - pbar.open) if pos else 0.0)
        curve.append((t, mtm))
    if pos:  # close at the end
        t = curve[-1][0]; bar = sp[t]; pbar = pp[t]
        pnl = pos["qty"] * (bar.open * (1 - slip) - pos["s"]) + pos["qty"] * (pos["p"] - pbar.open * (1 + slip))
        fee = pos["qty"] * bar.open * spot_fee + pos["qty"] * pbar.open * perp_fee
        cash += pnl - fee; fees_total += fee
        curve[-1] = (t, cash)
    eq = [e for _, e in curve]
    years = (curve[-1][0] - curve[0][0]) / (365.25 * DAY) if len(curve) > 1 else 0
    rets = [eq[j] / eq[j - 1] - 1 for j in range(1, len(eq))]
    sharpe = (statistics.mean(rets) / statistics.stdev(rets) * math.sqrt(len(rets) / years)
              if len(rets) > 2 and statistics.stdev(rets) > 0 and years > 0 else 0.0)
    return {"total_return": eq[-1] / equity - 1, "cagr": (eq[-1] / equity) ** (1 / years) - 1 if years > 0 else 0,
            "max_drawdown": bt.max_drawdown(eq), "sharpe": sharpe, "trades": trades,
            "funding": fund_total, "fees": fees_total, "years": years}


def main():
    t0 = time.time()
    data = {m: load_csv(f"{ROOT}/data/{m}_1h.csv") for m in MARKETS}
    report = {"dev": {}, "chosen": {}, "holdout": {}, "configs_tested": 0}
    # ---- development: walk-forward inside 2020-10 .. 2024-10, same params for all markets
    for fam, (cls, g) in FAMILIES.items():
        combos = grid(g)
        report["configs_tested"] += len(combos) * len(MARKETS)
        scores = []
        for p in combos:
            per = []
            for m in MARKETS:
                c = data[m]
                dev = c[:split_index(c)]
                wf_like = []
                # 3 sequential dev folds; score = compounded net return across folds
                seg = len(dev) // 4
                for k in range(1, 4):
                    end = len(dev) if k == 3 else seg * (k + 1)
                    r = bt.backtest(dev[:end], make(fam, p), start=seg * k, **SPOT).metrics()
                    wf_like.append(r)
                comp = math.prod(1 + r["total_return"] for r in wf_like) - 1
                per.append({"market": m, "comp": comp, "folds_pos": sum(r["total_return"] > 0 for r in wf_like),
                            "trades": sum(r["trades"] for r in wf_like),
                            "mdd": max(r["max_drawdown"] for r in wf_like)})
            avg = statistics.mean(x["comp"] for x in per)
            scores.append((avg, p, per))
            print(f"[dev] {fam:9s} {p} avg {avg:+.1%} " + " ".join(f"{x['market']}:{x['comp']:+.1%}({x['folds_pos']}/3,{x['trades']}t)" for x in per) + f"  [{time.time()-t0:.0f}s]", flush=True)
        scores.sort(key=lambda s: s[0], reverse=True)
        best = scores[0]
        report["dev"][fam] = [(s[0], s[1], s[2]) for s in scores]
        report["chosen"][fam] = best[1]
    # funding carry
    fund = {m: dict((int(r["ts"]), float(r["rate"])) for r in __import__("csv").DictReader(open(f"{ROOT}/data/{m}_funding.csv"))) for m in MARKETS}
    perp = {m: load_csv(f"{ROOT}/data/{m}_perp_1h.csv") for m in MARKETS}
    carry_grid = [{"lookback": lb, "threshold": th} for lb in (3, 9) for th in (0.0001, 0.0002)]
    report["configs_tested"] += len(carry_grid) * len(MARKETS)
    cs = []
    dev_start = max(min(fund[m]) for m in MARKETS)
    for p in carry_grid:
        per = [carry_backtest(data[m], perp[m], fund[m], p["lookback"], p["threshold"], dev_start, SPLIT) | {"market": m} for m in MARKETS]
        avg = statistics.mean(x["total_return"] for x in per)
        cs.append((avg, p, per))
        print(f"[dev] carry     {p} avg {avg:+.1%} " + " ".join(f"{x['market']}:{x['total_return']:+.1%}(dd {x['max_drawdown']:.1%},{x['trades']}t)" for x in per), flush=True)
    cs.sort(key=lambda s: s[0], reverse=True)
    report["chosen"]["carry"] = cs[0][1]
    print("\nCHOSEN ON DEVELOPMENT DATA:", json.dumps(report["chosen"]), flush=True)
    json.dump(report, open(f"{ROOT}/research/short_term_dev.json", "w"), default=str, indent=1)
    print("configs tested:", report["configs_tested"], f"[{time.time()-t0:.0f}s]")





def holdout():
    dev = json.load(open(f"{ROOT}/research/short_term_dev.json"))
    chosen = dev["chosen"]
    data = {m: load_csv(f"{ROOT}/data/{m}_1h.csv") for m in MARKETS}
    out = {}

    def verdict(rows):
        pos = sum(r["total_return"] > 0 for r in rows)
        avg = statistics.mean(r["total_return"] for r in rows)
        sh = statistics.mean(r["sharpe"] for r in rows)
        return {"positive_markets": pos, "avg_return": avg, "avg_sharpe": sh,
                "pass": pos >= 2 and avg > 0 and sh > 0.5}

    for fam in FAMILIES:
        rows = []
        for m in MARKETS:
            c = data[m]
            res = bt.backtest(c, make(fam, chosen[fam]), start=split_index(c), **SPOT)
            mt = res.metrics()
            rows.append({"market": m, **{k: mt[k] for k in ("total_return", "cagr", "max_drawdown", "sharpe", "trades",
                                                             "win_rate", "avg_gross", "avg_cost", "avg_net", "buy_hold_return",
                                                             "buy_hold_mdd", "exposure")}})
        out[fam] = {"params": chosen[fam], "rows": rows, **verdict(rows)}

    fund = {m: {int(r["ts"]): float(r["rate"]) for r in csv.DictReader(open(f"{ROOT}/data/{m}_funding.csv"))} for m in MARKETS}
    perp = {m: load_csv(f"{ROOT}/data/{m}_perp_1h.csv") for m in MARKETS}
    end = min(max(fund[m]) for m in MARKETS) + 1
    p = chosen["carry"]
    rows = [carry_backtest(data[m], perp[m], fund[m], p["lookback"], p["threshold"], SPLIT, end) | {"market": m} for m in MARKETS]
    out["carry"] = {"params": p, "rows": rows, **verdict(rows)}

    # context only (not part of the test): the existing 4h trend strategy over the same period
    ctx = []
    for m in MARKETS:
        c4 = load_csv(f"{ROOT}/data/{m}_4h.csv")
        r = bt.backtest(c4, TrendBreakout(), start=split_index(c4), **SPOT).metrics()
        ctx.append({"market": m, "total_return": r["total_return"], "max_drawdown": r["max_drawdown"], "sharpe": r["sharpe"], "trades": r["trades"]})
    out["context_trend4h"] = ctx

    json.dump(out, open(f"{ROOT}/research/short_term_holdout.json", "w"), default=str, indent=1)
    for fam, v in out.items():
        if fam == "context_trend4h":
            print("\ncontext, 4h trend:", " ".join(f"{r['market']}:{r['total_return']:+.1%}(dd {r['max_drawdown']:.1%}, sh {r['sharpe']:.2f}, {r['trades']}t)" for r in v))
            continue
        print(f"\n{fam} {v['params']} -> {'PASS' if v['pass'] else 'FAIL'} (positive {v['positive_markets']}/3, avg {v['avg_return']:+.1%}, avg sharpe {v['avg_sharpe']:.2f})")
        for r in v["rows"]:
            extra = (f" gross/trade {r['avg_gross']:+.3%} cost {r['avg_cost']:.3%} hold {r['buy_hold_return']:+.0%} (dd {r['buy_hold_mdd']:.0%})"
                     if "avg_gross" in r else f" funding {r['funding']:+.1f} fees {r['fees']:.1f}")
            print(f"   {r['market']}: return {r['total_return']:+.1%} cagr {r['cagr']:+.1%} dd {r['max_drawdown']:.1%} sharpe {r['sharpe']:.2f} trades {r['trades']}{extra}")


if __name__ == "__main__":
    phase = sys.argv[1] if len(sys.argv) > 1 else ""
    if phase == "dev":
        main()
    elif phase == "holdout":
        holdout()
    else:
        sys.exit("usage: python scripts/research_short_term.py dev|holdout  (run dev first; holdout once)")
