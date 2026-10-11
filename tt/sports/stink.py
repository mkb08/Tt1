"""Stink bids on game winners, with an optional cross-venue hedge.

The system from the video (Moon Dev, 10 Oct 2026):
  1. For every game in `leagues`, on each venue, rest a limit buy on each
     team at `discount` below its current price (30% in the video), `size_usd`
     each ($2).
  2. Keep the bids up before the game and through the first half; cancel the
     unfilled ones when the first half ends.
  3. A fill is held to settlement (no selling).
  4. Optional third step ("cross-exchange arbitrage triggered by a whale"):
     when a bid fills, buy the OTHER team on the OTHER venue if that locks in
     a profit after both venues' fees. Then the result no longer depends on
     who wins.

Reference price: the consensus of both venues' mid prices, so a bid isn't
set off a price one venue is already dumping. Before the game, a bid is
moved if the reference drifts by more than `reprice`; during the game it is
left alone.

Read docs/STINK_BID_RESEARCH.md before risking money: on 2025-26 Kalshi
data, bids like these filled mostly during games, when the team fell behind,
and lost money after fees.
"""

from __future__ import annotations

import math
import time

from . import espn
from . import games as G
from .venues import PaperVenue, contracts_for, maker_fee, taker_fee
from ..broker import load_json, save_json
from ..notify import Notifier

DEFAULTS = {
    "mode": "paper", "leagues": ["nba"], "venues": ["polymarket", "kalshi"],
    "discount": 0.30, "size_usd": 2.0, "sides": "both", "min_price": 0.10,
    "max_price": 0.90, "half_period": None, "reprice": 0.03, "ahead_hours": 12,
    "poll_seconds": 20, "max_total_usd": 60.0, "max_orders": 40,
    "hedge": {"enabled": True, "min_locked_edge": 0.02},
    "state_file": "state/stink.json", "kalshi_env": "prod",
}


def floor_tick(price: float, tick: float) -> float:
    return round(math.floor(price / tick + 1e-9) * tick, 4)


def settle_value(team: str, winner: str | None) -> float | None:
    if winner is None:
        return None
    return 0.5 if winner == "TIE" else (1.0 if winner == team else 0.0)


