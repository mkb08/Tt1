"""Event-driven backtester with costs, stops, metrics, walk-forward testing
and a Monte Carlo drawdown check.

Execution model (deliberately conservative):
  * signals are computed on bar i's close and filled at bar i+1's open;
  * every fill pays the taker fee plus slippage;
  * stops are checked against each bar's high/low; a gap through the stop
    fills at the open, not at the stop price;
  * if a bar both opens a trade and touches its stop, the stop wins;
  * perpetual futures pay or receive funding while a position is open,
    either at a constant rate or from a real funding-rate history.
"""

from __future__ import annotations

import itertools
import math
import random
import statistics
from dataclasses import dataclass, field

from .data import Candle
from .risk import RiskConfig, RiskGuard, position_size
from .strategies import FLAT, LONG, Strategy


@dataclass
class Trade:
    side: int
    qty: float
    entry_ts: int
    entry: float
    exit_ts: int = 0
    exit: float = 0.0
    stop: float | None = None
    risk: float = 0.0     # money at risk at entry (for R multiples)
    fees: float = 0.0
    funding: float = 0.0  # paid (+) or received (-) on perpetuals
    pnl: float = 0.0      # net of fees and funding
    reason: str = ""


@dataclass
class Result:
    strategy: str
    params: dict
    trades: list[Trade] = field(default_factory=list)
    equity: list[tuple[int, float]] = field(default_factory=list)
    start_equity: float = 0.0
    buy_hold_return: float = 0.0
    buy_hold_mdd: float = 0.0
    bars_in_market: int = 0
    halted: str = ""

    @property
    def end_equity(self) -> float:
        return self.equity[-1][1] if self.equity else self.start_equity

    def metrics(self) -> dict:
        eq = [e for _, e in self.equity]
        n = len(eq)
        total = self.end_equity / self.start_equity - 1 if self.start_equity else 0.0
        years = ((self.equity[-1][0] - self.equity[0][0]) / (365.25 * 86_400_000)
                 if n > 1 else 0.0)
        cagr = ((self.end_equity / self.start_equity) ** (1 / years) - 1
                if years > 0 and self.end_equity > 0 else float("nan"))
        rets = [eq[i] / eq[i - 1] - 1 for i in range(1, n) if eq[i - 1] > 0]
        bars_per_year = (n - 1) / years if years > 0 else 0
        sharpe = (statistics.mean(rets) / statistics.stdev(rets) * math.sqrt(bars_per_year)
                  if len(rets) > 2 and statistics.stdev(rets) > 0 else 0.0)
        wins = [t.pnl for t in self.trades if t.pnl > 0]
        losses = [t.pnl for t in self.trades if t.pnl <= 0]
        r_mults = [t.pnl / t.risk for t in self.trades if t.risk > 0]
        return {
            "trades": len(self.trades),
            "total_return": total,
            "cagr": cagr,
            "max_drawdown": max_drawdown(eq),
            "sharpe": sharpe,
            "win_rate": len(wins) / len(self.trades) if self.trades else 0.0,
            "profit_factor": (sum(wins) / -sum(losses)) if losses and sum(losses) < 0
                             else (float("inf") if wins else 0.0),
            "avg_r": statistics.mean(r_mults) if r_mults else 0.0,
            "exposure": self.bars_in_market / max(n, 1),
            "fees_paid": sum(t.fees for t in self.trades),
            "funding_paid": sum(t.funding for t in self.trades),
            "buy_hold_return": self.buy_hold_return,
            "buy_hold_mdd": self.buy_hold_mdd,
            "years": years,
            "halted": self.halted,
        }


def max_drawdown(equity: list[float]) -> float:
    peak, mdd = float("-inf"), 0.0
    for e in equity:
        peak = max(peak, e)
        if peak > 0:
            mdd = max(mdd, 1 - e / peak)
    return mdd


EIGHT_HOURS_MS = 8 * 3_600_000


