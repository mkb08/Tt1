import base64
import importlib.util
import os
import tempfile
import time
import unittest
from unittest import mock

from tt.notify import Notifier
from tt.sports import espn, games as G, kalshi, polymarket, whales
from tt.sports.fees import kalshi_fee, poly_fee
from tt.sports.stink import StinkBot, floor_tick
from tt.sports.venues import PaperVenue, contracts_for

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
START = 1_791_725_400  # 2026-10-11 13:30 UTC


def team(name, abbr, short, home, winner=False):
    return espn.Team(name, abbr, short, home, winner)


def game(state="pre", status="STATUS_SCHEDULED", period=0, league="nfl", winners=(), start=START):
    return espn.Game(league, "g1", start, state, status, period, "", [
        team("Philadelphia Eagles", "PHI", "Eagles", False, "PHI" in winners),
        team("Jacksonville Jaguars", "JAX", "Jaguars", True, "JAX" in winners)])


def leg(venue, mid, bid, ask, ask_size=1000.0, **kw):
    defaults = {"kalshi": dict(min_size=1.0, fee={"multiplier": 1.0}),
                "polymarket": dict(min_size=5.0, fee={"rate": 0.03, "exponent": 1})}[venue]
    return G.Leg(venue, mid, f"grp-{venue}", mid, f"https://x/{mid}", bid, ask, 100.0, ask_size,
                 0.01, **{**defaults, **kw})


def markets(g, quotes):
    """quotes: {(abbr, venue): (bid, ask)}"""
    gm = G.GameMarkets(g)
    for (abbr, venue), (b, a) in quotes.items():
        gm.legs[(abbr, venue)] = leg(venue, f"{venue}-{abbr}", b, a)
    return gm


FAIR = {("PHI", "kalshi"): (0.29, 0.31), ("PHI", "polymarket"): (0.29, 0.31),
        ("JAX", "kalshi"): (0.69, 0.71), ("JAX", "polymarket"): (0.69, 0.71)}


class FeeTests(unittest.TestCase):
    def test_kalshi_rounds_up_to_the_cent(self):
        self.assertAlmostEqual(kalshi_fee(100, 0.5), 1.75)
        self.assertAlmostEqual(kalshi_fee(1, 0.5), 0.02)            # 0.0175 -> 0.02
        self.assertAlmostEqual(kalshi_fee(100, 0.5, maker=True), 0.44)
        self.assertAlmostEqual(kalshi_fee(100, 0.5, multiplier=0.5), 0.88)
        self.assertEqual(kalshi_fee(0, 0.5), 0.0)

    def test_polymarket_matches_published_example(self):
        # docs: sports, 100 shares at $0.30, rate 0.05 -> $1.05
        self.assertAlmostEqual(poly_fee(100, 0.30, {"rate": 0.05, "exponent": 1}), 1.05)
        self.assertEqual(poly_fee(100, 0.30, {"rate": 0.05}, maker=True), 0.0)
        self.assertEqual(poly_fee(100, 0.30, {}), 0.0)


