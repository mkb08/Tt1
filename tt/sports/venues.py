"""Order routing for the stink-bid bot: a paper venue that simulates fills
from public trades, and live adapters for Kalshi and Polymarket.

Every venue offers the same four calls:
  place_bid(leg, price, size, expires) -> id   resting limit buy (maker)
  poll(order_id, leg)         -> (filled, open)
  cancel(order_id, leg)
  buy_now(leg, max_price, size) -> (filled, avg price, fee)   taker, for hedges
"""

from __future__ import annotations

import math
import os
import time
import uuid

from . import kalshi, polymarket
from .fees import kalshi_fee, poly_fee
from .games import Leg


def contracts_for(leg: Leg, price: float, usd: float) -> float:
    """Whole contracts worth about `usd`, at least the venue's minimum
    (Polymarket: usually 5 shares, so a $2 bid above 40c becomes 5 shares)."""
    n = math.floor(usd / price) if price > 0 else 0
    return float(max(n, math.ceil(leg.min_size)))


def maker_fee(leg: Leg, n: float, price: float) -> float:
    if leg.venue == "kalshi":
        return kalshi_fee(n, price, maker=True, multiplier=leg.fee.get("multiplier", 1.0))
    return 0.0


def taker_fee(leg: Leg, n: float, price: float) -> float:
    if leg.venue == "kalshi":
        return kalshi_fee(n, price, maker=False, multiplier=leg.fee.get("multiplier", 1.0))
    return poly_fee(n, price, leg.fee)


class PaperVenue:
    """A bid fills when the market trades BELOW it, or the best ask drops to
    it. Merely touching the bid isn't a fill: in reality you'd be behind
    everyone already waiting at that price. Hedges fill at the live ask, up
    to the size shown there."""

    def __init__(self, name: str, book: dict):
        self.name, self.book = name, book  # book: persisted {order_id: {...}}

    def place_bid(self, leg: Leg, price: float, size: float, expires: int | None = None) -> str:
        oid = f"paper-{uuid.uuid4().hex[:10]}"
        self.book[oid] = {"price": price, "size": size, "filled": 0.0, "open": True,
                          "since": int(time.time()), "expires": expires}
        return oid

    def _traded_below(self, leg: Leg, price: float, since: int) -> bool:
        if leg.venue == "kalshi":
            for t in kalshi.trades(leg.market_id, min_ts=since, limit=1000, max_pages=3):
                if kalshi.f(t.get("yes_price_dollars")) < price:
                    return True
            return False
        for t in polymarket.trades([leg.group_id], limit=500):
            if int(t["timestamp"]) < since:
                continue
            p = float(t["price"])
            # buying the other team's token at q sells this one at 1 - q
            if (str(t["asset"]) == leg.market_id and p < price) or \
                    (str(t["asset"]) != leg.market_id and 1 - p < price):
                return True
        return False

    def poll(self, order_id: str, leg: Leg) -> tuple[float, bool]:
        o = self.book.get(order_id)
        if not o or not o["open"]:
            return (o or {}).get("filled", 0.0), False
        now = int(time.time())
        if o.get("expires") and now >= o["expires"]:
            o["open"] = False
            return o["filled"], False
        if (leg.ask is not None and leg.ask <= o["price"]) or \
                self._traded_below(leg, o["price"], o["since"]):
            o.update(filled=o["size"], open=False)
        o["since"] = now - 5
        return o["filled"], o["open"]

    def cancel(self, order_id: str, leg: Leg) -> None:
        if order_id in self.book:
            self.book[order_id]["open"] = False

    def buy_now(self, leg: Leg, max_price: float, size: float) -> tuple[float, float, float]:
        if leg.ask is None or leg.ask > max_price or leg.ask_size < size:
            return 0.0, 0.0, 0.0
        return size, leg.ask, taker_fee(leg, size, leg.ask)


class KalshiVenue:
    def __init__(self, trader: kalshi.KalshiTrader):
        self.name, self.t = "kalshi", trader

    def place_bid(self, leg: Leg, price: float, size: float, expires: int | None = None) -> str:
        r = self.t.place_bid(leg.market_id, price, size, client_id=str(uuid.uuid4()),
                             expires=expires)
        return r["order_id"]

    def poll(self, order_id: str, leg: Leg) -> tuple[float, bool]:
        o = self.t.order(order_id)
        return kalshi.f(o.get("fill_count_fp")), o.get("status") == "resting"

    def cancel(self, order_id: str, leg: Leg) -> None:
        self.t.cancel(order_id, leg.market_id)

    def check(self) -> str:
        env = "demo" if self.t.base == kalshi.DEMO else "live"
        return f"kalshi ({env}): {self.t.balance():.2f} USD available"

    def buy_now(self, leg: Leg, max_price: float, size: float) -> tuple[float, float, float]:
        r = self.t.buy_now(leg.market_id, max_price, size, client_id=str(uuid.uuid4()))
        n = kalshi.f(r.get("fill_count"))
        return n, kalshi.f(r.get("average_fill_price")), n * kalshi.f(r.get("average_fee_paid"))


class PolymarketVenue:
    def __init__(self, trader: polymarket.PolymarketTrader):
        self.name, self.t = "polymarket", trader

    def place_bid(self, leg: Leg, price: float, size: float, expires: int | None = None) -> str:
        return self.t.place_bid(leg.market_id, price, size, expires)

    def poll(self, order_id: str, leg: Leg) -> tuple[float, bool]:
        o = self.t.order(order_id)
        return o["filled"], o["status"].upper() == "LIVE"

    def cancel(self, order_id: str, leg: Leg) -> None:
        self.t.cancel(order_id)

    def check(self) -> str:
        return self.t.check()

    def buy_now(self, leg: Leg, max_price: float, size: float) -> tuple[float, float, float]:
        n, spent = self.t.buy_now(leg.market_id, max_price, size)
        avg = spent / n if n else 0.0
        return n, avg, taker_fee(leg, n, avg) if n else 0.0


def live_venues(cfg: dict) -> dict:
    """Live adapters for the configured venues; keys come from the environment."""
    out = {}
    if "kalshi" in cfg["venues"]:
        key_id = os.environ.get("KALSHI_KEY_ID")
        key_path = os.environ.get("KALSHI_PRIVATE_KEY_PATH")
        if not key_id or not key_path:
            raise SystemExit("set KALSHI_KEY_ID and KALSHI_PRIVATE_KEY_PATH (the .key file "
                             "from Kalshi > Account & security > API Keys)")
        with open(key_path, "rb") as fh:
            pem = fh.read()
        base = kalshi.DEMO if cfg.get("kalshi_env", "prod") == "demo" else kalshi.PROD
        out["kalshi"] = KalshiVenue(kalshi.KalshiTrader(key_id, pem, base))
    if "polymarket" in cfg["venues"]:
        pk = os.environ.get("POLY_PRIVATE_KEY")
        if not pk:
            raise SystemExit("set POLY_PRIVATE_KEY (and POLY_WALLET, your Polymarket address)")
        out["polymarket"] = PolymarketVenue(
            polymarket.PolymarketTrader(pk, os.environ.get("POLY_WALLET")))
    return out
