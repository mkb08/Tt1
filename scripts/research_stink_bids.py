"""Do sports stink bids make money? Pre-registered study on settled Kalshi
game-winner markets. See docs/STINK_BID_RESEARCH.md.

    python scripts/research_stink_bids.py fetch    # ~40 min, writes data/stink/*.json.gz
    python scripts/research_stink_bids.py report   # writes research/stink_bids.json
    python scripts/research_stink_bids.py hedge    # step 3: was a Polymarket hedge there?

The rule under test is the one from the video, fixed before any data was seen:
  - reference = mid price (between best bid and ask) 2 hours before the start
  - rest a limit buy 30% below it, $2 per bid, on BOTH teams of every game
  - cancel unfilled bids at halftime; hold fills to settlement (no selling)
Fills are counted only when a trade printed BELOW the bid (traded through),
because a bid at the back of the queue may not fill when price only touches it.

Pass rule (written before running): net profit after Kalshi maker fees, a 95%
bootstrap interval on return-on-cost entirely above zero, and profit in both
the earlier and the later half of the period. Other discounts and windows are
reported as context only: 5 discounts x 3 windows = 15 variants, so one of
them looking good by luck is likely.
"""
import gzip
import json
import math
import os
import random
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from tt.sports import kalshi as K  # noqa: E402
from tt.sports.fees import kalshi_fee  # noqa: E402

DATA = os.path.join(ROOT, "data", "stink")
OUT = os.path.join(ROOT, "research", "stink_bids.json")
LEAGUES = {"nba": ("KXNBAGAME", "basketball_game"), "nfl": ("KXNFLGAME", "football_game"),
           "nhl": ("KXNHLGAME", "hockey_match"), "mlb": ("KXMLBGAME", "baseball_game"),
           "ncaaf": ("KXNCAAFGAME", "football_game")}
# Older settled markets live in Kalshi's archive (GET /historical/...); one
# season back covers the 2025-26 NBA and NHL and the 2025 NFL season.
HISTORY_FROM = "2025-09-01T00:00:00Z"
# minutes from start to the end of the first half (or the closest break):
# NFL ~1h40, college ~1h50, NBA ~1h15, NHL mid-2nd period ~1h05, MLB end of
# the 5th inning ~1h30. Approximate: Kalshi history has no game clock.
HALF_MIN = {"nfl": 100, "ncaaf": 110, "nba": 75, "nhl": 65, "mlb": 90}
FEE_MULT = {"mlb": 0.5}
PLACE_BEFORE_MIN = 120
WINDOW_AFTER_MIN = 300
SIZE_USD = 2.0
DISCOUNTS = (0.10, 0.20, 0.30, 0.40, 0.50)
WINDOWS = ("pregame", "half", "full")
PRIMARY = (0.30, "half")
MAX_SPREAD = 0.10


def utc(ts: int) -> datetime:
    return datetime.fromtimestamp(ts, timezone.utc)


def iso(ts: str) -> int:
    return int(datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp())


def patient(fn, *a, **k):
    """Kalshi rate-limits bursts (HTTP 429): wait it out instead of failing."""
    for attempt in range(10):
        try:
            return fn(*a, **k)
        except RuntimeError as e:
            if "HTTP 429" not in str(e) or attempt == 9:
                raise
            time.sleep(30)


def g(d, key):
    """Live candles say low_dollars / volume_fp; archived ones low / volume."""
    d = d or {}
    for k in (key + "_dollars", key + "_fp", key):
        if d.get(k) not in (None, ""):
            return K.f(d[k])
    return 0.0