class KalshiParsingTests(unittest.TestCase):
    def test_orderbook_turns_no_bids_into_yes_asks(self):
        ob = {"orderbook_fp": {"yes_dollars": [["0.2200", "500"], ["0.2300", "100"]],
                               "no_dollars": [["0.7500", "40"], ["0.7600", "900"]]}}
        with mock.patch.object(kalshi, "get_json", return_value=ob):
            b = kalshi.orderbook("T")
        self.assertEqual((b.bid, b.bid_size), (0.23, 100.0))
        self.assertEqual((b.ask, b.ask_size), (0.24, 900.0))

    def test_ticker_date_and_url(self):
        self.assertEqual(kalshi.ticker_date("KXNFLGAME-26OCT11PHIJAC"), "2026-10-11")
        self.assertIsNone(kalshi.ticker_date("KXNFLGAME-XYZ"))
        self.assertEqual(kalshi.event_url("KXNBAGAME-26OCT10SASPHX"),
                         "https://kalshi.com/markets/kxnbagame/nba-game/kxnbagame-26oct10sasphx")

    def test_trade_value_sides(self):
        buy = {"count_fp": "100", "yes_price_dollars": "0.30", "no_price_dollars": "0.70",
               "taker_side": "yes"}
        sell = {**buy, "taker_side": "no"}
        self.assertEqual(kalshi.trade_value(buy), ("BUY", 0.30, 30.0))
        self.assertEqual(kalshi.trade_value(sell), ("SELL", 0.70, 70.0))

    def test_signature_verifies(self):
        try:
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import padding, rsa
        except ImportError:
            self.skipTest("cryptography not installed")
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption())
        t = kalshi.KalshiTrader("key-id", pem, kalshi.DEMO)
        h = t._headers("GET", "/portfolio/balance?x=1")
        msg = f"{h['KALSHI-ACCESS-TIMESTAMP']}GET/trade-api/v2/portfolio/balance".encode()
        key.public_key().verify(base64.b64decode(h["KALSHI-ACCESS-SIGNATURE"]), msg,
                                padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                                            salt_length=padding.PSS.DIGEST_LENGTH),
                                hashes.SHA256())  # raises if wrong
        self.assertEqual(h["KALSHI-ACCESS-KEY"], "key-id")

    def test_place_bid_body(self):
        t = kalshi.KalshiTrader.__new__(kalshi.KalshiTrader)
        t.base, t.root = kalshi.PROD, "/trade-api/v2"
        t._headers = lambda m, p: {}
        with mock.patch.object(kalshi, "send_json", return_value={"order_id": "o1"}) as s:
            t.place_bid("KXNFLGAME-26OCT11PHIJAC-PHI", 0.16, 12, "cid")
        method, url, body, _ = s.call_args[0]
        self.assertEqual((method, url), ("POST", kalshi.PROD + "/portfolio/events/orders"))
        self.assertEqual((body["side"], body["price"], body["count"], body["post_only"]),
                         ("bid", "0.1600", "12.00", True))


