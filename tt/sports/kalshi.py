"""Kalshi: public market data (no key) and an authenticated trader.

Prices are dollars (0.01 to 0.99), counts are contracts paying $1.
Game-winner events hold one market per team, e.g. KXNFLGAME-26OCT11PHIJAC
has ...-PHI ("Philadelphia wins") and ...-JAC.
"""

from __future__ import annotations

import base64
import re
import time
import urllib.parse
from dataclasses import dataclass

from .http import get_json, send_json

PROD = "https://api.elections.kalshi.com/trade-api/v2"
DEMO = "https://demo-api.kalshi.co/trade-api/v2"

# game-winner series per league; titles feed the website link
SERIES = {"nfl": "KXNFLGAME", "ncaaf": "KXNCAAFGAME", "nba": "KXNBAGAME",
          "wnba": "KXWNBAGAME", "ncaab": "KXNCAAMBGAME", "mlb": "KXMLBGAME",
          "nhl": "KXNHLGAME"}
SERIES_TITLES = {"KXNFLGAME": "NFL Game", "KXNCAAFGAME": "College Football Game",
                 "KXNBAGAME": "NBA Game", "KXWNBAGAME": "Women's Pro Basketball Game",
                 "KXNCAAMBGAME": "Men's College Basketball Men's Game",
                 "KXMLBGAME": "Professional Baseball Game", "KXNHLGAME": "NHL Game"}
LEAGUE_OF_SERIES = {v: k for k, v in SERIES.items()}


def f(x) -> float:
    """Kalshi sends fixed-point strings; missing means 0."""
    return float(x) if x not in (None, "") else 0.0


def _paged(path: str, key: str, params: dict, base: str = PROD, max_pages: int = 100):
    params = dict(params)
    for _ in range(max_pages):
        page = get_json(base + path, params)
        yield from page.get(key) or []
        cursor = page.get("cursor")
        if not cursor:
            return
        params["cursor"] = cursor


def events(series_ticker: str, status: str = "open", base: str = PROD) -> list[dict]:
    """Events with their markets (and top-of-book quotes) nested."""
    return list(_paged("/events", "events", {"series_ticker": series_ticker, "status": status,
                                              "with_nested_markets": "true", "limit": 200},
                       base))


def markets(base: str = PROD, **params) -> list[dict]:
    return list(_paged("/markets", "markets", {"limit": 1000, **params}, base))


def series_info(series_ticker: str, base: str = PROD) -> dict:
    return get_json(f"{base}/series/{series_ticker}").get("series", {})


@dataclass
class Book:
    bid: float | None
    bid_size: float
    ask: float | None
    ask_size: float


def orderbook(ticker: str, base: str = PROD) -> Book:
    """Kalshi lists only bids: YES bids, and NO bids. A NO bid at x is a YES
    ask at 1 - x."""
    ob = get_json(f"{base}/markets/{ticker}/orderbook").get("orderbook_fp") or {}
    yes = [(f(p), f(s)) for p, s in ob.get("yes_dollars") or []]
    no = [(f(p), f(s)) for p, s in ob.get("no_dollars") or []]
    bid = max(yes) if yes else None
    no_best = max(no) if no else None
    return Book(bid[0] if bid else None, bid[1] if bid else 0.0,
                round(1 - no_best[0], 4) if no_best else None, no_best[1] if no_best else 0.0)


def quote_from_market(m: dict) -> Book:
    """Top of book as embedded in /events and /markets responses."""
    bid, ask = f(m.get("yes_bid_dollars")), f(m.get("yes_ask_dollars"))
    return Book(bid or None, f(m.get("yes_bid_size_fp")),
                ask if 0 < ask < 1 else None, f(m.get("yes_ask_size_fp")))


def trades(ticker: str | None = None, min_ts: int | None = None, limit: int = 1000,
           max_pages: int = 1, base: str = PROD) -> list[dict]:
    """Recent trades, newest first. Without `ticker`: the whole exchange."""
    params = {"limit": limit, "ticker": ticker, "min_ts": min_ts}
    return list(_paged("/markets/trades", "trades", params, base, max_pages))


def trade_value(t: dict) -> tuple[str, float, float]:
    """(taker action on YES, price of the side the taker bought, dollars)."""
    count = f(t.get("count_fp"))
    if t.get("taker_side") == "no":
        price = f(t.get("no_price_dollars"))
        return "SELL", price, count * price
    price = f(t.get("yes_price_dollars"))
    return "BUY", price, count * price


def candles(series_ticker: str, ticker: str, start_ts: int, end_ts: int,
            period_min: int = 1, base: str = PROD) -> list[dict]:
    """OHLC of trades plus bid/ask, `period_min` minutes each, at most 5000."""
    return get_json(f"{base}/series/{series_ticker}/markets/{ticker}/candlesticks",
                    {"start_ts": start_ts, "end_ts": end_ts,
                     "period_interval": period_min}).get("candlesticks") or []


