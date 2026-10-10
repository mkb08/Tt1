"""Bybit v5 public market data with the standard library only (no API key,
no ccxt): candles, last price and funding, funding history and the
instrument's trading rules.

Symbols use ccxt style: "BTC/USDT:USDT" is the USDT perpetual (category
"linear"), "BTC/USDT" is spot.
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request

from .data import Candle

BASE = "https://api.bybit.com"
INTERVALS = {"1m": "1", "5m": "5", "15m": "15", "30m": "30", "1h": "60", "2h": "120",
             "4h": "240", "6h": "360", "12h": "720", "1d": "D", "1w": "W"}


def split_symbol(symbol: str) -> tuple[str, str]:
    """ccxt symbol -> (category, Bybit id)."""
    if ":" in symbol:
        return "linear", symbol.split(":")[0].replace("/", "").upper()
    if "/" in symbol:
        return "spot", symbol.replace("/", "").upper()
    return "linear", symbol.upper()


def _get(path: str, params: dict) -> dict:
    url = BASE + path + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "tt/0.1"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = json.load(resp)
    if body.get("retCode") != 0:
        raise RuntimeError(f"bybit {path}: {body.get('retCode')} {body.get('retMsg')}")
    return body["result"]


def klines(symbol: str, timeframe: str, end: int | None = None,
           limit: int = 1000) -> list[Candle]:
    """Up to `limit` candles ending at `end` (ms), oldest first."""
    category, sym = split_symbol(symbol)
    params = {"category": category, "symbol": sym, "interval": INTERVALS[timeframe],
              "limit": min(limit, 1000)}
    if end is not None:
        params["end"] = end
    rows = _get("/v5/market/kline", params)["list"]  # newest first
    return [Candle(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]))
            for r in reversed(rows)]


def history(symbol: str, timeframe: str, days: float) -> list[Candle]:
    """`days` of candles, paging backwards from now."""
    since = int(time.time() * 1000 - days * 86_400_000)
    out: dict[int, Candle] = {}
    end = None
    while True:
        batch = klines(symbol, timeframe, end)
        if not batch:
            break
        for c in batch:
            if c.ts >= since:
                out[c.ts] = c
        if batch[0].ts <= since or len(batch) < 2:
            break
        end = batch[0].ts - 1
        time.sleep(0.15)
    return sorted(out.values(), key=lambda c: c.ts)


def ticker(symbol: str) -> dict:
    category, sym = split_symbol(symbol)
    t = _get("/v5/market/tickers", {"category": category, "symbol": sym})["list"][0]
    return {"last": float(t["lastPrice"]),
            "funding_rate": float(t["fundingRate"]) if t.get("fundingRate") else None,
            "next_funding_time": int(t["nextFundingTime"]) if t.get("nextFundingTime") else None}


def funding_history(symbol: str, days: float) -> list[tuple[int, float]]:
    """[(timestamp ms, rate)] oldest first. Positive rate: longs pay shorts."""
    category, sym = split_symbol(symbol)
    if category == "spot":
        raise ValueError("spot has no funding; use the perpetual symbol, e.g. BTC/USDT:USDT")
    since = int(time.time() * 1000 - days * 86_400_000)
    out: dict[int, float] = {}
    end = int(time.time() * 1000)
    while True:
        rows = _get("/v5/market/funding/history", {"category": category, "symbol": sym,
                                                   "endTime": end, "limit": 200})["list"]
        if not rows:
            break
        stamps = []
        for r in rows:
            ts = int(r["fundingRateTimestamp"])
            stamps.append(ts)
            if ts >= since:
                out[ts] = float(r["fundingRate"])
        oldest = min(stamps)
        if oldest <= since or len(rows) < 2 or oldest >= end:
            break
        end = oldest - 1
        time.sleep(0.15)
    return sorted(out.items())


def instrument(symbol: str) -> dict:
    """Trading rules, normalised across spot and perpetuals."""
    category, sym = split_symbol(symbol)
    info = _get("/v5/market/instruments-info", {"category": category, "symbol": sym})["list"][0]
    lot = info.get("lotSizeFilter", {})
    if category == "spot":
        return {"category": category, "min_qty": float(lot.get("minOrderQty") or 0),
                "qty_step": float(lot.get("basePrecision") or 0),
                "min_notional": float(lot.get("minOrderAmt") or 0),
                "max_leverage": 1.0, "funding_interval_h": None,
                "status": info.get("status")}
    return {"category": category, "min_qty": float(lot["minOrderQty"]),
            "qty_step": float(lot["qtyStep"]),
            "min_notional": float(lot.get("minNotionalValue") or 0),
            "max_leverage": float(info.get("leverageFilter", {}).get("maxLeverage") or 1),
            "funding_interval_h": (int(info["fundingInterval"]) / 60
                                   if info.get("fundingInterval") else None),
            "status": info.get("status")}
