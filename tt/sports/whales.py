"""Whale scanner: big trades on today's game-winner markets, both venues.

Each trade is shown with the same team's price on the other venue, so a
panicked dump stands out: "SELL Eagles 0.18 on Polymarket, Kalshi 0.23/0.24".
"""

from __future__ import annotations

import csv
import os
import shutil
import time
from dataclasses import dataclass

from . import games as G
from . import kalshi, polymarket


@dataclass
class Whale:
    ts: int
    venue: str
    league: str
    game: str
    team: str
    side: str        # BUY or SELL of this team's contract (taker's side)
    price: float     # this team's price
    contracts: float
    usd: float       # contracts x price
    url: str
    other: str       # same team on the other venue, "bid/ask" or "-"
    gap: float | None  # price minus the other venue's mid; negative = cheaper here
    key: str = ""


def _other(gm: G.GameMarkets, abbr: str, venue: str) -> tuple[str, float | None]:
    o = gm.leg(abbr, "polymarket" if venue == "kalshi" else "kalshi")
    if not o or o.bid is None or o.ask is None:
        return "-", None
    return f"{o.venue[0].upper()} {o.bid:.2f}/{o.ask:.2f}", o.mid


def _group(rows: list[tuple[str, int, str, float, float]]) -> list[tuple[str, int, str, float, float]]:
    """One taker order sweeping several resting orders shows up as several
    trades in the same second. Merge (market, second, side) into one trade
    with the volume-weighted price, so split orders still count as whales."""
    acc: dict[tuple[str, int, str], list[float]] = {}
    for mid, ts, side, price, n in rows:
        a = acc.setdefault((mid, ts, side), [0.0, 0.0])
        a[0] += n
        a[1] += n * price
    return [(k[0], k[1], k[2], a[1] / a[0], a[0]) for k, a in acc.items() if a[0] > 0]


def collect(index: list[G.GameMarkets], since: int, min_usd: float,
            price_band: tuple[float, float] = (0.03, 0.97),
            quotes_ts: float | None = None) -> list[Whale]:
    """Trades worth at least `min_usd` since unix time `since`, oldest first.
    Prices outside `price_band` (games already decided) are skipped. The gap
    to the other venue is only given for trades within 90s of `quotes_ts`,
    when the index's quotes were taken; older trades met a different market."""
    legs: dict[str, tuple[G.GameMarkets, str, G.Leg]] = {}
    rows: list[tuple[str, int, str, float, float]] = []
    for gm in index:
        for (abbr, venue), leg in gm.legs.items():
            legs[leg.market_id] = (gm, abbr, leg)
            if venue != "kalshi":
                continue
            for t in kalshi.trades(leg.market_id, min_ts=since, limit=1000, max_pages=3):
                side, _, _ = kalshi.trade_value(t)
                rows.append((leg.market_id, int(G.parse_ts(t["created_time"][:19] + "Z") or 0),
                             side, kalshi.f(t.get("yes_price_dollars")), kalshi.f(t.get("count_fp"))))
    conds = sorted({leg.group_id for _, _, leg in legs.values() if leg.venue == "polymarket"})
    for i in range(0, len(conds), 40):
        # a lower floor than min_usd so pieces of a split order get merged too
        for t in polymarket.trades(conds[i:i + 40], min_usd=min_usd / 5, limit=500):
            if str(t.get("asset")) in legs and int(t["timestamp"]) >= since:
                rows.append((str(t["asset"]), int(t["timestamp"]), t["side"],
                             float(t["price"]), float(t["size"])))
    out = []
    for market_id, ts, side, price, n in _group(rows):
        if n * price < min_usd or not price_band[0] <= price <= price_band[1]:
            continue
        gm, abbr, leg = legs[market_id]
        other, mid = _other(gm, abbr, leg.venue)
        fresh = mid is not None and quotes_ts is not None and abs(ts - quotes_ts) <= 90
        out.append(Whale(ts, leg.venue, gm.game.league, gm.game.title, abbr, side, price, n,
                         n * price, leg.url, other, price - mid if fresh else None,
                         f"{market_id}:{ts}:{side}"))
    return sorted(out, key=lambda w: w.ts)


def fmt(w: Whale, width: int | None = None) -> str:
    """One line on a wide terminal; three short lines on a phone."""
    width = width or shutil.get_terminal_size((160, 24)).columns
    gap = f"{w.gap * 100:+.0f}c" if w.gap is not None else ""
    when = time.strftime('%H:%M:%S', time.gmtime(w.ts))
    if width < 150:
        return (f"{when} {w.venue} {w.league} {w.game}\n"
                f"   {w.side} {w.team} @{w.price:.2f}  ${w.usd:,.0f}  {w.other} {gap}\n   {w.url}")
    return (f"{when} {w.venue[:4]:4s} {w.league:5s} "
            f"{w.game[:26]:26s} {w.side:4s} {w.team:4s} @{w.price:.2f} ${w.usd:>9,.0f}  "
            f"{w.other:13s} {gap:>5s}  {w.url}")


def run(leagues: list[str], venues: list[str], min_usd: float = 1000, every: int = 15,
        last: int = 25, once: bool = False, log_path: str = "logs/whales.csv") -> None:
    seen: set[str] = set()
    recent: list[Whale] = []
    since = int(time.time()) - 15 * 60
    index: list[G.GameMarkets] = []
    os.makedirs(os.path.dirname(log_path) or ".", exist_ok=True)
    print(f"watching {', '.join(leagues)} on {', '.join(venues)} for trades >= ${min_usd:,.0f}"
          " (UTC times; gap = this price minus the other venue's mid)")
    while True:
        try:
            # reload every round: the quotes double as the "other venue" price
            index = [gm for gm in G.load(leagues, venues) if gm.legs]
            new = [w for w in collect(index, since, min_usd, quotes_ts=time.time())
                   if w.key not in seen]
            for w in new:
                seen.add(w.key)
                recent.append(w)
                print(fmt(w), flush=True)
            if new:
                with open(log_path, "a", newline="") as fh:
                    cw = csv.writer(fh)
                    for w in new:
                        cw.writerow([w.ts, w.venue, w.league, w.game, w.team, w.side, w.price,
                                     w.contracts, round(w.usd, 2), w.other, w.gap, w.url])
                recent = recent[-last:]
                print(f"-- last {len(recent)} whales "
                      f"({len(index)} games watched) " + "-" * 40)
                for w in recent:
                    print("   " + fmt(w).replace("\n", "\n   "))
            since = max(since, int(time.time()) - 120)  # overlap; `seen` dedupes
            if once:
                if not new:
                    print(f"no trades >= ${min_usd:,.0f} in the last 15 minutes "
                          f"across {len(index)} games")
                return
            time.sleep(every)
        except KeyboardInterrupt:
            return
        except Exception as e:  # noqa: BLE001 - a scanner should keep going
            print(f"scanner error: {type(e).__name__}: {e}; retrying in {every * 2}s", flush=True)
            if once:
                raise
            time.sleep(every * 2)