def backtest(candles: list[Candle], strategy: Strategy, *,
             equity: float = 1000.0, risk: RiskConfig | None = None,
             fee: float = 0.001, slippage: float = 0.0005,
             funding_8h: float = 0.0,
             funding_events: list[tuple[int, float]] | None = None,
             start: int = 0) -> Result:
    """Run `strategy` over `candles`. Trading begins at bar max(start, warmup);
    earlier bars are only used to warm up indicators.

    Funding (perpetuals only): pass `funding_events` [(ts, rate)] for real
    history, or `funding_8h` as a constant rate per 8 hours. Positive rates
    mean longs pay and shorts receive."""
    risk = risk or RiskConfig()
    strategy.prepare(candles)
    guard = RiskGuard(risk)
    first = max(start, strategy.warmup)
    res = Result(strategy.name, strategy.params(), start_equity=equity)
    if first >= len(candles) - 1:
        return res
    res.buy_hold_return = candles[-1].close / candles[first].open - 1
    res.buy_hold_mdd = max_drawdown([c.close for c in candles[first:]])

    cash = equity
    pos: Trade | None = None
    entry_bar = 0
    pending = None  # Signal decided on the previous close
    events = funding_events or []
    ev = 0

    def fill_price(px: float, side: int) -> float:
        return px * (1 + slippage * side)

    def close(t: Trade, px: float, ts: int, reason: str) -> float:
        fill = fill_price(px, -t.side)
        f = abs(t.qty) * fill * fee
        t.exit, t.exit_ts, t.reason = fill, ts, reason
        t.fees += f
        gross = t.side * t.qty * (fill - t.entry)
        t.pnl = gross - t.fees - t.funding
        res.trades.append(t)
        return gross - f

    for i in range(first, len(candles)):
        bar = candles[i]

        # 1) act on the previous bar's signal at this bar's open
        if pending is not None:
            want = pending.side
            if pos and want != pos.side:
                cash += close(pos, bar.open, bar.ts, "signal")
                pos = None
            if pos is None and want != FLAT:
                eq_now = cash
                ok, why = guard.allow_entries(eq_now)
                entry = fill_price(bar.open, want)
                # a gap past the stop makes the planned trade invalid: skip it
                stop_ok = pending.stop is not None and (pending.stop - entry) * want < 0
                if ok and stop_ok:
                    qty = position_size(eq_now, entry, pending.stop, risk)
                    if qty > 0:
                        f = qty * entry * fee
                        cash -= f
                        pos = Trade(want, qty, bar.ts, entry, stop=pending.stop,
                                    risk=qty * abs(entry - pending.stop), fees=f)
                        entry_bar = i
                elif guard.halted:
                    res.halted = why
            elif pos and pending.stop is not None:
                # trail: only ever move the stop in the trade's favour
                if pos.side == LONG:
                    pos.stop = max(pos.stop, pending.stop)
                else:
                    pos.stop = min(pos.stop, pending.stop)
            pending = None

        # funding for the time this bar spans (charged to cash as it accrues)
        bar_end = (candles[i + 1].ts if i + 1 < len(candles)
                   else bar.ts + (bar.ts - candles[i - 1].ts if i else 0))
        rate = 0.0
        if events:
            while ev < len(events) and events[ev][0] < bar_end:
                if events[ev][0] >= bar.ts:
                    rate += events[ev][1]
                ev += 1
        elif funding_8h:
            rate = funding_8h * (bar_end - bar.ts) / EIGHT_HOURS_MS
        if pos and rate:
            paid = pos.side * pos.qty * bar.open * rate
            pos.funding += paid
            cash -= paid

        # 2) protective stop inside this bar
        if pos and pos.stop is not None:
            if pos.side == LONG and bar.low <= pos.stop:
                cash += close(pos, min(bar.open, pos.stop), bar.ts, "stop")
                pos = None
            elif pos and pos.side != LONG and bar.high >= pos.stop:
                cash += close(pos, max(bar.open, pos.stop), bar.ts, "stop")
                pos = None

        # 3) mark to market
        eq = cash + (pos.side * pos.qty * (bar.close - pos.entry) if pos else 0.0)
        res.equity.append((bar.ts, eq))
        res.bars_in_market += pos is not None
        guard.update(eq, bar.ts)

        # 4) decide for the next bar
        if i < len(candles) - 1:
            side = pos.side if pos else FLAT
            pending = strategy.signal(i, side, i - entry_bar if pos else 0)
            if guard.halted and pos is None:
                res.halted = guard.reason
                pending = None

    if pos:  # close at the last close so the result is fully realised
        last = candles[-1]
        cash += close(pos, last.close, last.ts, "end")
        res.equity[-1] = (last.ts, cash)
    return res


def grid(param_grid: dict[str, list]) -> list[dict]:
    keys = list(param_grid)
    return [dict(zip(keys, combo)) for combo in itertools.product(*param_grid.values())]


def score(m: dict) -> float:
    """Return per unit of drawdown; penalises strategies with too few trades."""
    if m["trades"] < 5:
        return float("-inf")
    return m["total_return"] / max(m["max_drawdown"], 0.05)


def walk_forward(candles: list[Candle], strategy_cls, param_grid: dict[str, list],
                 folds: int = 4, **bt_kwargs) -> dict:
    """Anchored walk-forward: optimise on all data before each test segment,
    then trade the segment with those parameters. Only the out-of-sample
    segments are reported - that is the honest estimate of live performance."""
    n = len(candles)
    seg = n // (folds + 1)
    combos = grid(param_grid)
    out = []
    for k in range(1, folds + 1):
        train = candles[:seg * k]
        test_end = n if k == folds else seg * (k + 1)
        best, best_score = None, float("-inf")
        for p in combos:
            s = score(backtest(train, strategy_cls(**p), **bt_kwargs).metrics())
            if s > best_score:
                best, best_score = p, s
        if best is None:
            best = combos[0]
        oos = backtest(candles[:test_end], strategy_cls(**best), start=seg * k, **bt_kwargs)
        out.append({"fold": k, "params": best, "in_sample_score": best_score,
                    "oos": oos.metrics()})
    rets = [f["oos"]["total_return"] for f in out]
    compounded = math.prod(1 + r for r in rets) - 1
    return {"folds": out, "oos_compounded_return": compounded,
            "oos_profitable_folds": sum(r > 0 for r in rets)}


def monte_carlo(trades: list[Trade], start_equity: float, runs: int = 2000,
                seed: int = 1) -> dict:
    """Reshuffle trade order to see how bad drawdowns could plausibly get with
    the same trades. Uses per-trade returns relative to equity at entry."""
    if len(trades) < 2:
        return {}
    rng = random.Random(seed)
    eq, rets = start_equity, []
    for t in trades:
        rets.append(t.pnl / eq if eq > 0 else 0.0)
        eq += t.pnl
    dds, finals = [], []
    for _ in range(runs):
        rng.shuffle(rets)
        e, curve = 1.0, [1.0]
        for r in rets:
            e *= 1 + r
            curve.append(e)
        dds.append(max_drawdown(curve))
        finals.append(e - 1)
    dds.sort()
    finals.sort()
    pct = lambda xs, p: xs[min(len(xs) - 1, int(p * len(xs)))]  # noqa: E731
    return {"dd_median": pct(dds, 0.5), "dd_95": pct(dds, 0.95),
            "return_5th_pct": pct(finals, 0.05), "prob_loss": sum(f < 0 for f in finals) / runs}
