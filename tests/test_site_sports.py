"""The website's sports page runs a JavaScript port of tt/sports
(site/js/sports-core.js). This checks it gives the same answers: fees, team
matching, the halftime rule and stink-bid levels; and that the Kalshi proxy
(site/api/kalshi.js) only passes allowed read-only requests. Skipped when
Node.js isn't installed (e.g. on Termux)."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest

from tt.sports import espn, games as G
from tt.sports.cli import locked_edge
from tt.sports.fees import kalshi_fee, poly_fee
from tt.sports.stink import floor_tick

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NODE = shutil.which("node")
START = 1_791_725_400

SCHEDULE = {"rate": 0.03, "exponent": 1}
FEES = [[n, p, maker, mult] for n in (1, 5, 9, 100) for p in (0.03, 0.21, 0.5, 0.77, 0.99)
        for maker in (False, True) for mult in (1, 0.5)]


def team(name, abbr, short):
    return {"name": name, "abbr": abbr, "short": short}


JETS, GIANTS, BROWNS = (team("New York Jets", "NYJ", "Jets"), team("New York Giants", "NYG", "Giants"),
                        team("Cleveland Browns", "CLE", "Browns"))
TAMU, TEXAS = team("Texas A&M Aggies", "TA&M", "Texas A&M"), team("Texas Longhorns", "TEX", "Texas")
SCORES = [[JETS, "New York Jets", None], [JETS, "Jets", None], [JETS, "x", "nyj"],
          [JETS, "New York J", None], [GIANTS, "New York J", None], [TAMU, "Texas", None],
          [TEXAS, "Texas", None], [TAMU, "Texas A&M", "TAMU"], [BROWNS, "", None]]
ASSIGNS = [[[BROWNS, JETS], [["Cleveland", "CLE"], ["New York J", "NYJ"]]],
           [[BROWNS, JETS], [["New York J", "NYJ"], ["Cleveland", "CLE"]]],
           [[JETS, GIANTS], [["New York", None], ["New York", None]]],
           [[TAMU, TEXAS], [["Texas", "TEX"], ["Texas A&M", "TAM"]]],
           [[BROWNS, JETS], [["Cleveland", None], ["Boston", None]]]]


def game(state, status, period, league="nba"):
    return {"league": league, "state": state, "status": status, "period": period}


GAMES = [[game("pre", "STATUS_SCHEDULED", 0), None], [game("in", "STATUS_IN_PROGRESS", 2), None],
         [game("in", "STATUS_HALFTIME", 2), None], [game("in", "STATUS_IN_PROGRESS", 3), None],
         [game("post", "STATUS_FINAL", 4), None], [game("pre", "STATUS_POSTPONED", 0), None],
         [game("in", "STATUS_END_PERIOD", 1, "ncaab"), None],
         [game("in", "STATUS_IN_PROGRESS", 5, "mlb"), None], [game("in", "STATUS_IN_PROGRESS", 1), 0],
         [game("in", "STATUS_IN_PROGRESS", 3), 3]]
TICKS = [[0.2299, 0.01], [0.21, 0.01], [0.4567, 0.001], [0.3 * 0.7, 0.01], [0.535 * 0.7, 0.01]]

PHI_JAX = {"league": "nfl", "id": "g1", "start": START, "state": "pre", "status": "", "period": 0,
           "clock": "", "teams": [team("Philadelphia Eagles", "PHI", "Eagles"),
                                  team("Jacksonville Jaguars", "JAX", "Jaguars")]}
KALSHI_EVENTS = [{"event_ticker": "KXNFLGAME-26OCT11PHIJAC", "markets": [
    {"ticker": "KXNFLGAME-26OCT11PHIJAC-JAC", "yes_sub_title": "Jacksonville", "status": "active",
     "yes_bid_dollars": "0.7600", "yes_ask_dollars": "0.7700"},
    {"ticker": "KXNFLGAME-26OCT11PHIJAC-PHI", "yes_sub_title": "Philadelphia", "status": "active",
     "yes_bid_dollars": "0.2300", "yes_ask_dollars": "0.2400"}]}]
POLY_EVENTS = [{"slug": "nfl-phi-jax-2026-10-11", "startTime": "2026-10-11T13:30:00Z",
                "teams": [{"name": "Philadelphia Eagles", "abbreviation": "phi", "alias": "Eagles"},
                          {"name": "Jacksonville Jaguars", "abbreviation": "jax", "alias": "Jaguars"}],
                "markets": [{"sportsMarketType": "moneyline", "conditionId": "0xc",
                             "outcomes": '["Eagles", "Jaguars"]', "clobTokenIds": '["t1", "t2"]',
                             "bestBid": 0.22, "bestAsk": 0.23, "acceptingOrders": True,
                             "feeSchedule": SCHEDULE}]}]


def espn_team(t):
    return espn.Team(t["name"], t["abbr"], t["short"], False)


@unittest.skipUnless(NODE, "node not installed")
class SiteSportsParity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cases = {"fees": FEES, "schedule": SCHEDULE, "scores": SCORES, "assigns": ASSIGNS,
                 "games": GAMES, "ticks": TICKS,
                 "match": {"games": [PHI_JAX], "kalshi": KALSHI_EVENTS, "polymarket": POLY_EVENTS}}
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(cases, f)
        try:
            out = subprocess.run([NODE, os.path.join(ROOT, "tests/js/sports_parity.mjs"), f.name],
                                 capture_output=True, text=True, check=True).stdout
        finally:
            os.unlink(f.name)
        cls.js = json.loads(out)

    def test_fees(self):
        for (n, p, maker, mult), k, pm in zip(FEES, self.js["kalshiFee"], self.js["polyFee"]):
            self.assertAlmostEqual(kalshi_fee(n, p, maker, mult), k, places=9, msg=(n, p, maker, mult))
            self.assertAlmostEqual(poly_fee(n, p, SCHEDULE, maker), pm, places=9, msg=(n, p, maker))

    def test_matching(self):
        for (t, label, abbr), js in zip(SCORES, self.js["score"]):
            self.assertEqual(G.score(espn_team(t), label, abbr), js, (t["name"], label, abbr))
        for (teams, labels), js in zip(ASSIGNS, self.js["assign"]):
            self.assertEqual(G.assign([espn_team(t) for t in teams], [tuple(x) for x in labels]), js)

    def test_halftime_rule(self):
        for (g, half), js in zip(GAMES, self.js["bids"]):
            pg = espn.Game(g["league"], "x", START, g["state"], g["status"], g["period"], "", [])
            self.assertEqual(espn.bids_allowed(pg, half), js, (g, half))

    def test_ticks(self):
        for (p, t), js in zip(TICKS, self.js["floorTick"]):
            self.assertAlmostEqual(floor_tick(p, t), js, places=9)

    def test_attach_and_levels(self):
        gm = G.GameMarkets(espn.Game("nfl", "g1", START, "pre", "", 0, "",
                                     [espn_team(t) for t in PHI_JAX["teams"]]))
        G.attach_kalshi([gm], [{**e, "series_fee": {"multiplier": 1}} for e in KALSHI_EVENTS])
        G.attach_polymarket([gm], POLY_EVENTS)
        py = {f"{a}|{v}": [l.market_id, l.bid, l.ask] for (a, v), l in gm.legs.items()}
        js = self.js["legs"][0]
        self.assertEqual(set(py), set(js))
        for k in py:
            self.assertEqual(py[k][0], js[k][0])
            self.assertAlmostEqual(py[k][1], js[k][1])
            self.assertAlmostEqual(py[k][2], js[k][2])
        for abbr, lvl in zip(("PHI", "JAX"), self.js["stink"][0]):
            self.assertAlmostEqual(floor_tick(gm.fair(abbr) * 0.7, 0.01), lvl)
        best = max((locked_edge(gm.leg(x, "kalshi"), gm.leg(y, "polymarket")), x, y)
                   for x, y in (("PHI", "JAX"), ("JAX", "PHI")))
        self.assertAlmostEqual(best[0], self.js["lock"][0][0], places=9)
        self.assertEqual(best[1:], tuple(self.js["lock"][0][1:]))

    def test_kalshi_proxy_allowlist(self):
        self.assertEqual(self.js["proxy"], [200, 400, 200, 400, 200])
        urls = self.js["proxyUrls"]
        self.assertEqual(len(urls), 3)
        self.assertTrue(urls[0].startswith("https://api.elections.kalshi.com/trade-api/v2/events?"))
        self.assertIn("series_ticker=KXNFLGAME", urls[0])
        self.assertNotIn("evil", urls[0])
        self.assertIn("markets/trades?ticker=KXNFLGAME-26OCT11PHIJAC-PHI", urls[1])


if __name__ == "__main__":
    unittest.main()
