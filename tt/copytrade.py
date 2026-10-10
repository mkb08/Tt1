"""Copy trading: mirror a leader's positions into follower accounts.

It copies *positions*, not individual orders. Every poll it reads what the
leader holds, scales it to each follower's equity, and trades only the
difference. That makes it self-healing: a missed poll, a restart or a
rejected order is corrected on the next sync instead of leaving the follower
out of step forever.

Leaders:
  * an exchange account read through a read-only API key (yours, or one
    whose owner gave you that key);
  * a JSON feed - a local file or an HTTPS URL - such as the `publish_file`
    written by `tt.live.Bot`, so one strategy bot can drive many accounts.
"""

from __future__ import annotations

import json
import time
import urllib.request
from dataclasses import dataclass, field

from .broker import load_json, make_broker, save_json
from .notify import Notifier
from .risk import RiskConfig, RiskGuard


# -- leaders -------------------------------------------------------------
class FeedLeader:
    """Reads {"equity": float, "positions": {symbol: qty}, "ts": ms}."""

    def __init__(self, source: str, max_age_s: float = 900):
        self.source, self.max_age_s = source, max_age_s

    def snapshot(self) -> tuple[float, dict[str, float]]:
        if self.source.startswith(("http://", "https://")):
            with urllib.request.urlopen(self.source, timeout=15) as r:
                d = json.load(r)
        else:
            d = load_json(self.source, None)
            if d is None:
                raise RuntimeError(f"leader feed {self.source} missing or unreadable")
        age = time.time() - d.get("ts", 0) / 1000
        if d.get("ts") and age > self.max_age_s:
            raise RuntimeError(f"leader feed is stale ({age:.0f}s old); not copying")
        return float(d["equity"]), {s: float(q) for s, q in d.get("positions", {}).items()}


class BrokerLeader:
    def __init__(self, broker):
        self.broker = broker

    def snapshot(self) -> tuple[float, dict[str, float]]:
        return self.broker.equity(), self.broker.positions()


# -- pure sizing logic (unit tested) -----------------------------------------
@dataclass
class FollowerConfig:
    name: str
    multiplier: float = 1.0      # 2.0 = twice the leader's size relative to equity
    max_leverage: float = 1.0    # cap on gross notional / follower equity
    min_notional: float = 10.0   # ignore adjustments smaller than this (quote ccy)
    symbols: list[str] | None = None   # whitelist; None = copy everything
    allow_short: bool = True
    on_halt: str = "flatten"     # "flatten" or "freeze" when the risk guard trips
    risk: RiskConfig = field(default_factory=lambda: RiskConfig(max_drawdown=0.25,
                                                                max_daily_loss=0.10))


def scale_targets(leader_pos: dict[str, float], leader_equity: float,
                  follower_equity: float, prices: dict[str, float],
                  cfg: FollowerConfig) -> dict[str, float]:
    if leader_equity <= 0 or follower_equity <= 0:
        return {s: 0.0 for s in leader_pos}
    ratio = follower_equity / leader_equity * cfg.multiplier
    targets = {}
    for s, q in leader_pos.items():
        if cfg.symbols is not None and s not in cfg.symbols:
            continue
        t = q * ratio
        if t < 0 and not cfg.allow_short:
            t = 0.0
        targets[s] = t
    gross = sum(abs(q) * prices[s] for s, q in targets.items())
    cap = follower_equity * cfg.max_leverage
    if gross > cap > 0:
        targets = {s: q * cap / gross for s, q in targets.items()}
    return targets


def plan_orders(current: dict[str, float], targets: dict[str, float],
                prices: dict[str, float], min_notional: float) -> list[tuple[str, float, bool]]:
    """Orders (symbol, signed qty, reduce_only) moving `current` to `targets`.

    Reductions come first so they free margin for the increases. A flip from
    long to short becomes a reduce-only close plus a fresh open. Closing a
    position entirely is never skipped for being small.
    """
    reduces, opens = [], []
    for s in sorted(set(current) | set(targets)):
        cur, tgt = current.get(s, 0.0), targets.get(s, 0.0)
        px = prices[s]
        if cur and tgt and (cur > 0) != (tgt > 0):
            reduces.append((s, -cur, True))
            if abs(tgt) * px >= min_notional:
                opens.append((s, tgt, False))
            continue
        delta = tgt - cur
        if delta == 0:
            continue
        closing_fully = cur != 0 and abs(tgt) * px < min_notional
        if closing_fully:
            reduces.append((s, -cur, True))
        elif abs(delta) * px < min_notional:
            continue
        elif abs(tgt) < abs(cur):
            reduces.append((s, delta, True))
        else:
            opens.append((s, delta, False))
    return reduces + opens


def reduction_only(cur: float, tgt: float) -> float:
    """`tgt` if it only shrinks the position `cur` (same side or flat), else `cur`."""
    if abs(tgt) <= abs(cur) and (tgt == 0 or (tgt > 0) == (cur > 0)):
        return tgt
    return cur