def fetch():
    os.makedirs(DATA, exist_ok=True)
    for (league, (series, mtype)), hist in [(x, h) for h in (False, True) for x in LEAGUES.items()]:
        path = os.path.join(DATA, f"{league}{'_hist' if hist else ''}.json.gz")
        have = {}
        if os.path.exists(path):
            with gzip.open(path, "rt") as fh:
                have = {m["ticker"]: m for m in json.load(fh)}
            if not hist or os.path.exists(path + ".espn"):
                print(f"{league}{' archive' if hist else ''}: cached")
                continue
        if hist:
            ms = list(K._paged("/historical/markets", "markets",
                               {"series_ticker": series, "limit": 1000}, K.PROD, 200))
            ms = [m for m in ms if m.get("result") in ("yes", "no")
                  and m["close_time"] >= HISTORY_FROM]
        else:
            ms = [m for m in K.markets(series_ticker=series, status="settled")
                  if m.get("result") in ("yes", "no")]
        if not ms:
            print(f"{league}: no settled markets")
            continue
        first = min(iso(m["open_time"]) for m in ms) - 86400
        starts = {}
        for mi in K.milestones(type=mtype, minimum_start_date=utc(first)
                               .strftime("%Y-%m-%dT%H:%M:%SZ"), max_pages=200):
            for ev in mi.get("primary_event_tickers") or []:
                starts.setdefault(ev, iso(mi["start_date"]))
        espn_starts(league, ms, starts)
        ms = [m for m in ms if m["ticker"] not in have]
        rows = list(have.values())

        def one(m):
            st = starts.get(m["event_ticker"])
            if st is None:
                return None
            a, b = st - PLACE_BEFORE_MIN * 60 - 600, st + WINDOW_AFTER_MIN * 60
            if hist:
                c = patient(K.get_json, f"{K.PROD}/historical/markets/{m['ticker']}/candlesticks",
                            {"start_ts": a, "end_ts": b, "period_interval": 1}
                            ).get("candlesticks") or []
            else:
                c = patient(K.candles, series, m["ticker"], a, b)
            keep = [[x["end_period_ts"], g(x.get("price"), "low") or None,
                     g(x.get("price"), "close") or None, g(x.get("yes_bid"), "close"),
                     g(x.get("yes_ask"), "close"), g(x, "volume")] for x in c]
            return {"ticker": m["ticker"], "event": m["event_ticker"], "result": m["result"],
                    "start": st, "volume": K.f(m.get("volume_fp")), "candles": keep}

        t0 = time.time()
        with ThreadPoolExecutor(3) as pool:
            for i, r in enumerate(pool.map(one, ms)):
                if r:
                    rows.append(r)
                if i % 200 == 0:
                    print(f"  {league}: {i}/{len(ms)} markets, {time.time() - t0:.0f}s", flush=True)
        with gzip.open(path, "wt") as fh:
            json.dump(rows, fh)
        if hist:
            open(path + ".espn", "w").close()  # marks the ESPN top-up as done
        print(f"{league}: {len(rows)} settled markets with a start time and candles "
              f"({len(ms)} new this run)")


def espn_starts(league, ms, starts):
    """Kalshi's schedule (/milestones) only goes back to early 2026 for some
    leagues. For other games, take the start time from ESPN's scoreboard,
    matching the two teams by name on the ticker's date."""
    from tt.sports import espn, games as G
    by_event = {}
    for m in ms:
        by_event.setdefault(m["event_ticker"], []).append(m)
    days = {}
    found = 0
    for ev, pair in by_event.items():
        if ev in starts or len(pair) != 2:
            continue
        day = K.ticker_date(ev)
        if not day:
            continue
        cands = []
        for d in {day, (datetime.fromisoformat(day) + timedelta(days=1)).strftime("%Y-%m-%d")}:
            if d not in days:
                try:
                    days[d] = espn.scoreboard(league, d.replace("-", ""))
                except Exception:  # noqa: BLE001 - a missing day just stays unmatched
                    days[d] = []
            cands += days[d]
        labels = [(m.get("yes_sub_title") or "", m["ticker"].rsplit("-", 1)[-1]) for m in pair]
        hits = [g for g in cands if G.us_date(g.start) == day and G.assign(g.teams, labels)]
        if len(hits) == 1:
            starts[ev] = hits[0].start
            found += 1
    print(f"  {league}: ESPN supplied {found} start times Kalshi's schedule lacked", flush=True)