def milestones(base: str = PROD, max_pages: int = 50, **params) -> list[dict]:
    """Scheduled games with real start times (`start_date`)."""
    return list(_paged("/milestones", "milestones", {"limit": 200, **params}, base, max_pages))


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def event_url(event_ticker: str) -> str:
    series = event_ticker.split("-")[0]
    title = SERIES_TITLES.get(series, series)
    return f"https://kalshi.com/markets/{series.lower()}/{slug(title)}/{event_ticker.lower()}"


def ticker_date(event_ticker: str) -> str | None:
    """'KXNFLGAME-26OCT11PHIJAC' -> '2026-10-11' (the US date of the game)."""
    m = re.search(r"-(\d{2})([A-Z]{3})(\d{2})", event_ticker)
    if not m:
        return None
    months = "JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split()
    if m.group(2) not in months:
        return None
    return f"20{m.group(1)}-{months.index(m.group(2)) + 1:02d}-{m.group(3)}"


class KalshiTrader:
    """Signed requests (RSA-PSS or Ed25519 key from Kalshi's API Keys page).

    Needs `pip install cryptography` (Termux: `pkg install python-cryptography`).
    Use base=DEMO with a demo.kalshi.co account to test with fake money.
    """

    def __init__(self, key_id: str, private_key_pem: bytes, base: str = PROD):
        from cryptography.hazmat.primitives import serialization
        self.key_id, self.base = key_id, base
        self.key = serialization.load_pem_private_key(private_key_pem, password=None)
        self.root = urllib.parse.urlparse(base).path  # "/trade-api/v2"

    def _headers(self, method: str, path: str) -> dict:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        ts = str(int(time.time() * 1000))
        msg = f"{ts}{method}{self.root}{path.split('?')[0]}".encode()
        if isinstance(self.key, Ed25519PrivateKey):
            sig = self.key.sign(msg)
        else:
            sig = self.key.sign(msg, padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                                                 salt_length=padding.PSS.DIGEST_LENGTH),
                                hashes.SHA256())
        return {"KALSHI-ACCESS-KEY": self.key_id, "KALSHI-ACCESS-TIMESTAMP": ts,
                "KALSHI-ACCESS-SIGNATURE": base64.b64encode(sig).decode()}

    def _get(self, path: str, params: dict | None = None):
        return get_json(self.base + path, params, headers=self._headers("GET", path), retries=2)

    def _send(self, method: str, path: str, body: dict | None = None):
        return send_json(method, self.base + path, body, self._headers(method, path))

    def balance(self) -> float:
        return f(self._get("/portfolio/balance").get("balance_dollars"))

    def place_bid(self, ticker: str, price: float, count: float, client_id: str,
                  post_only: bool = True, expires: int | None = None) -> dict:
        """Resting buy of YES. post_only: rejected instead of taking liquidity.
        expires: unix seconds when the exchange cancels it by itself."""
        body = {"ticker": ticker, "client_order_id": client_id, "side": "bid",
                "count": f"{count:.2f}", "price": f"{price:.4f}",
                "time_in_force": "good_till_canceled", "post_only": post_only,
                "self_trade_prevention_type": "maker", "cancel_order_on_pause": True}
        if expires:
            body["expiration_time"] = int(expires)
        return self._send("POST", "/portfolio/events/orders", body)

    def buy_now(self, ticker: str, max_price: float, count: float, client_id: str) -> dict:
        """Immediate-or-cancel buy of YES at up to `max_price`."""
        return self._send("POST", "/portfolio/events/orders", {
            "ticker": ticker, "client_order_id": client_id, "side": "bid",
            "count": f"{count:.2f}", "price": f"{max_price:.4f}",
            "time_in_force": "immediate_or_cancel",
            "self_trade_prevention_type": "taker_at_cross"})

    def cancel(self, order_id: str, ticker: str) -> dict:
        return self._send("DELETE", f"/portfolio/events/orders/{order_id}"
                                    f"?market_ticker={urllib.parse.quote(ticker)}")

    def order(self, order_id: str) -> dict:
        """Order with fill_count_fp, remaining_count_fp, status, fees."""
        return self._get(f"/portfolio/orders/{order_id}").get("order", {})

    def positions(self) -> dict[str, float]:
        out, params = {}, {"limit": 200}
        for _ in range(50):
            page = self._get("/portfolio/positions", params)
            for p in page.get("market_positions") or []:
                if f(p.get("position_fp")):
                    out[p["ticker"]] = f(p.get("position_fp"))
            if not page.get("cursor"):
                break
            params["cursor"] = page["cursor"]
        return out
