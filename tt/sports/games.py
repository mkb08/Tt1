"""One view of a game across venues.

ESPN gives the canonical game (teams, start, clock). Each venue's winner
market is matched to it by date and team names, team by team:
  Kalshi      "Philadelphia" (yes_sub_title), ticker suffix "PHI"
  Polymarket  outcome "Eagles", event team "Philadelphia Eagles" / "phi"
  ESPN        "Philadelphia Eagles", "PHI", "Eagles"
A match must be unambiguous for both teams or it is skipped, so the bot never
buys the wrong team.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from . import espn, kalshi, polymarket
from .espn import Game, Team


@dataclass
class Leg:
    """One team's winner contract on one venue."""
    venue: str          # "kalshi" or "polymarket"
    market_id: str      # Kalshi ticker or Polymarket token id
    group_id: str       # Kalshi event ticker or Polymarket condition id
    label: str
    url: str
    bid: float | None = None
    ask: float | None = None
    bid_size: float = 0.0
    ask_size: float = 0.0
    tick: float = 0.01
    min_size: float = 1.0
    fee: dict = field(default_factory=dict)  # kalshi: {"multiplier"}, poly: feeSchedule

    @property
    def mid(self) -> float | None:
        if self.bid and self.ask and self.ask > self.bid:
            return (self.bid + self.ask) / 2
        return None


@dataclass
class GameMarkets:
    game: Game
    legs: dict[tuple[str, str], Leg] = field(default_factory=dict)  # (team abbr, venue)

    def leg(self, abbr: str, venue: str) -> Leg | None:
        return self.legs.get((abbr, venue))

    def opponent(self, abbr: str) -> str:
        a, b = (t.abbr for t in self.game.teams)
        return b if abbr == a else a

    def fair(self, abbr: str) -> float | None:
        """Consensus price of `abbr` winning: average of venue mids."""
        mids = [l.mid for (t, _), l in self.legs.items() if t == abbr and l.mid]
        return sum(mids) / len(mids) if mids else None


def norm(s: str) -> str:
    s = s.lower().replace("&", " and ").replace("st.", "state")
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def score(team: Team, label: str, abbr: str | None = None) -> int:
    """How well a venue's team label fits an ESPN team: 3 exact, 2 same
    abbreviation, 1 prefix ("New York J" -> "New York Jets"), 0 no."""
    lab, name, short = norm(label), norm(team.name), norm(team.short)
    if not lab:
        return 0
    if lab in (name, short):
        return 3
    if abbr and abbr.lower() == team.abbr.lower():
        return 2
    if name.startswith(lab):
        return 1
    return 0


def assign(teams: list[Team], labels: list[tuple[str, str | None]]) -> list[int] | None:
    """Pair two venue labels with two teams. Returns team index per label,
    or None unless one pairing fits both labels and beats the other."""
    if len(teams) != 2 or len(labels) != 2:
        return None
    straight = [score(teams[0], *labels[0]), score(teams[1], *labels[1])]
    swapped = [score(teams[1], *labels[0]), score(teams[0], *labels[1])]
    s, w = min(straight), min(swapped)
    if s > 0 and (s > w or (s == w and sum(straight) > sum(swapped))):
        return [0, 1]
    if w > 0 and (w > s or (w == s and sum(swapped) > sum(straight))):
        return [1, 0]
    return None


def parse_ts(s: str | None) -> int | None:
    """'2026-10-11T13:30:00Z' or '2026-10-11 13:30:00+00' -> unix seconds."""
    if not s:
        return None
    s = s.strip().replace(" ", "T").replace("Z", "+00:00")
    if re.search(r"[+-]\d{2}$", s):
        s += ":00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return int((dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).timestamp())


def us_date(ts: int) -> str:
    return (datetime.fromtimestamp(ts, timezone.utc) - timedelta(hours=5)).strftime("%Y-%m-%d")


def _pick(game_scores: list[tuple[int, Game, list[int]]]):
    """The single best-fitting game, or None if two fit equally well."""
    if not game_scores:
        return None
    game_scores.sort(key=lambda x: -x[0])
    if len(game_scores) > 1 and game_scores[0][0] == game_scores[1][0]:
        return None
    return game_scores[0]


