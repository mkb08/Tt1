"""Command line for the sports tools (wired into `python -m tt`)."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

from . import games as G
from .fees import kalshi_fee, poly_fee


def _list(s: str) -> list[str]:
    return [x.strip().lower() for x in s.split(",") if x.strip()]


def locked_edge(a: G.Leg, b: G.Leg, n: float = 10) -> float | None:
    """Profit per share from buying team A on one venue and team B on the
    other at their asks, after taker fees. Positive = an arbitrage."""
    if a.ask is None or b.ask is None:
        return None

    def fee(leg: G.Leg, p: float) -> float:
        if leg.venue == "kalshi":
            return kalshi_fee(n, p, multiplier=leg.fee.get("multiplier", 1.0)) / n
        return poly_fee(n, p, leg.fee) / n

    return 1 - a.ask - b.ask - fee(a, a.ask) - fee(b, b.ask)


def cmd_games(args) -> None:
    index = G.load(_list(args.leagues), _list(args.venues), args.ahead)
    best = None
    for gm in index:
        g = gm.game
        print(f"\n{g.league.upper()} {g.title}  [{g.phase()}]  "
              f"starts {datetime.fromtimestamp(g.start, timezone.utc):%a %H:%M} UTC")
        for t in g.teams:
            parts = []
            for v in ("kalshi", "polymarket"):
                leg = gm.leg(t.abbr, v)
                if leg and args.live:
                    G.refresh(leg)
                parts.append(f"{v}: {leg.bid}/{leg.ask}" if leg else f"{v}: -")
            print(f"   {t.abbr:4s} {t.name:28s} " + "   ".join(parts))
        a, b = (t.abbr for t in g.teams)
        for x, y in ((a, b), (b, a)):
            lk, lp = gm.leg(x, "kalshi"), gm.leg(y, "polymarket")
            if lk and lp:
                e = locked_edge(lk, lp)
                if e is not None:
                    print(f"   buy {x} on kalshi + {y} on polymarket: {e:+.3f} per $1 after fees")
                    if best is None or e > best[0]:
                        best = (e, g.title, x, y)
    if not index:
        print("no games in the window")
    elif best:
        print(f"\nbest cross-venue lock now: {best[0]:+.3f} ({best[1]}: {best[2]} on kalshi + "
              f"{best[3]} on polymarket). Below zero means no arbitrage.")
    if not args.live:
        print("\nquotes are listing snapshots; add --live to read each order book")


def cmd_whales(args) -> None:
    from .whales import run
    run(_list(args.leagues), _list(args.venues), args.min_usd, args.every, args.last, args.once)


def cmd_stink(args) -> None:
    from .stink import StinkBot
    with open(args.config) as fh:
        cfg = json.load(fh)
    if args.status:
        print(StinkBot({**cfg, "mode": "paper"}).summary())
        return
    if args.check:
        from .venues import live_venues
        for v in live_venues({"venues": ["polymarket", "kalshi"], **cfg}).values():
            print(v.check())
        print("keys work (read-only check; nothing was sent)")
        return
    if args.dry_run:
        bot = StinkBot({**cfg, "state_file": None, "mode": "paper"})
        index = G.load(bot.cfg["leagues"], bot.cfg["venues"], bot.cfg["ahead_hours"])
        plan = bot.plan(index)
        print("\n".join(plan) or "nothing to bid on right now (no games before halftime "
                                  "in the window)")
        total = sum(float(line.split("($")[1].split(")")[0]) for line in plan if "($" in line)
        print(f"\n{len(plan)} lines, about ${total:.2f} of bids (cap: max_total_usd "
              f"{bot.cfg['max_total_usd']}). Nothing was sent.")
        return
    venues = None
    if cfg.get("mode", "paper") == "live":
        if not args.i_accept_the_risk:
            sys.exit("live mode places real orders: re-run with --i-accept-the-risk")
        from .venues import live_venues
        venues = live_venues(cfg)
    bot = StinkBot(cfg, venues)
    if args.once:
        bot.step()
        print(bot.summary())
    else:
        bot.run()


def add_parsers(sub) -> None:
    leagues = "nfl,ncaaf,nba,wnba,ncaab,mlb,nhl"
    p = sub.add_parser("games", help="sports: today's games, both venues' prices, cross-venue gaps")
    p.add_argument("--leagues", default="nfl,nba,mlb,nhl", help=f"any of {leagues}")
    p.add_argument("--venues", default="kalshi,polymarket")
    p.add_argument("--ahead", type=float, default=18, help="hours ahead to include")
    p.add_argument("--live", action="store_true", help="read each order book (slower)")
    p.set_defaults(fn=cmd_games)

    p = sub.add_parser("whales", help="sports: big trades on game markets, both venues")
    p.add_argument("--leagues", default="nfl,nba,mlb,nhl")
    p.add_argument("--venues", default="kalshi,polymarket")
    p.add_argument("--min-usd", type=float, default=1000)
    p.add_argument("--every", type=int, default=15, help="seconds between scans")
    p.add_argument("--last", type=int, default=25, help="rows in the recap table")
    p.add_argument("--once", action="store_true")
    p.set_defaults(fn=cmd_whales)

    p = sub.add_parser("stink", help="sports: stink-bid bot (paper by default)")
    p.add_argument("--config", default="stink.json")
    p.add_argument("--dry-run", action="store_true", help="print the bids it would place now")
    p.add_argument("--status", action="store_true", help="P&L, resting bids, open positions")
    p.add_argument("--once", action="store_true", help="one pass, then exit")
    p.add_argument("--check", action="store_true", help="verify API keys and balances, read-only")
    p.add_argument("--i-accept-the-risk", action="store_true")
    p.set_defaults(fn=cmd_stink)