class MatchingTests(unittest.TestCase):
    def setUp(self):
        self.jets = team("New York Jets", "NYJ", "Jets", True)
        self.giants = team("New York Giants", "NYG", "Giants", True)
        self.browns = team("Cleveland Browns", "CLE", "Browns", False)

    def test_score(self):
        self.assertEqual(G.score(self.jets, "New York Jets"), 3)
        self.assertEqual(G.score(self.jets, "Jets"), 3)
        self.assertEqual(G.score(self.jets, "anything", "nyj"), 2)
        self.assertEqual(G.score(self.jets, "New York J"), 1)
        self.assertEqual(G.score(self.giants, "New York J"), 0)

    def test_assign_handles_order_and_ambiguity(self):
        teams = [self.browns, self.jets]
        self.assertEqual(G.assign(teams, [("Cleveland", "CLE"), ("New York J", "NYJ")]), [0, 1])
        self.assertEqual(G.assign(teams, [("New York J", "NYJ"), ("Cleveland", "CLE")]), [1, 0])
        self.assertIsNone(G.assign(teams, [("Cleveland", None), ("Boston", None)]))
        self.assertIsNone(G.assign([self.jets, self.giants], [("New York", None), ("New York", None)]))

    def test_parse_ts(self):
        self.assertEqual(G.parse_ts("2026-10-11T13:30:00Z"), START)
        self.assertEqual(G.parse_ts("2026-10-11 13:30:00+00"), START)
        self.assertIsNone(G.parse_ts("garbage"))

    def test_attach_kalshi_maps_teams_by_name(self):
        gm = G.GameMarkets(game())
        ev = {"event_ticker": "KXNFLGAME-26OCT11PHIJAC", "series_fee": {"multiplier": 1},
              "markets": [
                  {"ticker": "KXNFLGAME-26OCT11PHIJAC-JAC", "yes_sub_title": "Jacksonville",
                   "status": "active", "yes_bid_dollars": "0.76", "yes_ask_dollars": "0.77"},
                  {"ticker": "KXNFLGAME-26OCT11PHIJAC-PHI", "yes_sub_title": "Philadelphia",
                   "status": "active", "yes_bid_dollars": "0.23", "yes_ask_dollars": "0.24"}]}
        G.attach_kalshi([gm], [ev])
        self.assertEqual(gm.leg("JAX", "kalshi").market_id, "KXNFLGAME-26OCT11PHIJAC-JAC")
        self.assertEqual((gm.leg("PHI", "kalshi").bid, gm.leg("PHI", "kalshi").ask), (0.23, 0.24))

    def test_attach_kalshi_rejects_other_dates(self):
        gm = G.GameMarkets(game())
        ev = {"event_ticker": "KXNFLGAME-26NOV22PHIJAC", "markets": [
            {"ticker": "A-JAC", "yes_sub_title": "Jacksonville", "status": "active"},
            {"ticker": "A-PHI", "yes_sub_title": "Philadelphia", "status": "active"}]}
        G.attach_kalshi([gm], [ev])
        self.assertEqual(gm.legs, {})

    def test_attach_polymarket_mirrors_quotes_for_second_outcome(self):
        gm = G.GameMarkets(game())
        ev = {"slug": "nfl-phi-jax-2026-10-11", "startTime": "2026-10-11T13:30:00Z",
              "teams": [{"name": "Philadelphia Eagles", "abbreviation": "phi", "alias": "Eagles"},
                        {"name": "Jacksonville Jaguars", "abbreviation": "jax", "alias": "Jaguars"}],
              "markets": [{"sportsMarketType": "moneyline", "conditionId": "0xc",
                           "outcomes": '["Eagles", "Jaguars"]', "clobTokenIds": '["t1", "t2"]',
                           "bestBid": 0.23, "bestAsk": 0.24, "acceptingOrders": True,
                           "feeSchedule": {"rate": 0.03, "exponent": 1}}]}
        G.attach_polymarket([gm], [ev])
        phi, jax = gm.leg("PHI", "polymarket"), gm.leg("JAX", "polymarket")
        self.assertEqual((phi.market_id, phi.bid, phi.ask), ("t1", 0.23, 0.24))
        self.assertEqual((jax.market_id, jax.bid, jax.ask), ("t2", 0.76, 0.77))
        self.assertEqual(phi.url, "https://polymarket.com/event/nfl-phi-jax-2026-10-11")

    def test_fair_averages_venue_mids(self):
        gm = markets(game(), {("PHI", "kalshi"): (0.20, 0.22), ("PHI", "polymarket"): (0.24, 0.26)})
        self.assertAlmostEqual(gm.fair("PHI"), 0.23)
        self.assertEqual(gm.opponent("PHI"), "JAX")


class ClockTests(unittest.TestCase):
    def test_bids_allowed_until_halftime(self):
        self.assertTrue(espn.bids_allowed(game()))
        self.assertTrue(espn.bids_allowed(game("in", "STATUS_IN_PROGRESS", 2)))
        self.assertFalse(espn.bids_allowed(game("in", "STATUS_HALFTIME", 2)))
        self.assertFalse(espn.bids_allowed(game("in", "STATUS_IN_PROGRESS", 3)))
        self.assertFalse(espn.bids_allowed(game("post", "STATUS_FINAL", 4)))
        self.assertFalse(espn.bids_allowed(game(status="STATUS_POSTPONED")))

    def test_league_halves_and_pregame_only(self):
        self.assertFalse(espn.bids_allowed(game("in", "STATUS_END_PERIOD", 1, league="ncaab")))
        self.assertTrue(espn.bids_allowed(game("in", "STATUS_IN_PROGRESS", 5, league="mlb")))
        self.assertFalse(espn.bids_allowed(game("in", "STATUS_IN_PROGRESS", 1), half_period=0))

    def test_parse_event_and_winner(self):
        e = {"id": "1", "date": "2026-10-11T13:30Z", "competitions": [{
            "status": {"period": 4, "displayClock": "0:00",
                       "type": {"state": "post", "name": "STATUS_FINAL"}},
            "competitors": [
                {"homeAway": "home", "winner": True, "team": {
                    "displayName": "Jacksonville Jaguars", "abbreviation": "JAX",
                    "shortDisplayName": "Jaguars"}},
                {"homeAway": "away", "winner": False, "team": {
                    "displayName": "Philadelphia Eagles", "abbreviation": "PHI",
                    "shortDisplayName": "Eagles"}}]}]}
        g = espn.parse_event("nfl", e)
        self.assertEqual([t.abbr for t in g.teams], ["PHI", "JAX"])  # away first
        self.assertEqual((g.start, g.winner(), g.title), (START, "JAX", "Eagles @ Jaguars"))


