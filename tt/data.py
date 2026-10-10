"""Market data: the Candle type, CSV storage, public OHLCV download and
synthetic data for offline tests."""

from __future__ import annotations

import csv
import json
import math
import random
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass

TIMEFRAME_MS = {
    "1m": 60_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000,
    "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000, "6h": 21_600_000,
    "12h": 43_200_000, "1d": 86_400_000, "1w": 604_800_000,
}

# Market-data-only Binance mirror: no API key needed, reachable from more
# regions than api.binance.com.
BINANCE_DATA_URL = "https://data-api.binance.vision/api/v3/klines"


@dataclass(frozen=True)
class Candle:
    ts: int  # open time, milliseconds since epoch (UTC)
    open: float
    high: float
    low: float
    close: float
    volume: float


def load_csv(path: str) -> list[Candle]:
    with open(path, newline="") as f:
        rows = csv.DictReader(f)
        candles = [
            Candle(int(float(r["ts"])), float(r["open"]), float(r["high"]),
                   float(r["low"]), float(r["close"]), float(r["volume"]))
            for r in rows
        ]
    candles.sort(key=lambda c: c.ts)
    return candles


def save_csv(path: str, candles: list[Candle]) -> None:
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ts", "open", "high", "low", "close", "volume"])
        for c in candles:
            w.writerow([c.ts, c.open, c.high, c.low, c.close, c.volume])


def _binance_symbol(symbol: str) -> str:
    return symbol.replace("/", "").replace(":USDT", "").upper()


def _fetch_binance_page(symbol: str, timeframe: str, since: int | None,
                        limit: int) -> list[Candle]:
    params = {"symbol": _binance_symbol(symbol), "interval": timeframe,
              "limit": min(limit, 1000)}
    if since is not None:
        params["startTime"] = since
    url = BINANCE_DATA_URL + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "tt/0.1"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        rows = json.load(resp)
    return [Candle(int(r[0]), float(r[1]), float(r[2]), float(r[3]),
                   float(r[4]), float(r[5])) for r in rows]


def _fetch_ccxt_page(exchange, symbol: str, timeframe: str,
                     since: int | None, limit: int) -> list[Candle]:
    rows = exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=limit)
    return [Candle(int(r[0]), *map(float, r[1:6])) for r in rows]


def fetch_history(symbol: str, timeframe: str, days: float,
                  exchange: str = "binance") -> list[Candle]:
    """Download `days` of history, paging forward from the start.

    Binance data comes from its public mirror with the stdlib alone; any
    other exchange needs ccxt.
    """
    if timeframe not in TIMEFRAME_MS:
        raise ValueError(f"unsupported timeframe {timeframe!r}")
    step = TIMEFRAME_MS[timeframe]
    since = int(time.time() * 1000 - days * 86_400_000)

    if exchange == "binance":
        page = lambda s: _fetch_binance_page(symbol, timeframe, s, 1000)  # noqa: E731
    else:
        ex = _ccxt_public(exchange)
        page = lambda s: _fetch_ccxt_page(ex, symbol, timeframe, s, 1000)  # noqa: E731

    out: dict[int, Candle] = {}
    while True:
        batch = page(since)
        if not batch:
            break
        for c in batch:
            out[c.ts] = c
        nxt = batch[-1].ts + step
        if nxt <= since or len(batch) < 2:
            break
        since = nxt
        time.sleep(0.2)
    return drop_open_candle(sorted(out.values(), key=lambda c: c.ts), timeframe)


def fetch_recent(symbol: str, timeframe: str, limit: int = 500,
                 exchange: str = "binance", ccxt_exchange=None) -> list[Candle]:
    """Latest closed candles, for live and paper trading."""
    if ccxt_exchange is not None:
        candles = _fetch_ccxt_page(ccxt_exchange, symbol, timeframe, None, limit)
    elif exchange == "binance":
        candles = _fetch_binance_page(symbol, timeframe, None, limit)
    else:
        candles = _fetch_ccxt_page(_ccxt_public(exchange), symbol, timeframe, None, limit)
    return drop_open_candle(candles, timeframe)


def _ccxt_public(exchange: str):
    try:
        import ccxt  # type: ignore
    except ImportError:
        raise SystemExit(f"install ccxt for {exchange} data (see install_termux.sh)") from None
    return getattr(ccxt, exchange)({"enableRateLimit": True})


def fetch_price(symbol: str, exchange: str = "binance", ccxt_exchange=None) -> float:
    """Last traded price."""
    if ccxt_exchange is not None:
        return float(ccxt_exchange.fetch_ticker(symbol)["last"])
    url = ("https://data-api.binance.vision/api/v3/ticker/price?symbol="
           + _binance_symbol(symbol))
    req = urllib.request.Request(url, headers={"User-Agent": "tt/0.1"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return float(json.load(resp)["price"])


def drop_open_candle(candles: list[Candle], timeframe: str,
                     now_ms: int | None = None) -> list[Candle]:
    """Remove the still-forming last candle so signals use closed bars only."""
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    if candles and candles[-1].ts + TIMEFRAME_MS[timeframe] > now_ms:
        return candles[:-1]
    return candles


def synthetic(n: int = 3000, seed: int = 7, start_price: float = 100.0,
              timeframe: str = "4h") -> list[Candle]:
    """Random walk with alternating trending and choppy regimes.

    For offline tests and demos only; results on it say nothing about
    real markets.
    """
    rng = random.Random(seed)
    step = TIMEFRAME_MS[timeframe]
    ts = 1_600_000_000_000
    price = start_price
    out = []
    drift = 0.0
    for i in range(n):
        if i % 250 == 0:
            drift = rng.choice([0.002, -0.0015, 0.0, 0.0])
        vol = 0.012
        ret = drift + rng.gauss(0, vol)
        o = price
        c = max(0.01, o * math.exp(ret))
        h = max(o, c) * (1 + abs(rng.gauss(0, vol / 2)))
        lo = min(o, c) * (1 - abs(rng.gauss(0, vol / 2)))
        out.append(Candle(ts + i * step, o, h, lo, c, rng.uniform(100, 1000)))
        price = c
    return out