def mid_at(candles, ts):
    """Mid of the last two-sided quote at or before ts."""
    best = None
    for c in candles:
        if c[0] > ts:
            break
        if c[3] and c[4] and c[4] > c[3]:
            best = c
    return ((best[3] + best[4]) / 2, best[4] - best[3]) if best else (None, None)


def simulate(m, league, discount, window, touch=False):
    """One resting bid on one team. None if the market isn't tradable at T0."""
    st, c = m["start"], m["candles"]
    t0 = st - PLACE_BEFORE_MIN * 60
    ref, spread = mid_at(c, t0)
    if ref is None or spread > MAX_SPREAD:
        return None
    bid = math.floor(ref * (1 - discount) * 100 + 1e-9) / 100
    if bid < 0.01:
        return None
    end = {"pregame": st, "half": st + HALF_MIN[league] * 60,
           "full": st + WINDOW_AFTER_MIN * 60}[window]
    out = {"ticker": m["ticker"], "event": m["event"], "start": st, "ref": ref,
           "bid": bid, "filled": False}
    for i, x in enumerate(c):
        if x[0] <= t0 or x[0] > end or x[1] is None:
            continue
        if x[1] < bid or (touch and x[1] <= bid):
            n = math.floor(SIZE_USD / bid)
            if n < 1:
                return None
            won = m["result"] == "yes"
            fee = kalshi_fee(n, bid, maker=True, multiplier=FEE_MULT.get(league, 1.0))
            later, _ = mid_at(c, x[0] + 600)
            out.update(filled=True, fill_ts=x[0], ingame=x[0] > st, contracts=n,
                       cost=n * bid, gross=n * ((1.0 if won else 0.0) - bid), fee=fee,
                       won=won, mid_10m=later,
                       bounced=later is not None and later >= bid + (ref - bid) / 2)
            out["net"] = out["gross"] - fee
            return out
    return out


def summarize(fills, eligible, boot=2000, seed=7):
    n = len(fills)
    if not n:
        return {"eligible": eligible, "fills": 0}
    cost = sum(x["cost"] for x in fills)
    net = sum(x["net"] for x in fills)
    gross = sum(x["gross"] for x in fills)
    by_game = {}
    for x in fills:
        g = by_game.setdefault(x["event"], [0.0, 0.0])
        g[0] += x["net"]
        g[1] += x["cost"]
    games = list(by_game.values())
    rnd = random.Random(seed)
    rois = []
    for _ in range(boot):
        s = [games[rnd.randrange(len(games))] for _ in games]
        c = sum(g[1] for g in s)
        rois.append(sum(g[0] for g in s) / c if c else 0.0)
    rois.sort()
    return {"eligible": eligible, "fills": n, "fill_rate": n / eligible if eligible else 0,
            "win_rate": sum(x["won"] for x in fills) / n,
            "avg_price": statistics.mean(x["bid"] for x in fills),
            "cost": round(cost, 2), "gross": round(gross, 2), "net": round(net, 2),
            "fees": round(gross - net, 2), "roi_gross": gross / cost, "roi_net": net / cost,
            "roi_net_ci95": [rois[int(0.025 * boot)], rois[int(0.975 * boot) - 1]],
            "ingame_share": sum(x["ingame"] for x in fills) / n}