class WhaleTests(unittest.TestCase):
    def test_fmt_fits_a_phone(self):
        w = whales.Whale(START, "kalshi", "nfl", "Eagles @ Jaguars", "PHI", "SELL", 0.2, 10000,
                         2000.0, "https://k/x", "P 0.29/0.31", -0.1)
        wide, narrow = whales.fmt(w, 200), whales.fmt(w, 60)
        self.assertEqual(wide.count("\n"), 0)
        self.assertIn("-10c", wide)
        self.assertEqual(narrow.count("\n"), 2)
        self.assertTrue(all(len(line) <= 60 for line in narrow.splitlines()))

    def test_group_merges_one_sweep(self):
        rows = [("m", 10, "SELL", 0.30, 100), ("m", 10, "SELL", 0.20, 100),
                ("m", 10, "BUY", 0.31, 5), ("m", 11, "SELL", 0.30, 1)]
        g = {(r[0], r[1], r[2]): r for r in whales._group(rows)}
        self.assertAlmostEqual(g[("m", 10, "SELL")][3], 0.25)   # volume-weighted price
        self.assertEqual(g[("m", 10, "SELL")][4], 200)
        self.assertEqual(len(g), 3)

    def test_collect_flags_cheap_dump_against_other_venue(self):
        gm = markets(game("in", "STATUS_IN_PROGRESS", 1), FAIR)
        k_trades = [{"created_time": "2026-10-11T13:40:00.5Z", "taker_side": "no",
                     "yes_price_dollars": "0.20", "no_price_dollars": "0.80", "count_fp": "10000"}]
        with mock.patch.object(kalshi, "trades", side_effect=lambda t, **k:
                               k_trades if t == "kalshi-PHI" else []), \
                mock.patch.object(polymarket, "trades", return_value=[]):
            out = whales.collect([gm], 0, 1000, quotes_ts=START + 600)
        self.assertEqual(len(out), 1)
        w = out[0]
        self.assertEqual((w.venue, w.team, w.side, w.usd), ("kalshi", "PHI", "SELL", 2000.0))
        self.assertAlmostEqual(w.gap, -0.10)
        self.assertIn("P 0.29/0.31", w.other)


class FakeVenue:
    def __init__(self, name):
        self.name, self.orders, self.calls, self.n = name, {}, [], 0
        self.ask_fill = None  # (filled, price) for buy_now

    def place_bid(self, leg, price, size, expires=None):
        self.expires = expires
        self.n += 1
        oid = f"{self.name}-{self.n}"
        self.orders[oid] = {"price": price, "size": size, "filled": 0.0, "open": True}
        self.calls.append(("bid", leg.market_id, price, size))
        return oid

    def poll(self, oid, leg):
        o = self.orders[oid]
        return o["filled"], o["open"]

    def cancel(self, oid, leg):
        self.orders[oid]["open"] = False
        self.calls.append(("cancel", leg.market_id))

    def buy_now(self, leg, max_price, size):
        self.calls.append(("buy", leg.market_id, max_price, size))
        return size, max_price, 0.0

    def fill(self, market_id, qty=None):
        for o, c in zip(self.orders.values(), [c for c in self.calls if c[0] == "bid"]):
            if c[1] == market_id and o["open"]:
                o["filled"] = qty if qty is not None else o["size"]
                o["open"] = qty is not None and qty < o["size"]


