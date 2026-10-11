"""Trading fees on both venues, in dollars.

Both charge more near 50 cents and almost nothing near 0 or 1, through the
p x (1 - p) term.

Kalshi (fee_type "quadratic_with_maker_fees", game markets):
    fee = round up to the cent( multiplier x rate x contracts x p x (1 - p) )
    rate 0.07 for takers, 0.0175 for makers; the series sets the multiplier
    (1 for NFL/NBA/NHL/college, 0.5 for MLB as of Oct 2026). Charged per order.

Polymarket (markets with fees enabled):
    fee = shares x rate x (p x (1 - p)) ^ exponent, rounded to 5 decimals
    takers only; makers pay nothing. The market's `feeSchedule` gives rate and
    exponent (sports were 0.03-0.05 with exponent 1 in Oct 2026).
"""

from __future__ import annotations

import math

KALSHI_TAKER = 0.07
KALSHI_MAKER = 0.0175


def kalshi_fee(contracts: float, price: float, maker: bool = False,
               multiplier: float = 1.0) -> float:
    rate = KALSHI_MAKER if maker else KALSHI_TAKER
    raw = multiplier * rate * contracts * price * (1 - price)
    # round up to the cent; the epsilon stops 0.07 * 100 * ... float noise
    # from adding a cent
    return math.ceil(raw * 100 - 1e-9) / 100 if raw > 0 else 0.0


def poly_fee(shares: float, price: float, schedule: dict | None, maker: bool = False) -> float:
    if maker or not schedule:
        return 0.0
    rate = float(schedule.get("rate") or 0)
    exponent = float(schedule.get("exponent") or 1)
    return round(shares * rate * (price * (1 - price)) ** exponent, 5)
