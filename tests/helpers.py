from tt.data import Candle
from tt.strategies import FLAT, Signal, Strategy

H = 3_600_000


def bars(prices, start=0, spread=0.0):
    """Candles opening at the previous close; high/low widen by `spread`."""
    out, prev = [], prices[0]
    for i, p in enumerate(prices):
        out.append(Candle(start + i * H, prev, max(prev, p) + spread,
                          min(prev, p) - spread, p, 1.0))
        prev = p
    return out


class Scripted(Strategy):
    """Returns pre-set signals by bar index; FLAT otherwise (keeps side)."""
    name = "scripted"

    def __init__(self, script: dict[int, Signal], hold=True):
        self.script, self.hold, self.warmup = script, hold, 0

    def prepare(self, candles):
        pass

    def signal(self, i, side, bars_held=0):
        if i in self.script:
            return self.script[i]
        return Signal(side) if self.hold else Signal(FLAT)