class StinkBot:
    def __init__(self, cfg: dict, venues: dict | None = None, notifier: Notifier | None = None):
        self.cfg = {**DEFAULTS, **cfg, "hedge": {**DEFAULTS["hedge"], **cfg.get("hedge", {})}}
        self.state = load_json(self.cfg["state_file"], {}) if self.cfg["state_file"] else {}
        self.state.setdefault("slots", {})
        self.state.setdefault("settled", [])
        self.state.setdefault("paper_orders", {})
        if venues is None:  # paper
            venues = {v: PaperVenue(v, self.state["paper_orders"]) for v in self.cfg["venues"]}
        self.venues = venues
        self.n = notifier or Notifier(log_path="logs/stink.log", title="tt stink")

    # -- bookkeeping -------------------------------------------------------
    def save(self) -> None:
        if self.cfg["state_file"]:
            save_json(self.cfg["state_file"], self.state)

    def committed_usd(self) -> float:
        """Dollars tied up: open bids plus filled positions (and hedges)."""
        tot = 0.0
        for s in self.state["slots"].values():
            if s["status"] == "resting":
                tot += (s["size"] - s["filled"]) * s["bid"]
            tot += s["filled"] * s["bid"] + (s.get("hedge") or {}).get("cost", 0.0)
        return tot

    def open_orders(self) -> int:
        return sum(s["status"] == "resting" for s in self.state["slots"].values())

    # -- the loop ----------------------------------------------------------
    def step(self, index: list[G.GameMarkets] | None = None) -> None:
        cfg = self.cfg
        if index is None:
            index = G.load(cfg["leagues"], cfg["venues"], cfg["ahead_hours"])
        by_game = {gm.game.id: gm for gm in index}
        self.check_fills(by_game)
        for gm in index:
            self.manage_game(gm)
        self.settle(by_game)
        self.save()

    def run(self) -> None:
        cfg = self.cfg
        self.n.alert(f"stink bot started ({cfg['mode']}): {', '.join(cfg['leagues'])} on "
                     f"{', '.join(cfg['venues'])}, {cfg['discount']:.0%} below, "
                     f"${cfg['size_usd']} per bid, hedge {'on' if cfg['hedge']['enabled'] else 'off'}")
        errors = 0
        while True:
            try:
                self.step()
                errors = 0
                time.sleep(cfg["poll_seconds"])
            except KeyboardInterrupt:
                self.n.alert("stink bot stopped by user (resting bids stay on the venues; "
                             "run again to manage them, or cancel them on the website)")
                return
            except Exception as e:  # noqa: BLE001 - keep running, back off
                errors += 1
                wait = min(cfg["poll_seconds"] * 2 ** errors, 600)
                self.n.alert(f"error #{errors}: {type(e).__name__}: {e}; retry in {wait}s")
                time.sleep(wait)

    # -- per game ----------------------------------------------------------
    def wanted_teams(self, gm: G.GameMarkets) -> list[str]:
        teams = [t.abbr for t in gm.game.teams]
        side = self.cfg["sides"]
        if side == "both":
            return teams
        fair = {t: gm.fair(t) or 0.0 for t in teams}
        fav = max(teams, key=lambda t: fair[t])
        return [fav] if side == "favorite" else [t for t in teams if t != fav]

    def manage_game(self, gm: G.GameMarkets) -> None:
        cfg, g = self.cfg, gm.game
        allowed = espn.bids_allowed(g, cfg["half_period"])
        for abbr in self.wanted_teams(gm):
            for vname in cfg["venues"]:
                leg = gm.leg(abbr, vname)
                if not leg or vname not in self.venues:
                    continue
                key = f"{vname}:{leg.market_id}"
                slot = self.state["slots"].get(key)
                if not allowed:
                    if slot and slot["status"] == "resting":
                        self.cancel(slot, leg, f"{g.phase()}: first half over")
                    continue
                if slot and slot["status"] != "resting":
                    continue  # one stink bid per team per venue per game
                ref = gm.fair(abbr)
                if ref is None or not cfg["min_price"] <= ref <= cfg["max_price"]:
                    continue
                bid = floor_tick(ref * (1 - cfg["discount"]), leg.tick)
                if bid < leg.tick:
                    continue
                if slot:
                    if g.state == "pre" and not slot["filled"] and abs(ref - slot["ref"]) > cfg["reprice"]:
                        self.cancel(slot, leg, f"reference moved {slot['ref']:.2f} -> {ref:.2f}")
                        self.place(gm, abbr, leg, ref, bid)
                    continue
                self.place(gm, abbr, leg, ref, bid)

    def size_for(self, leg: G.Leg, bid: float) -> float:
        size = contracts_for(leg, bid, self.cfg["size_usd"])
        if self.cfg["hedge"]["enabled"]:
            # a hedge on Polymarket can't be smaller than its 5-share minimum
            size = max(size, float(self.cfg["hedge"].get("min_contracts", 5)))
        return size

    def place(self, gm: G.GameMarkets, abbr: str, leg: G.Leg, ref: float, bid: float) -> None:
        cfg = self.cfg
        size = self.size_for(leg, bid)
        if self.open_orders() >= cfg["max_orders"]:
            return
        if self.committed_usd() + size * bid > cfg["max_total_usd"]:
            self.n.log(f"budget: skip {abbr} {leg.venue} (max_total_usd {cfg['max_total_usd']})")
            return
        g = gm.game
        half = cfg["half_period"]
        mins = 0 if half == 0 else espn.HALF_MINUTES.get(g.league, 90)
        expires = g.start + (mins + 15) * 60  # backstop if the bot goes offline
        if expires <= time.time() + 240:
            return  # too close to halftime to be worth it
        try:
            oid = self.venues[leg.venue].place_bid(leg, bid, size, expires)
        except Exception as e:  # noqa: BLE001 - one rejected order shouldn't stop the rest
            self.n.alert(f"bid rejected {leg.venue} {gm.game.title} {abbr} @{bid:.2f}: {e}")
            return
        self.state["slots"][f"{leg.venue}:{leg.market_id}"] = {
            "venue": leg.venue, "market_id": leg.market_id, "group_id": leg.group_id,
            "game_id": gm.game.id, "league": gm.game.league, "game": gm.game.title,
            "game_start": gm.game.start, "team": abbr, "url": leg.url, "order_id": oid,
            "ref": ref, "bid": bid, "size": size, "filled": 0.0, "fee": 0.0,
            "status": "resting", "placed": int(time.time()), "hedge": None}
        self.n.log(f"BID {leg.venue} {gm.game.title} {abbr} {size:g} @ {bid:.2f} "
                   f"(price {ref:.2f}, {gm.game.phase()})")

    def cancel(self, slot: dict, leg: G.Leg, why: str) -> None:
        try:
            self.venues[slot["venue"]].cancel(slot["order_id"], leg)
        except Exception as e:  # noqa: BLE001
            self.n.alert(f"cancel failed {slot['venue']} {slot['game']} {slot['team']}: {e}")
            return
        filled, _ = self.venues[slot["venue"]].poll(slot["order_id"], leg)
        self.record_fill(slot, filled, None)
        slot["status"] = "filled" if slot["filled"] else "cancelled"
        self.n.log(f"CANCEL {slot['venue']} {slot['game']} {slot['team']} ({why})"
                   + (f", kept {slot['filled']:g} filled" if slot["filled"] else ""))

    # -- fills and hedges --------------------------------------------------
    def _leg(self, slot: dict, gm: G.GameMarkets | None) -> G.Leg:
        leg = gm.leg(slot["team"], slot["venue"]) if gm else None
        return leg or G.Leg(slot["venue"], slot["market_id"], slot["group_id"], slot["team"],
                            slot["url"])

    def record_fill(self, slot: dict, filled: float, gm: G.GameMarkets | None) -> None:
        new = filled - slot["filled"]
        if new <= 1e-9:
            return
        leg = self._leg(slot, gm)
        slot["filled"] = filled
        slot["fee"] = maker_fee(leg, filled, slot["bid"])
        slot.setdefault("fill_ts", int(time.time()))
        self.n.alert(f"FILLED {slot['venue']} {slot['game']} {slot['team']} {new:g} @ "
                     f"{slot['bid']:.2f} (was {slot['ref']:.2f}) {slot['url']}")

    def check_fills(self, by_game: dict) -> None:
        for slot in self.state["slots"].values():
            gm = by_game.get(slot["game_id"])
            if slot["status"] == "resting":
                leg = self._leg(slot, gm)
                if isinstance(self.venues.get(leg.venue), PaperVenue):
                    G.refresh(leg)  # paper fills read the live ask
                filled, still_open = self.venues[slot["venue"]].poll(slot["order_id"], leg)
                self.record_fill(slot, filled, gm)
                if not still_open:
                    slot["status"] = "filled" if slot["filled"] else "cancelled"
            if gm and slot["filled"] and self.cfg["hedge"]["enabled"] and gm.game.state != "post":
                self.try_hedge(slot, gm)

    def try_hedge(self, slot: dict, gm: G.GameMarkets) -> None:
        """Buy the other team on the other venue if that locks a profit."""
        h = slot.get("hedge") or {"size": 0.0, "cost": 0.0, "fee": 0.0}
        need = slot["filled"] - h["size"]
        if need < 1:
            return
        opp = gm.opponent(slot["team"])
        for vname in self.cfg["venues"]:
            if vname == slot["venue"] or vname not in self.venues:
                continue
            leg = gm.leg(opp, vname)
            if not leg:
                continue
            G.refresh(leg)
            if leg.ask is None:
                continue
            n = max(need, leg.min_size)
            mine = slot["bid"] + slot["fee"] / max(slot["filled"], 1)
            locked = 1 - mine - leg.ask - taker_fee(leg, n, leg.ask) / n
            if locked < self.cfg["hedge"]["min_locked_edge"]:
                if not slot.get("hedge_note"):
                    self.n.log(f"no hedge for {slot['game']} {slot['team']}: other venue "
                               f"{opp} ask {leg.ask:.2f} locks {locked:+.3f}/share (the move "
                               f"looks market-wide, i.e. news); holding")
                    slot["hedge_note"] = True
                continue
            got, px, fee = self.venues[vname].buy_now(leg, leg.ask, n)
            if got <= 0:
                continue
            h = {"venue": vname, "market_id": leg.market_id, "team": opp,
                 "size": h["size"] + got, "cost": h["cost"] + got * px, "fee": h["fee"] + fee,
                 "url": leg.url}
            slot["hedge"] = h
            self.n.alert(f"HEDGED {slot['game']}: {slot['team']} @ {slot['bid']:.2f} on "
                         f"{slot['venue']} + {opp} {got:g} @ {px:.2f} on {vname}; "
                         f"locked about {locked * got:+.2f} USD whoever wins")
            return

    # -- settlement --------------------------------------------------------
    def settle(self, by_game: dict) -> None:
        for key, slot in list(self.state["slots"].items()):
            if slot["status"] == "resting":
                continue
            gm = by_game.get(slot["game_id"])
            if gm is None and time.time() - slot["game_start"] > 6 * 3600:
                if not slot["filled"]:
                    del self.state["slots"][key]  # nothing to settle
                    continue
                gm = self.lookup(slot)
            if not gm:
                continue
            winner = gm.game.winner()
            if winner is None:
                if gm.game.status in espn.DEAD_STATUSES:
                    self.n.alert(f"{slot['game']} is {gm.game.status}: check how each "
                                 f"venue settles it ({slot['url']})")
                continue
            if slot["filled"]:
                v = settle_value(slot["team"], winner)
                pnl = slot["filled"] * (v - slot["bid"]) - slot["fee"]
                h = slot.get("hedge")
                if h:
                    pnl += h["size"] * settle_value(h["team"], winner) - h["cost"] - h["fee"]
                rec = {**slot, "winner": winner, "pnl": round(pnl, 4), "settled": int(time.time())}
                self.state["settled"].append(rec)
                self.n.alert(f"SETTLED {slot['game']} winner {winner}: {slot['team']} "
                             f"{slot['filled']:g} @ {slot['bid']:.2f}"
                             + (f" + hedge {h['team']} {h['size']:g}" if h else "")
                             + f" -> {pnl:+.2f} USD")
            del self.state["slots"][key]

    def lookup(self, slot: dict) -> G.GameMarkets | None:
        """Find a finished game that has left the live index."""
        day = time.strftime("%Y%m%d", time.gmtime(slot["game_start"] - 5 * 3600))
        try:
            for g in espn.scoreboard(slot["league"], day):
                if g.id == slot["game_id"]:
                    return G.GameMarkets(g)
        except Exception as e:  # noqa: BLE001
            self.n.log(f"lookup {slot['game']}: {e}")
        return None

    # -- reporting ---------------------------------------------------------
    def summary(self) -> str:
        st = self.state
        lines = []
        done = st["settled"]
        if done:
            pnl = sum(r["pnl"] for r in done)
            cost = sum(r["filled"] * r["bid"] + (r.get("hedge") or {}).get("cost", 0) for r in done)
            wins = sum(r["pnl"] > 0 for r in done)
            hedged = sum(bool(r.get("hedge")) for r in done)
            lines.append(f"settled: {len(done)} fills, {wins} profitable, {hedged} hedged, "
                         f"P&L {pnl:+.2f} USD on {cost:.2f} USD staked ({pnl / cost:+.1%})")
        resting = [s for s in st["slots"].values() if s["status"] == "resting"]
        held = [s for s in st["slots"].values() if s["filled"]]
        lines.append(f"resting bids: {len(resting)}, open positions: {len(held)}, "
                     f"committed {self.committed_usd():.2f} USD")
        for s in held:
            h = s.get("hedge")
            lines.append(f"  {s['game']:26s} {s['team']:4s} {s['filled']:g} @ {s['bid']:.2f} "
                         f"{s['venue']}" + (f" + {h['team']} {h['size']:g} on {h['venue']}"
                                            if h else " (unhedged)"))
        return "\n".join(lines)

    def plan(self, index: list[G.GameMarkets]) -> list[str]:
        """What the bot would do now, without placing anything."""
        out = []
        for gm in index:
            g = gm.game
            if not espn.bids_allowed(g, self.cfg["half_period"]):
                continue
            for abbr in self.wanted_teams(gm):
                ref = gm.fair(abbr)
                for vname in self.cfg["venues"]:
                    leg = gm.leg(abbr, vname)
                    if not leg or ref is None:
                        continue
                    if not self.cfg["min_price"] <= ref <= self.cfg["max_price"]:
                        out.append(f"{g.league:5s} {g.title:26s} {abbr:4s} {vname:10s} "
                                   f"price {ref:.2f}: outside min/max price, skipped")
                        continue
                    bid = floor_tick(ref * (1 - self.cfg["discount"]), leg.tick)
                    size = self.size_for(leg, bid)
                    out.append(f"{g.league:5s} {g.title:26s} {abbr:4s} {vname:10s} price "
                               f"{ref:.2f} -> bid {size:g} @ {bid:.2f} (${size * bid:.2f}) "
                               f"[{g.phase()}]")
        return out
