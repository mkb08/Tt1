"""Strategies.

A strategy precomputes causal indicators once with `prepare(candles)` and
then answers `signal(i, side, bars_held)`: given the closed bar index i, the
current position side and how many bars it has been held, what side should
we hold from the next bar on, where is the protective stop and (optionally)
the profit target. Strategies keep no state between calls, so backtests and
live runs behave the same.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import indicators as ind
from .data import Candle

LONG, FLAT, SHORT = 1, 0, -1


@dataclass(frozen=True)
class Signal:
    side: int                  # +1 long, 0 flat, -1 short
    stop: float | None = None  # stop price; while holding, only ever tightened
    take: float | None = None  # profit target, fixed when the trade opens


class Strategy:
    name = "base"
    warmup = 0

    def prepare(self, candles: list[Candle]) -> None:
        raise NotImplementedError

    def signal(self, i: int, side: int, bars_held: int = 0) -> Signal:
        raise NotImplementedError

    def params(self) -> dict:
        return {k: v for k, v in vars(self).items()
                if not k.startswith("_") and k != "warmup"}


class TrendBreakout(Strategy):
    """Donchian channel breakout with an ATR trailing stop (Turtle-style).

    Trend following is the strategy family with the longest documented track
    record across asset classes (time-series momentum). It loses often in
    small amounts and earns in a few large trends.
    """

    name = "trend"

    def __init__(self, entry: int = 55, exit: int = 20, atr_n: int = 20,
                 atr_mult: float = 3.0, allow_short: bool = False):
        self.entry, self.exit, self.atr_n = entry, exit, atr_n
        self.atr_mult, self.allow_short = atr_mult, allow_short
        self.warmup = max(entry, exit, atr_n) + 1

    def prepare(self, candles):
        self._c = [c.close for c in candles]
        self._up, self._dn = ind.donchian(candles, self.entry)
        self._xup, self._xdn = ind.donchian(candles, self.exit)
        self._atr = ind.atr(candles, self.atr_n)

    def signal(self, i, side, bars_held=0):
        c, a = self._c[i], self._atr[i]
        up, dn, xup, xdn = self._up[i], self._dn[i], self._xup[i], self._xdn[i]
        if None in (a, up, dn, xup, xdn):
            return Signal(FLAT)
        if side == LONG:
            if c < xdn:
                return Signal(FLAT)
            return Signal(LONG, c - self.atr_mult * a)
        if side == SHORT:
            if c > xup:
                return Signal(FLAT)
            return Signal(SHORT, c + self.atr_mult * a)
        if c > up:
            return Signal(LONG, c - self.atr_mult * a)
        if self.allow_short and c < dn:
            return Signal(SHORT, c + self.atr_mult * a)
        return Signal(FLAT)


class EmaCross(Strategy):
    """Fast/slow EMA crossover, optionally only long above a long-term EMA."""

    name = "ema"

    def __init__(self, fast: int = 20, slow: int = 100, atr_n: int = 14,
                 atr_mult: float = 4.0, allow_short: bool = False):
        self.fast, self.slow, self.atr_n = fast, slow, atr_n
        self.atr_mult, self.allow_short = atr_mult, allow_short
        self.warmup = max(fast, slow, atr_n) + 1

    def prepare(self, candles):
        closes = [c.close for c in candles]
        self._c = closes
        self._f, self._s = ind.ema(closes, self.fast), ind.ema(closes, self.slow)
        self._atr = ind.atr(candles, self.atr_n)

    def signal(self, i, side, bars_held=0):
        f, s, a, c = self._f[i], self._s[i], self._atr[i], self._c[i]
        if None in (f, s, a):
            return Signal(FLAT)
        if f > s:
            return Signal(LONG, c - self.atr_mult * a)
        if f < s and self.allow_short:
            return Signal(SHORT, c + self.atr_mult * a)
        return Signal(FLAT)


class RsiReversion(Strategy):
    """Buy short-term oversold dips, but only inside a long-term uptrend.

    Mean reversion wins often in small amounts and loses rarely but large,
    which is why the trend filter and hard stop matter.
    """

    name = "rsi"

    def __init__(self, rsi_n: int = 3, entry: float = 15, exit: float = 60,
                 trend_n: int = 200, atr_n: int = 14, atr_mult: float = 3.0,
                 max_bars: int = 10):
        self.rsi_n, self.entry, self.exit, self.trend_n = rsi_n, entry, exit, trend_n
        self.atr_n, self.atr_mult, self.max_bars = atr_n, atr_mult, max_bars
        self.warmup = max(rsi_n, trend_n, atr_n) + 1

    def prepare(self, candles):
        closes = [c.close for c in candles]
        self._c = closes
        self._rsi = ind.rsi(closes, self.rsi_n)
        self._trend = ind.sma(closes, self.trend_n)
        self._atr = ind.atr(candles, self.atr_n)

    def signal(self, i, side, bars_held=0):
        r, t, a, c = self._rsi[i], self._trend[i], self._atr[i], self._c[i]
        if None in (r, t, a):
            return Signal(FLAT)
        if side == LONG:
            if r > self.exit or bars_held >= self.max_bars:
                return Signal(FLAT)
            return Signal(LONG)  # keep the entry stop; no trailing
        if c > t and r < self.entry:
            return Signal(LONG, c - self.atr_mult * a)
        return Signal(FLAT)


class Scalp(Strategy):
    """Short-term scalper for 1-15 minute candles: small fixed target, fixed
    stop, and a time limit.

    mode "bounce": in an uptrend (close above the trend EMA) buy a close below
    the lower Bollinger band, betting on a quick snap back.
    mode "break": in an uptrend buy a close above the highest high of the last
    `n` candles, betting on a quick follow-through.
    Exits at entry +tp x ATR (target), -sl x ATR (stop), or after max_bars.
    allow_short mirrors both rules for perpetual futures.

    Scalps make many small trades, so fees decide the outcome: test with your
    real fee level (taker vs maker) before trusting any result.
    """

    name = "scalp"

    def __init__(self, mode: str = "bounce", n: int = 20, k: float = 2.0,
                 trend_n: int = 100, atr_n: int = 14, tp: float = 1.0,
                 sl: float = 1.0, max_bars: int = 12, allow_short: bool = False):
        if mode not in ("bounce", "break"):
            raise ValueError("mode must be 'bounce' or 'break'")
        self.mode, self.n, self.k, self.trend_n, self.atr_n = mode, n, k, trend_n, atr_n
        self.tp, self.sl, self.max_bars, self.allow_short = tp, sl, max_bars, allow_short
        self.warmup = max(n, trend_n, atr_n) + 1

    def prepare(self, candles):
        closes = [c.close for c in candles]
        self._c = closes
        self._mid = ind.sma(closes, self.n)
        self._sd = ind.rolling_std(closes, self.n)
        self._trend = ind.ema(closes, self.trend_n)
        self._atr = ind.atr(candles, self.atr_n)
        self._hi, self._lo = ind.donchian(candles, self.n)

    def signal(self, i, side, bars_held=0):
        c, a, t = self._c[i], self._atr[i], self._trend[i]
        mid, sd, hi, lo = self._mid[i], self._sd[i], self._hi[i], self._lo[i]
        if None in (a, t, mid, sd, hi, lo) or a <= 0:
            return Signal(FLAT)
        if side != FLAT:
            # target and stop were fixed at entry; only the clock can end it here
            return Signal(FLAT) if bars_held >= self.max_bars else Signal(side)
        if self.mode == "bounce":
            go_long = c > t and c < mid - self.k * sd
            go_short = c < t and c > mid + self.k * sd
        else:
            go_long = c > t and c > hi
            go_short = c < t and c < lo
        if go_long:
            return Signal(LONG, c - self.sl * a, c + self.tp * a)
        if go_short and self.allow_short:
            return Signal(SHORT, c + self.sl * a, c - self.tp * a)
        return Signal(FLAT)


STRATEGIES: dict[str, type[Strategy]] = {
    cls.name: cls for cls in (TrendBreakout, EmaCross, RsiReversion, Scalp)
}


def make_strategy(name: str, params: dict | None = None) -> Strategy:
    if name not in STRATEGIES:
        raise ValueError(f"unknown strategy {name!r}; choose from {sorted(STRATEGIES)}")
    return STRATEGIES[name](**(params or {}))