def report():
    data = load_data()
    allstarts = sorted(m["start"] for ms in data.values() for m in ms)
    split = allstarts[len(allstarts) // 2]
    out = {"period": [utc(allstarts[0]).strftime("%Y-%m-%d"),
                      utc(allstarts[-1]).strftime("%Y-%m-%d")],
           "split": utc(split).strftime("%Y-%m-%d"),
           "markets": {k: len(v) for k, v in data.items()},
           "games": {k: len({m["event"] for m in v}) for k, v in data.items()},
           "variants": {}, "primary": {}}

    def run(discount, window, leagues=None, touch=False, part=None):
        fills, eligible = [], 0
        for league, ms in data.items():
            if leagues and league not in leagues:
                continue
            for m in ms:
                if part == "early" and m["start"] >= split or part == "late" and m["start"] < split:
                    continue
                r = simulate(m, league, discount, window, touch)
                if r is None:
                    continue
                eligible += 1
                if r["filled"]:
                    fills.append(r)
        return fills, eligible

    for d in DISCOUNTS:
        for w in WINDOWS:
            fills, el = run(d, w)
            out["variants"][f"{int(d * 100)}%/{w}"] = summarize(fills, el)

    d, w = PRIMARY
    fills, el = run(d, w)
    p = out["primary"]
    p["all"] = summarize(fills, el)
    p["touch_fills"] = summarize(*run(d, w, touch=True))
    p["early"] = summarize(*run(d, w, part="early"))
    p["late"] = summarize(*run(d, w, part="late"))
    p["by_league"] = {lg: summarize(*run(d, w, leagues=[lg])) for lg in data}
    p["pregame_fills"] = summarize([x for x in fills if not x["ingame"]], el)
    p["ingame_fills"] = summarize([x for x in fills if x["ingame"]], el)
    p["bounced_fills"] = summarize([x for x in fills if x["bounced"]], el)
    p["stayed_down_fills"] = summarize([x for x in fills if not x["bounced"]], el)
    a = p["all"]
    p["passes"] = bool(a.get("fills") and a["net"] > 0 and a["roi_net_ci95"][0] > 0
                       and p["early"].get("net", 0) > 0 and p["late"].get("net", 0) > 0)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    if os.path.exists(OUT):  # keep the hedge step's results
        with open(OUT) as fh:
            old = json.load(fh)
        if "hedge" in old:
            out["hedge"] = old["hedge"]
    with open(OUT, "w") as fh:
        json.dump(out, fh, indent=1)
    write_site(out)
    print(json.dumps({k: out[k] for k in ("period", "split", "markets", "games")}))
    print(f"\n{'variant':16s} {'elig':>5s} {'fills':>5s} {'fill%':>6s} {'win%':>5s} "
          f"{'avgpx':>6s} {'gross':>7s} {'net':>7s} {'ROI':>7s} {'95% CI':>17s} {'ingame':>6s}")

    def row(name, s):
        if not s.get("fills"):
            print(f"{name:16s} {s['eligible']:5d}     0")
            return
        lo, hi = s["roi_net_ci95"]
        print(f"{name:16s} {s['eligible']:5d} {s['fills']:5d} {s['fill_rate']:6.1%} "
              f"{s['win_rate']:5.0%} {s['avg_price']:6.2f} {s['gross']:+7.2f} {s['net']:+7.2f} "
              f"{s['roi_net']:+7.1%} [{lo:+6.1%},{hi:+6.1%}] {s['ingame_share']:6.0%}")

    for k, s in out["variants"].items():
        row(k, s)
    print("\nPRIMARY (30% below, until halftime):")
    for k in ("all", "touch_fills", "early", "late", "pregame_fills", "ingame_fills",
              "bounced_fills", "stayed_down_fills"):
        row(k, p[k])
    for lg, s in p["by_league"].items():
        row("  " + lg, s)
    print(f"\npasses the pre-registered rule: {p['passes']}")


def write_site(out):
    """The summary the website's sports page shows (site/data/stink.json)."""
    a = out["primary"]["all"]
    pct = lambda x: f"{'+' if x > 0 else '−' if x < 0 else ''}{abs(x) * 100:.1f}%"  # noqa: E731
    lo, hi = a["roi_net_ci95"]
    games = sum(out["games"].values())
    verdict = (f"{'It passed' if out['primary']['passes'] else 'It lost money'}. "
               f"Of {a['eligible']:,} bids on {games:,} games, {a['fills']:,} filled, "
               f"{out['primary']['ingame_fills']['fills']:,} of them during play, as the game "
               f"turned against that team. "
               f"Fills won {a['win_rate']:.0%} of the time at an average price of "
               f"{a['avg_price'] * 100:.0f}¢, a return of {pct(a['roi_net'])} after Kalshi's fees "
               f"(95% range {pct(lo)} to {pct(hi)}). Only {out['primary']['pregame_fills']['fills']} "
               f"filled before kick-off, where a panicking whale would be the reason.")
    site = {"verdict": verdict, "primary": out["primary"], "variants": out["variants"],
            "period_text": f"Settled Kalshi game-winner markets from {out['period'][0]} to "
                           f"{out['period'][1]}: {games:,} games in the NFL, college football, "
                           f"NBA, NHL and MLB."}
    h = out.get("hedge", {}).get("all")
    if h and h.get("matched_on_polymarket"):
        site["hedge"] = h
        site["hedge_text"] = (
            f"The hedge (step 3): of {h['matched_on_polymarket']:,} NFL and NBA fills with a "
            f"matching Polymarket market, buying the other team there would have locked 2¢ a "
            f"share or more after fees {h['hedge_available']:,} times ({h['hedge_share']:.0%}). "
            f"Those were the real one-venue dips: held, they returned "
            f"{pct(h['hedgeable_hold_roi'])}; hedged, they locked ${h['hedgeable_lock_pnl']:,.0f}. "
            f"The other {h['unhedgeable_fills']:,} were news (Polymarket had moved too) and "
            f"returned {pct(h['unhedgeable_hold_roi'])}. Hedging can't rescue those, so the "
            f"system still loses.")
    path = os.path.join(ROOT, "site", "data", "stink.json")
    with open(path, "w") as fh:
        json.dump(site, fh, separators=(",", ":"))


def load_data():
    data = {}
    for league in LEAGUES:
        rows = {}
        for suffix in ("_hist", ""):
            path = os.path.join(DATA, f"{league}{suffix}.json.gz")
            if os.path.exists(path):
                with gzip.open(path, "rt") as fh:
                    rows.update({m["ticker"]: m for m in json.load(fh)})
        if rows:
            data[league] = sorted(rows.values(), key=lambda m: m["start"])
    return data


def hedge():
    """Step 3 of the video's system, tested on the primary rule's Kalshi fills
    in NFL and NBA games: at the minute the stink bid filled, would buying the
    other team on Polymarket have locked in a profit (2c a share or more after
    both venues' fees)? Polymarket's minute price history stands in for its
    book, plus half a cent of spread. Fills on Polymarket can't be tested: its
    history has no intraminute lows."""
    from tt.sports import espn, games as G, polymarket as P
    from tt.sports.fees import poly_fee
    data = load_data()
    cache_path = os.path.join(DATA, "hedge_cache.json.gz")
    cache = {}
    if os.path.exists(cache_path):
        with gzip.open(cache_path, "rt") as fh:
            cache = json.load(fh)
    d, w = PRIMARY
    rows = []
    for league in ("nfl", "nba"):
        fills = [r for m in data.get(league, []) for r in [simulate(m, league, d, w)]
                 if r and r["filled"]]
        print(f"{league}: {len(fills)} Kalshi fills to check", flush=True)

        def one(r):
            key = r["ticker"]
            if cache.get(key, {}).get("p") is not None:
                return r, cache[key]
            hist = r["start"] < iso("2026-08-11T00:00:00Z")
            mk = K.get_json(f"{K.PROD}/{'historical/' if hist else ''}markets/{r['ticker']}")["market"]
            label = mk.get("yes_sub_title") or ""
            evs = K.get_json(f"{P.GAMMA}/events", {
                "tag_id": P.TAGS[league], "closed": "true", "limit": 200,
                "end_date_min": utc(r["start"] - 4 * 3600).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "end_date_max": utc(r["start"] + 4 * 3600).strftime("%Y-%m-%dT%H:%M:%SZ")})
            for ev in evs:
                m = P.moneyline(ev)
                if not m:
                    continue
                outs = G._poly_labels(ev, m)
                teams = [espn.Team(lab, ab or "", lab.split()[-1], False) for lab, ab, _ in outs]
                scores = [G.score(t, label, r["ticker"].rsplit("-", 1)[-1]) for t in teams]
                if max(scores) == 0 or scores[0] == scores[1]:
                    continue
                tok = outs[scores.index(max(scores))][2]
                h = P.price_history(tok, r["fill_ts"] - 240, r["fill_ts"] + 60)
                before = [x for x in h if x["t"] <= r["fill_ts"] + 30]
                res = {"p": before[-1]["p"] if before else None, "slug": ev["slug"],
                       "fee": m.get("feeSchedule") or {}}
                return r, res
            return r, {"p": None}

        with ThreadPoolExecutor(4) as pool:
            for r, res in pool.map(one, fills):
                cache[r["ticker"]] = res
                rows.append((league, r, res))
    with gzip.open(cache_path, "wt") as fh:
        json.dump(cache, fh)

    out = {}
    for league in ("nfl", "nba", "all"):
        sel = [(r, res) for lg, r, res in rows if league in (lg, "all")]
        matched = [(r, res) for r, res in sel if res.get("p") is not None]
        hold = hedged_pnl = cost_h = cost_hold = 0.0
        n_hedge, locks = 0, []
        # split: fills where a hedge existed (the other venue hadn't moved)
        # and fills where it didn't (both venues moved: news)
        part = {"yes": [0.0, 0.0, 0.0, 0], "no": [0.0, 0.0, 0.0, 0]}  # hold, cost, lock, n
        for r, res in matched:
            n, bid = r["contracts"], r["bid"]
            other_ask = min(0.99, 1 - res["p"] + 0.005)
            lock = 1 - bid - r["fee"] / n - other_ask - poly_fee(n, other_ask, res["fee"]) / n
            locks.append(lock)
            hold += r["net"]
            cost_hold += r["cost"]
            q = part["yes" if lock >= 0.02 else "no"]
            q[0] += r["net"]
            q[1] += r["cost"]
            q[3] += 1
            if lock >= 0.02:
                n_hedge += 1
                hedged_pnl += n * lock
                q[2] += n * lock
                cost_h += r["cost"] + n * other_ask
            else:
                hedged_pnl += r["net"]
                cost_h += r["cost"]
        out[league] = {"fills": len(sel), "matched_on_polymarket": len(matched),
                       "hedge_available": n_hedge,
                       "hedge_share": n_hedge / len(matched) if matched else 0,
                       "median_lock": statistics.median(locks) if locks else None,
                       "hold_pnl": round(hold, 2), "hold_roi": hold / cost_hold if cost_hold else 0,
                       "hedge_or_hold_pnl": round(hedged_pnl, 2),
                       "hedge_or_hold_roi": hedged_pnl / cost_h if cost_h else 0,
                       "hedgeable_hold_pnl": round(part["yes"][0], 2),
                       "hedgeable_hold_roi": part["yes"][0] / part["yes"][1] if part["yes"][1] else 0,
                       "hedgeable_lock_pnl": round(part["yes"][2], 2),
                       "unhedgeable_fills": part["no"][3],
                       "unhedgeable_hold_pnl": round(part["no"][0], 2),
                       "unhedgeable_hold_roi": part["no"][0] / part["no"][1] if part["no"][1] else 0}
        print(league, json.dumps(out[league]))
    res_all = {}
    if os.path.exists(OUT):
        with open(OUT) as fh:
            res_all = json.load(fh)
    res_all["hedge"] = out
    with open(OUT, "w") as fh:
        json.dump(res_all, fh, indent=1)
    if "primary" in res_all:
        write_site(res_all)


if __name__ == "__main__":
    {"fetch": fetch, "report": report, "hedge": hedge}[
        sys.argv[1] if len(sys.argv) > 1 else "report"]()