def attach_kalshi(gms: list[GameMarkets], events: list[dict]) -> None:
    for ev in events:
        ms = [m for m in ev.get("markets") or [] if m.get("status") in ("active", "open", None)]
        if len(ms) != 2:
            continue
        day = kalshi.ticker_date(ev["event_ticker"])
        labels = [(m.get("yes_sub_title") or "", m["ticker"].rsplit("-", 1)[-1]) for m in ms]
        cands = []
        for gm in gms:
            g = gm.game
            if day and abs((datetime.fromisoformat(day) -
                            datetime.fromisoformat(us_date(g.start))).days) > 1:
                continue
            a = assign(g.teams, labels)
            if a:
                cands.append((sum(score(g.teams[a[i]], *labels[i]) for i in range(2)), gm, a))
        best = _pick(cands)
        if not best:
            continue
        _, gm, a = best
        mult = kalshi.f((ev.get("series_fee") or {}).get("multiplier")) or 1.0
        for i, m in enumerate(ms):
            q = kalshi.quote_from_market(m)
            gm.legs[(gm.game.teams[a[i]].abbr, "kalshi")] = Leg(
                "kalshi", m["ticker"], ev["event_ticker"], m.get("yes_sub_title") or m["ticker"],
                kalshi.event_url(ev["event_ticker"]), q.bid, q.ask, q.bid_size, q.ask_size,
                0.01, 1.0, {"multiplier": mult})


def _poly_labels(ev: dict, m: dict) -> list[tuple[str, str | None, str]]:
    """[(label, abbreviation, token)] per outcome, using the event's team
    list to turn "Eagles" into "Philadelphia Eagles" when possible."""
    teams = ev.get("teams") or []
    out = []
    for name, tok in polymarket.outcomes(m):
        t = next((t for t in teams if norm(t.get("alias") or "") == norm(name)
                  or norm(t.get("name") or "") == norm(name)), None)
        out.append(((t or {}).get("name") or name, (t or {}).get("abbreviation"), tok))
    return out


def attach_polymarket(gms: list[GameMarkets], events: list[dict]) -> None:
    for ev in events:
        m = polymarket.moneyline(ev)
        if not m or not m.get("acceptingOrders", True):
            continue
        outs = _poly_labels(ev, m)
        if len(outs) != 2:
            continue
        st = parse_ts(ev.get("startTime") or m.get("gameStartTime"))
        cands = []
        for gm in gms:
            if st is not None and abs(st - gm.game.start) > 6 * 3600:
                continue
            a = assign(gm.game.teams, [(lab, ab) for lab, ab, _ in outs])
            if a:
                cands.append((sum(score(gm.game.teams[a[i]], outs[i][0], outs[i][1])
                                  for i in range(2)), gm, a))
        best = _pick(cands)
        if not best:
            continue
        _, gm, a = best
        for i, (lab, _, tok) in enumerate(outs):
            # gamma's bestBid/bestAsk describe outcome 0; mirror for outcome 1
            bid, ask = m.get("bestBid"), m.get("bestAsk")
            if i == 1 and bid is not None and ask is not None:
                bid, ask = round(1 - float(ask), 4), round(1 - float(bid), 4)
            gm.legs[(gm.game.teams[a[i]].abbr, "polymarket")] = Leg(
                "polymarket", tok, m["conditionId"], lab, polymarket.event_url(ev["slug"]),
                float(bid) if bid else None, float(ask) if ask else None, 0.0, 0.0,
                float(m.get("orderPriceMinTickSize") or 0.01), float(m.get("orderMinSize") or 5),
                m.get("feeSchedule") or {})


def load(leagues: list[str], venues: list[str], ahead_h: float = 18,
         now: float | None = None) -> list[GameMarkets]:
    """Today's games in `leagues` with each venue's winner markets attached.
    Quotes are snapshots from listing endpoints; refresh before trading."""
    out = []
    now = now or time.time()
    for lg in leagues:
        gms = [GameMarkets(g) for g in espn.games_around(lg, now, ahead_h) if len(g.teams) == 2]
        if not gms:
            continue
        if "kalshi" in venues and lg in kalshi.SERIES:
            series = kalshi.SERIES[lg]
            evs = kalshi.events(series, status="open")
            mult = kalshi.f(kalshi.series_info(series).get("fee_multiplier")) or 1.0
            for ev in evs:
                ev["series_fee"] = {"multiplier": mult}
            attach_kalshi(gms, evs)
        if "polymarket" in venues and lg in polymarket.SERIES:
            attach_polymarket(gms, polymarket.game_events(lg, now - 9 * 3600,
                                                          now + (ahead_h + 1) * 3600))
        out.extend(gms)
    return out


def refresh(leg: Leg) -> Leg:
    """Live top of book for one leg."""
    if leg.venue == "kalshi":
        b = kalshi.orderbook(leg.market_id)
        leg.bid, leg.bid_size, leg.ask, leg.ask_size = b.bid, b.bid_size, b.ask, b.ask_size
    else:
        b = polymarket.orderbook(leg.market_id)
        leg.bid, leg.bid_size, leg.ask, leg.ask_size = b.bid, b.bid_size, b.ask, b.ask_size
        leg.tick, leg.min_size = b.tick, b.min_size
    return leg