class StinkBotTests(unittest.TestCase):
    def setUp(self):
        # bids carry an exchange-side expiry after halftime, so start soon
        self.start = int(time.time()) + 7200
        self.tmp = tempfile.mkdtemp()
        self.v = {"kalshi": FakeVenue("kalshi"), "polymarket": FakeVenue("polymarket")}
        self.cfg = {"leagues": ["nfl"], "state_file": os.path.join(self.tmp, "s.json"),
                    "max_total_usd": 100, "hedge": {"enabled": False}}
        refresh = mock.patch.object(G, "refresh", side_effect=lambda leg: leg)
        refresh.start()
        self.addCleanup(refresh.stop)

    def game(self, *a, **k):
        return game(*a, **{"start": self.start, **k})

    def bot(self, **cfg):
        return StinkBot({**self.cfg, **cfg}, self.v,
                        Notifier(log_path=os.path.join(self.tmp, "log"), title="t"))

    def test_bids_30pct_below_on_both_teams_and_venues(self):
        b = self.bot()
        b.step([markets(self.game(), FAIR)])
        bids = sorted(c for v in self.v.values() for c in v.calls if c[0] == "bid")
        self.assertEqual([(c[1], c[2]) for c in bids], [
            ("kalshi-JAX", 0.49), ("kalshi-PHI", 0.21),
            ("polymarket-JAX", 0.49), ("polymarket-PHI", 0.21)])
        self.assertEqual(bids[1][3], 9.0)   # $2 / 0.21 -> 9 contracts
        self.assertEqual(bids[2][3], 5.0)   # Polymarket minimum 5 shares
        b.step([markets(self.game(), FAIR)])     # no duplicates on the next pass
        self.assertEqual(sum(c[0] == "bid" for v in self.v.values() for c in v.calls), 4)

    def test_cancels_at_halftime_and_keeps_fills(self):
        b = self.bot()
        b.step([markets(self.game(), FAIR)])
        self.v["kalshi"].fill("kalshi-PHI", 3)
        b.step([markets(self.game("in", "STATUS_HALFTIME", 2), FAIR)])
        cancels = sorted(c[1] for v in self.v.values() for c in v.calls if c[0] == "cancel")
        self.assertEqual(cancels, ["kalshi-JAX", "kalshi-PHI", "polymarket-JAX", "polymarket-PHI"])
        slots = b.state["slots"]
        self.assertEqual(slots["kalshi:kalshi-PHI"]["status"], "filled")
        self.assertEqual(slots["kalshi:kalshi-PHI"]["filled"], 3)
        self.assertEqual(slots["kalshi:kalshi-JAX"]["status"], "cancelled")

    def test_reprices_before_the_game_only(self):
        b = self.bot()
        b.step([markets(self.game(), FAIR)])
        moved = {**FAIR, ("PHI", "kalshi"): (0.39, 0.41), ("PHI", "polymarket"): (0.39, 0.41)}
        b.step([markets(self.game(), moved)])
        self.assertIn(("bid", "kalshi-PHI", 0.28, 7.0), self.v["kalshi"].calls)
        n = len(self.v["kalshi"].calls)
        b.step([markets(self.game("in", "STATUS_IN_PROGRESS", 1), FAIR)])
        self.assertEqual(len(self.v["kalshi"].calls), n)  # in-game: leave it

    def test_budget_cap(self):
        b = self.bot(max_total_usd=4.0)
        b.step([markets(self.game(), FAIR)])
        self.assertEqual(sum(c[0] == "bid" for v in self.v.values() for c in v.calls), 2)

    def test_skips_longshots(self):
        lop = {("PHI", "kalshi"): (0.04, 0.06), ("JAX", "kalshi"): (0.94, 0.96)}
        b = self.bot(venues=["kalshi"])
        b.step([markets(self.game(), lop)])
        self.assertEqual(self.v["kalshi"].calls, [])

    def test_hedge_only_when_it_locks_a_profit(self):
        b = self.bot(hedge={"enabled": True, "min_locked_edge": 0.02})
        b.step([markets(self.game(), FAIR)])
        self.v["kalshi"].fill("kalshi-PHI")
        # whale dumped PHI on Kalshi only: Polymarket still has JAX at 0.71
        b.step([markets(self.game("in", "STATUS_IN_PROGRESS", 1), FAIR)])
        buys = [c for c in self.v["polymarket"].calls if c[0] == "buy"]
        self.assertEqual(buys, [("buy", "polymarket-JAX", 0.71, 9.0)])
        h = b.state["slots"]["kalshi:kalshi-PHI"]["hedge"]
        self.assertEqual((h["team"], h["size"]), ("JAX", 9.0))

    def test_hedge_only_right_after_the_fill(self):
        b = self.bot(hedge={"enabled": True, "min_locked_edge": 0.02, "window_s": 120})
        b.step([markets(self.game(), FAIR)])
        self.v["kalshi"].fill("kalshi-PHI")
        news = {**FAIR, ("JAX", "polymarket"): (0.78, 0.80)}
        b.step([markets(self.game("in", "STATUS_IN_PROGRESS", 1), news)])
        b.state["slots"]["kalshi:kalshi-PHI"]["fill_ts"] -= 600  # ten minutes later
        b.step([markets(self.game("in", "STATUS_IN_PROGRESS", 1), FAIR)])
        self.assertFalse([c for c in self.v["polymarket"].calls if c[0] == "buy"])

    def test_no_hedge_when_the_move_is_news(self):
        b = self.bot(hedge={"enabled": True, "min_locked_edge": 0.02})
        b.step([markets(self.game(), FAIR)])
        self.v["kalshi"].fill("kalshi-PHI")
        news = {**FAIR, ("JAX", "polymarket"): (0.78, 0.80)}  # both venues moved
        b.step([markets(self.game("in", "STATUS_IN_PROGRESS", 1), news)])
        self.assertFalse([c for c in self.v["polymarket"].calls if c[0] == "buy"])

    def test_settles_win_loss_and_hedge(self):
        b = self.bot(hedge={"enabled": True, "min_locked_edge": 0.02})
        b.step([markets(self.game(), FAIR)])
        self.v["kalshi"].fill("kalshi-PHI")
        b.step([markets(self.game("in", "STATUS_IN_PROGRESS", 1), FAIR)])
        b.step([markets(self.game("post", "STATUS_FINAL", 4, winners=("JAX",)), FAIR)])
        rec = [r for r in b.state["settled"] if r["team"] == "PHI"][0]
        fee = kalshi_fee(9, 0.21, maker=True)
        # PHI lost: -9 x 0.21 - fee; JAX hedge won: 9 x (1 - 0.71)
        self.assertAlmostEqual(rec["pnl"], -9 * 0.21 - fee + 9 * (1 - 0.71), places=6)
        self.assertGreater(rec["pnl"], 0)
        self.assertEqual(b.state["slots"], {})
        self.assertIn("settled: 1 fills", b.summary())

    def test_state_survives_restart(self):
        b = self.bot()
        b.step([markets(self.game(), FAIR)])
        b2 = self.bot()
        self.assertEqual(len(b2.state["slots"]), 4)
        b2.step([markets(self.game(), FAIR)])
        self.assertEqual(sum(c[0] == "bid" for v in self.v.values() for c in v.calls), 4)

    def test_bids_expire_on_the_exchange_after_halftime(self):
        self.bot().step([markets(self.game(), FAIR)])
        self.assertEqual(self.v["kalshi"].expires, self.start + (100 + 15) * 60)
        self.bot(half_period=3, state_file=os.path.join(self.tmp, "q3.json")).step(
            [markets(self.game(), FAIR)])
        self.assertEqual(self.v["kalshi"].expires, self.start + (150 + 15) * 60)
        late = self.game("in", "STATUS_IN_PROGRESS", 2, start=int(time.time()) - 113 * 60)
        b = self.bot(state_file=os.path.join(self.tmp, "late.json"))
        n = self.v["kalshi"].n
        b.step([markets(late, FAIR)])
        self.assertEqual(self.v["kalshi"].n, n)  # too close to the expiry to bother

    def test_floor_tick(self):
        self.assertEqual(floor_tick(0.2299, 0.01), 0.22)
        self.assertEqual(floor_tick(0.21, 0.01), 0.21)
        self.assertEqual(floor_tick(0.4567, 0.001), 0.456)


