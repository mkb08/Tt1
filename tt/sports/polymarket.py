"""Polymarket: public market data (no key) and a trader on the official SDK.

A game's moneyline market has two outcome tokens, one per team; each token
has its own order book with prices 0.01 to 0.99 and pays $1 if that team wins.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass

from .http import get_json

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
DATA = "https://data-api.polymarket.com"

# Polymarket series ids for game events (gamma /sports)
SERIES = {"nfl": 12185, "ncaaf": 12756, "nba": 10345, "wnba": 10105, "ncaab": 39,
          "mlb": 3, "nhl": 10346}


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def game_events(league: str, start_from: float, start_to: float, closed: bool = False) -> list[dict]:
    """A league's game events starting between two unix times. A game
    event's endDate is its scheduled start."""
    return get_json(f"{GAMMA}/events", {"series_id": SERIES[league], "closed": str(closed).lower(),
                                        "limit": 500, "end_date_min": _iso(start_from),
                                        "end_date_max": _iso(start_to)})


def moneyline(event: dict) -> dict | None:
    """The event's who-wins market, if it has one."""
    for m in event.get("markets") or []:
        if m.get("sportsMarketType") == "moneyline" and m.get("clobTokenIds"):
            return m
    return None


def outcomes(market: dict) -> list[tuple[str, str]]:
    """[(outcome name, token id)] in the market's order."""
    names = json.loads(market["outcomes"]) if isinstance(market["outcomes"], str) else market["outcomes"]
    toks = (json.loads(market["clobTokenIds"]) if isinstance(market["clobTokenIds"], str)
            else market["clobTokenIds"])
    return list(zip(names, toks))


@dataclass
class Book:
    bid: float | None
    bid_size: float
    ask: float | None
    ask_size: float
    tick: float = 0.01
    min_size: float = 5.0


def _book(b: dict) -> Book:
    bids = [(float(x["price"]), float(x["size"])) for x in b.get("bids") or []]
    asks = [(float(x["price"]), float(x["size"])) for x in b.get("asks") or []]
    hi = max(bids) if bids else None
    lo = min(asks) if asks else None
    return Book(hi[0] if hi else None, hi[1] if hi else 0.0, lo[0] if lo else None,
                lo[1] if lo else 0.0, float(b.get("tick_size") or 0.01),
                float(b.get("min_order_size") or 5))


def orderbook(token_id: str) -> Book:
    return _book(get_json(f"{CLOB}/book", {"token_id": token_id}))


def trades(condition_ids: list[str] | None = None, min_usd: float | None = None,
           limit: int = 500) -> list[dict]:
    """Recent trades (newest first), optionally only these markets and only
    trades worth at least `min_usd`. Taker side only, so each fill counts once."""
    params = {"limit": limit, "takerOnly": "true"}
    if condition_ids:
        params["market"] = ",".join(condition_ids)
    if min_usd:
        params.update(filterType="CASH", filterAmount=min_usd)
    return get_json(f"{DATA}/trades", params)


def price_history(token_id: str, start_ts: int, end_ts: int, fidelity_min: int = 1) -> list[dict]:
    return get_json(f"{CLOB}/prices-history", {"market": token_id, "startTs": start_ts,
                                               "endTs": end_ts, "fidelity": fidelity_min}
                    ).get("history") or []


def event_url(slug: str) -> str:
    return f"https://polymarket.com/event/{slug}"


class PolymarketTrader:
    """Orders through the official SDK: `pip install polymarket-client`.

    `private_key` signs orders; `wallet` is the Polymarket account address
    shown in the profile menu. Never give this key withdrawal-capable reuse
    elsewhere: anyone with it controls the wallet.
    """

    def __init__(self, private_key: str, wallet: str | None = None):
        from polymarket import SecureClient
        self.c = SecureClient.create(private_key=private_key, wallet=wallet)

    def place_bid(self, token_id: str, price: float, shares: float,
                  expires: int | None = None) -> str:
        """Resting post-only buy. expires: unix seconds; Polymarket ends GTD
        orders a minute early and needs them at least 3 minutes out."""
        exp = int(expires) + 60 if expires and expires > time.time() + 180 else None
        r = self.c.place_limit_order(token_id=token_id, price=f"{price:.2f}",
                                     size=f"{shares:.2f}", side="BUY", post_only=True,
                                     expiration=exp)
        if not r.ok:
            raise RuntimeError(f"polymarket rejected bid: {r.code} {r.message}")
        return r.order_id

    def buy_now(self, token_id: str, max_price: float, shares: float) -> tuple[float, float]:
        """Fill-or-kill buy of at least `shares` at no more than `max_price`.
        Market buys are sized in dollars, so this spends shares x max_price
        (fewer dollars' worth of price, more shares, if the book is cheaper).
        Returns (shares bought, dollars spent); (0, 0) if it couldn't fill."""
        r = self.c.place_market_order(token_id=token_id, side="BUY",
                                      amount=f"{shares * max_price:.2f}",
                                      max_price=f"{max_price:.2f}", order_type="FOK")
        if not r.ok:
            return 0.0, 0.0
        # a buy gives dollars (making) and takes shares (taking)
        return float(r.taking_amount or 0), float(r.making_amount or 0)

    def cancel(self, order_id: str) -> None:
        self.c.cancel_order(order_id=order_id)

    def check(self) -> str:
        """Read-only: cash and whether the wallet's trading approvals are set."""
        cash = self.c.get_balance_allowance(asset_type="COLLATERAL").balance / 1e6
        ok = self.c.get_trading_approvals_state().is_fully_approved
        return (f"polymarket: {cash:.2f} USD available, trading approvals "
                + ("set" if ok else "MISSING (place one small trade on polymarket.com first)"))

    def order(self, order_id: str) -> dict:
        o = self.c.get_order(order_id=order_id)
        return {"status": str(o.status), "filled": float(o.size_matched),
                "size": float(o.original_size), "price": float(o.price)}