# -- engine ----------------------------------------------------------------
class CopyEngine:
    def __init__(self, leader, followers: list[tuple[FollowerConfig, object]],
                 price_fn, notifier: Notifier | None = None, dry_run: bool = True,
                 state_path: str = "state/copy.json"):
        self.leader, self.followers, self.price_fn = leader, followers, price_fn
        self.n = notifier or Notifier(title="tt copy")
        self.dry_run, self.state_path = dry_run, state_path
        st = load_json(state_path, {})
        # every symbol ever seen from the leader, so we can close it after the
        # leader exits (it then disappears from the leader's positions)
        self.universe: set[str] = set(st.get("universe", []))
        self.guards = {cfg.name: RiskGuard(cfg.risk, st.get("guards", {}).get(cfg.name))
                       for cfg, _ in followers}

    def _save(self):
        save_json(self.state_path, {"universe": sorted(self.universe),
                                    "guards": {k: g.state() for k, g in self.guards.items()}})

    def sync_once(self) -> dict[str, list]:
        l_eq, l_pos = self.leader.snapshot()
        self.universe |= set(l_pos)
        prices = {s: self.price_fn(s) for s in self.universe}
        report = {}
        for cfg, broker in self.followers:
            try:
                report[cfg.name] = self._sync_follower(cfg, broker, l_eq, l_pos, prices)
            except Exception as e:  # noqa: BLE001 - one bad follower must not stop the rest
                self.n.alert(f"{cfg.name}: sync failed: {type(e).__name__}: {e}")
                report[cfg.name] = [("error", str(e))]
        self._save()
        return report

    def _sync_follower(self, cfg, broker, l_eq, l_pos, prices):
        f_eq = broker.equity()
        guard = self.guards[cfg.name]
        guard.update(f_eq, int(time.time() * 1000))
        tracked = [s for s in self.universe if cfg.symbols is None or s in cfg.symbols]
        current = {s: broker.position(s) for s in tracked}
        if guard.halted:
            if cfg.on_halt != "flatten" or not any(current.values()):
                return []
            targets = {s: 0.0 for s in tracked}
            self.n.alert(f"{cfg.name}: {guard.reason}; flattening")
        else:
            targets = scale_targets({s: l_pos.get(s, 0.0) for s in tracked}, l_eq, f_eq, prices, cfg)
            ok, why = guard.allow_entries(f_eq)
            if not ok:  # daily loss limit: only let positions shrink today
                targets = {s: reduction_only(current.get(s, 0.0), t) for s, t in targets.items()}
        orders = plan_orders(current, targets, prices,
                             max(cfg.min_notional, *(broker.min_notional(s) for s in tracked))
                             if tracked else cfg.min_notional)
        for s, q, reduce_only in orders:
            if self.dry_run:
                self.n.log(f"[dry-run] {cfg.name}: {s} {q:+.6f} reduce_only={reduce_only}")
                continue
            res = broker.market_order(s, q, reduce_only=reduce_only)
            self.n.alert(f"{cfg.name}: {s} {q:+.6f} -> {res}")
        return orders

    def run(self, poll_seconds: float = 15) -> None:
        self.n.alert(f"copy engine started ({'DRY RUN' if self.dry_run else 'LIVE'}) "
                     f"followers: {[c.name for c, _ in self.followers]}")
        errors = 0
        while True:
            try:
                self.sync_once()
                errors = 0
                time.sleep(poll_seconds)
            except KeyboardInterrupt:
                self.n.alert("copy engine stopped by user")
                return
            except Exception as e:  # noqa: BLE001
                errors += 1
                wait = min(poll_seconds * 2 ** errors, 600)
                self.n.alert(f"leader read failed #{errors}: {e}; retry in {wait}s")
                time.sleep(wait)


def build_engine(cfg: dict) -> CopyEngine:
    from .data import fetch_price
    lc = cfg["leader"]
    if lc.get("type", "feed") == "feed":
        leader = FeedLeader(lc["source"], lc.get("max_age_s", 900))
    else:
        leader = BrokerLeader(make_broker(lc))
    followers = []
    for fc in cfg["followers"]:
        fcfg = FollowerConfig(
            name=fc["name"], multiplier=fc.get("multiplier", 1.0),
            max_leverage=fc.get("max_leverage", 1.0), min_notional=fc.get("min_notional", 10.0),
            symbols=fc.get("symbols"), allow_short=fc.get("market_type", "spot") != "spot",
            on_halt=fc.get("on_halt", "flatten"),
            risk=RiskConfig.from_dict({"max_drawdown": 0.25, "max_daily_loss": 0.10,
                                       **fc.get("risk", {})}))
        fc = {"price_source": cfg.get("price_source", "binance"), **fc}
        broker = make_broker(fc, state_path=f"state/paper_{fc['name']}.json")
        followers.append((fcfg, broker))
    src = cfg.get("price_source", "binance")
    return CopyEngine(leader, followers, lambda s: fetch_price(s, src),
                      dry_run=cfg.get("dry_run", True))