class PaperVenueTests(unittest.TestCase):
    def test_fills_only_when_traded_through(self):
        book = {}
        pv = PaperVenue("kalshi", book)
        lg = leg("kalshi", "T", 0.29, 0.31)
        oid = pv.place_bid(lg, 0.21, 9)
        at_bid = [{"yes_price_dollars": "0.21"}]
        below = [{"yes_price_dollars": "0.20"}]
        with mock.patch.object(kalshi, "trades", return_value=at_bid):
            self.assertEqual(pv.poll(oid, lg), (0.0, True))     # touching isn't a fill
        with mock.patch.object(kalshi, "trades", return_value=below):
            self.assertEqual(pv.poll(oid, lg), (9, False))

    def test_polymarket_counts_the_other_token(self):
        pv = PaperVenue("polymarket", {})
        lg = leg("polymarket", "tokA", 0.29, 0.31)
        oid = pv.place_bid(lg, 0.21, 10)
        # someone bought team B at 0.80, i.e. sold team A at 0.20
        t = [{"asset": "tokB", "price": "0.80", "timestamp": str(2 ** 40)}]
        with mock.patch.object(polymarket, "trades", return_value=t):
            self.assertEqual(pv.poll(oid, lg), (10, False))

    def test_hedge_fill_needs_size_at_the_ask(self):
        pv = PaperVenue("polymarket", {})
        self.assertEqual(pv.buy_now(leg("polymarket", "t", 0.7, 0.71, ask_size=3), 0.71, 9)[0], 0)
        n, px, fee = pv.buy_now(leg("polymarket", "t", 0.7, 0.71), 0.71, 9)
        self.assertEqual((n, px), (9, 0.71))
        self.assertAlmostEqual(fee, poly_fee(9, 0.71, {"rate": 0.03, "exponent": 1}))

    def test_contracts_for(self):
        self.assertEqual(contracts_for(leg("kalshi", "t", 0, 0), 0.21, 2), 9)
        self.assertEqual(contracts_for(leg("polymarket", "t", 0, 0), 0.49, 2), 5)


