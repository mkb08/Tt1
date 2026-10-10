"""Position sizing and account-level kill switches.

These rules matter more than the entry signal: they decide whether a bad
streak is survivable.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

DAY_MS = 86_400_000


@dataclass
class RiskConfig:
    risk_per_trade: float = 0.01   # fraction of equity lost if the stop is hit
    max_leverage: float = 1.0      # position notional / equity cap
    max_daily_loss: float = 0.03   # pause new entries for the UTC day after this
    max_drawdown: float = 0.20     # from peak equity: stop trading entirely

    @classmethod
    def from_dict(cls, d: dict | None) -> "RiskConfig":
        return cls(**(d or {}))

    def to_dict(self) -> dict:
        return asdict(self)


def position_size(equity: float, entry: float, stop: float | None,
                  cfg: RiskConfig, qty_step: float = 0.0) -> float:
    """Units to trade so that hitting `stop` loses `risk_per_trade` of equity,
    capped by `max_leverage`. Returns 0 when the trade can't be sized."""
    if equity <= 0 or entry <= 0 or stop is None:
        return 0.0
    dist = abs(entry - stop)
    if dist <= 0:
        return 0.0
    qty = min(equity * cfg.risk_per_trade / dist,
              equity * cfg.max_leverage / entry)
    if qty_step > 0:
        qty = math.floor(qty / qty_step) * qty_step
    return max(qty, 0.0)


class RiskGuard:
    """Tracks peak equity and the day's opening equity.

    `allow_entries` says whether new positions may be opened. Once the
    max-drawdown switch trips it stays tripped until a human resets the state.
    """

    def __init__(self, cfg: RiskConfig, state: dict | None = None):
        self.cfg = cfg
        s = state or {}
        self.peak = s.get("peak", 0.0)
        self.day = s.get("day")
        self.day_start = s.get("day_start", 0.0)
        self.halted = s.get("halted", False)
        self.reason = s.get("reason", "")

    def update(self, equity: float, now_ms: int) -> None:
        day = now_ms // DAY_MS
        if day != self.day:
            self.day, self.day_start = day, equity
        self.peak = max(self.peak, equity)
        if self.peak > 0 and equity <= self.peak * (1 - self.cfg.max_drawdown):
            self.halted = True
            self.reason = (f"max drawdown hit: equity {equity:.2f} is "
                           f"{1 - equity / self.peak:.1%} below peak {self.peak:.2f}")

    def allow_entries(self, equity: float) -> tuple[bool, str]:
        if self.halted:
            return False, self.reason
        if self.day_start > 0 and equity <= self.day_start * (1 - self.cfg.max_daily_loss):
            return False, f"daily loss limit: {1 - equity / self.day_start:.1%} down today"
        return True, ""

    def state(self) -> dict:
        return {"peak": self.peak, "day": self.day, "day_start": self.day_start,
                "halted": self.halted, "reason": self.reason}
