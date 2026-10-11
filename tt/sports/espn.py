"""ESPN's public scoreboard: which games are on, when they start, the period
and clock while they play, and who won. No key needed."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .http import get_json

BASE = "https://site.api.espn.com/apis/site/v2/sports"
PATHS = {"nfl": ("football/nfl", {}),
         "ncaaf": ("football/college-football", {"groups": 80, "limit": 300}),
         "nba": ("basketball/nba", {}), "wnba": ("basketball/wnba", {}),
         "ncaab": ("basketball/mens-college-basketball", {"groups": 50, "limit": 400}),
         "mlb": ("baseball/mlb", {}), "nhl": ("hockey/nhl", {})}

# Bids stay up through this period and are cancelled once it ends (the
# video's rule: "cancel all bids after the first half").
# Quarters: 2 = halftime. Halves (college basketball): 1. Hockey: after the
# 1st period. Baseball: after the 5th inning.
HALF_PERIOD = {"nfl": 2, "ncaaf": 2, "nba": 2, "wnba": 2, "ncaab": 1, "nhl": 1, "mlb": 5}
# Typical minutes from start until that point. Only a backstop: bids also
# carry an exchange-side expiry this long (+15 min) after the start, so they
# lapse even if the phone running the bot dies.
HALF_MINUTES = {"nfl": 100, "ncaaf": 110, "nba": 75, "wnba": 65, "ncaab": 60, "nhl": 40,
                "mlb": 90}
BREAK_STATUSES = {"STATUS_HALFTIME", "STATUS_END_PERIOD"}
DEAD_STATUSES = {"STATUS_POSTPONED", "STATUS_CANCELED", "STATUS_SUSPENDED",
                 "STATUS_FORFEIT", "STATUS_DELAYED"}


@dataclass
class Team:
    name: str   # "Philadelphia Eagles"
    abbr: str   # "PHI"
    short: str  # "Eagles"
    home: bool
    winner: bool = False


@dataclass
class Game:
    league: str
    id: str
    start: int            # unix seconds
    state: str            # "pre", "in" or "post"
    status: str           # e.g. STATUS_SCHEDULED, STATUS_HALFTIME, STATUS_FINAL
    period: int
    clock: str
    teams: list[Team] = field(default_factory=list)  # away first, then home

    @property
    def title(self) -> str:
        return f"{self.teams[0].short} @ {self.teams[1].short}" if len(self.teams) == 2 else self.id

    def phase(self) -> str:
        if self.state == "pre":
            return "pre-game"
        if self.state == "post":
            return "final"
        return f"P{self.period} {self.clock}".strip() + (" (break)" if self.status in BREAK_STATUSES else "")

    def winner(self) -> str | None:
        """Winning team's abbreviation once final; 'TIE' for a tie."""
        if self.status != "STATUS_FINAL":
            return None
        w = [t.abbr for t in self.teams if t.winner]
        return w[0] if w else "TIE"


def bids_allowed(g: Game, half_period: int | None = None) -> bool:
    """True while resting bids may stay up: before the game, and in-game
    until `half_period` has ended. 0 means pre-game only."""
    if g.status in DEAD_STATUSES or g.state == "post":
        return False
    if g.state == "pre":
        return True
    n = HALF_PERIOD.get(g.league, 2) if half_period is None else half_period
    if g.period > n or n == 0:
        return False
    return not (g.period == n and g.status in BREAK_STATUSES)


def parse_event(league: str, e: dict) -> Game:
    comp = e["competitions"][0]
    st = comp.get("status") or e.get("status") or {}
    teams = []
    for c in sorted(comp.get("competitors", []), key=lambda c: c.get("homeAway") != "away"):
        t = c.get("team", {})
        teams.append(Team(t.get("displayName", ""), t.get("abbreviation", ""),
                          t.get("shortDisplayName") or t.get("name", ""),
                          c.get("homeAway") == "home", bool(c.get("winner"))))
    start = int(datetime.fromisoformat(e["date"].replace("Z", "+00:00")).timestamp())
    typ = st.get("type", {})
    return Game(league, str(e["id"]), start, typ.get("state", "pre"), typ.get("name", ""),
                int(st.get("period") or 0), str(st.get("displayClock") or ""), teams)


def scoreboard(league: str, day: str) -> list[Game]:
    """Games on `day` (YYYYMMDD, US date)."""
    path, extra = PATHS[league]
    d = get_json(f"{BASE}/{path}/scoreboard", {"dates": day, **extra})
    return [parse_event(league, e) for e in d.get("events", [])]


def games_around(league: str, now: float | None = None, ahead_h: float = 18) -> list[Game]:
    """Games that started up to 8h ago or start within `ahead_h` hours."""
    now = now or time.time()
    days, h = set(), -8.0
    while True:
        # US dates: Eastern time is UTC-4/-5; -5 covers both edges
        dt = datetime.fromtimestamp(now + h * 3600, timezone.utc) - timedelta(hours=5)
        days.add(dt.strftime("%Y%m%d"))
        if h >= ahead_h:
            break
        h = min(h + 12, ahead_h)
    seen, out = set(), []
    for day in sorted(days):
        for g in scoreboard(league, day):
            if g.id not in seen and now - 8 * 3600 <= g.start <= now + ahead_h * 3600:
                seen.add(g.id)
                out.append(g)
    return sorted(out, key=lambda g: g.start)