class LiveAdapterTests(unittest.TestCase):
    """Response parsing of the live venues, against canned API answers."""

    def test_kalshi_venue(self):
        from tt.sports.venues import KalshiVenue
        t = mock.Mock()
        t.place_bid.return_value = {"order_id": "o1", "fill_count": "0.00"}
        t.order.return_value = {"fill_count_fp": "3.00", "status": "resting"}
        t.buy_now.return_value = {"fill_count": "9.00", "average_fill_price": "0.7100",
                                  "average_fee_paid": "0.0150"}
        v = KalshiVenue(t)
        lg = leg("kalshi", "T", 0.29, 0.31)
        self.assertEqual(v.place_bid(lg, 0.21, 9, expires=123), "o1")
        self.assertEqual(t.place_bid.call_args.kwargs["expires"], 123)
        self.assertEqual(v.poll("o1", lg), (3.0, True))
        n, px, fee = v.buy_now(lg, 0.71, 9)
        self.assertEqual((n, px), (9.0, 0.71))
        self.assertAlmostEqual(fee, 0.135)

    def test_polymarket_venue(self):
        from tt.sports.venues import PolymarketVenue
        t = mock.Mock()
        t.order.return_value = {"status": "LIVE", "filled": 2.0, "size": 5.0, "price": 0.21}
        t.buy_now.return_value = (10.0, 7.1)
        v = PolymarketVenue(t)
        lg = leg("polymarket", "tok", 0.7, 0.71)
        self.assertEqual(v.poll("o", lg), (2.0, True))
        n, px, fee = v.buy_now(lg, 0.71, 10)
        self.assertEqual((n, round(px, 4)), (10.0, 0.71))
        self.assertAlmostEqual(fee, poly_fee(10, 0.71, lg.fee))
        t.buy_now.return_value = (0.0, 0.0)
        self.assertEqual(v.buy_now(lg, 0.71, 10), (0.0, 0.0, 0.0))

    def test_live_needs_keys(self):
        from tt.sports.venues import live_venues
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(SystemExit):
                live_venues({"venues": ["kalshi"]})
            with self.assertRaises(SystemExit):
                live_venues({"venues": ["polymarket"]})

    def test_cli_refuses_live_without_flag(self):
        from tt.sports import cli
        path = os.path.join(tempfile.mkdtemp(), "c.json")
        with open(path, "w") as fh:
            fh.write('{"mode": "live", "venues": ["kalshi"]}')
        args = mock.Mock(config=path, status=False, check=False, dry_run=False,
                         i_accept_the_risk=False, once=True)
        with self.assertRaises(SystemExit) as e:
            cli.cmd_stink(args)
        self.assertIn("--i-accept-the-risk", str(e.exception))


class ResearchSimulationTests(unittest.TestCase):
    """The backtest's fill and P&L rules on hand-made candles."""

    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location(
            "research_stink_bids", os.path.join(ROOT, "scripts", "research_stink_bids.py"))
        cls.R = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.R)

    def market(self, lows, result="yes"):
        t0 = START - 120 * 60
        # [end_ts, low, close, yes_bid, yes_ask, volume]; quotes 0.29/0.31 -> mid 0.30
        c = [[t0 - 60, 0.30, 0.30, 0.29, 0.31, 1]]
        c += [[t0 + 60 * (i + 1), lo, lo, 0.29, 0.31, 1] for i, lo in enumerate(lows)]
        return {"ticker": "T", "event": "E", "result": result, "start": START, "candles": c}

    def test_fill_below_bid_and_pnl(self):
        # bid = floor(0.30 x 0.7) = 0.21; a print at 0.20 in-game fills it
        lows = [0.30] * 125 + [0.20]
        r = self.R.simulate(self.market(lows), "nfl", 0.30, "half")
        self.assertTrue(r["filled"])
        self.assertTrue(r["ingame"])
        self.assertEqual((r["bid"], r["contracts"]), (0.21, 9))
        self.assertAlmostEqual(r["gross"], 9 * (1 - 0.21))
        self.assertAlmostEqual(r["net"], r["gross"] - kalshi_fee(9, 0.21, maker=True))

    def test_no_fill_at_the_bid_or_after_the_window(self):
        self.assertFalse(self.R.simulate(self.market([0.21] * 50), "nfl", 0.30, "half")["filled"])
        self.assertTrue(self.R.simulate(self.market([0.21] * 50), "nfl", 0.30, "half",
                                        touch=True)["filled"])
        late = [0.30] * 300 + [0.10]   # after halftime (start + 100 min)
        self.assertFalse(self.R.simulate(self.market(late), "nfl", 0.30, "half")["filled"])
        self.assertTrue(self.R.simulate(self.market(late), "nfl", 0.30, "full")["filled"])

    def test_skips_wide_or_missing_quotes(self):
        m = self.market([0.1])
        m["candles"][0][3:5] = [0.10, 0.40]
        self.assertIsNone(self.R.simulate(m, "nfl", 0.30, "half"))


if __name__ == "__main__":
    unittest.main()
