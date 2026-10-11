// Pure logic for the sports page, ported from tt/sports (games.py, fees.py,
// espn.py, stink.py). tests/test_site_sports.py checks it gives the same
// answers as the Python.

export const HALF_PERIOD = { nfl: 2, ncaaf: 2, nba: 2, wnba: 2, ncaab: 1, nhl: 1, mlb: 5 };
const BREAKS = new Set(["STATUS_HALFTIME", "STATUS_END_PERIOD"]);
const DEAD = new Set(["STATUS_POSTPONED", "STATUS_CANCELED", "STATUS_SUSPENDED",
                      "STATUS_FORFEIT", "STATUS_DELAYED"]);

// Kalshi: round up to the cent; rate 0.07 taker / 0.0175 maker
export function kalshiFee(n, p, maker = false, mult = 1) {
  const raw = mult * (maker ? 0.0175 : 0.07) * n * p * (1 - p);
  return raw > 0 ? Math.ceil(raw * 100 - 1e-9) / 100 : 0;
}

// Polymarket: takers only, per the market's feeSchedule
export function polyFee(n, p, sched, maker = false) {
  if (maker || !sched) return 0;
  const rate = Number(sched.rate || 0), exp = Number(sched.exponent || 1);
  return Math.round(n * rate * Math.pow(p * (1 - p), exp) * 1e5) / 1e5;
}

export function takerFee(leg, n, p) {
  return leg.venue === "kalshi" ? kalshiFee(n, p, false, leg.fee.multiplier || 1)
                                : polyFee(n, p, leg.fee);
}

// Profit per share of buying team A on one venue and team B on the other at
// their asks, after taker fees. Positive = arbitrage.
export function lockedEdge(a, b, n = 10) {
  if (a.ask == null || b.ask == null) return null;
  return 1 - a.ask - b.ask - takerFee(a, n, a.ask) / n - takerFee(b, n, b.ask) / n;
}

export const floorTick = (p, tick) => Math.round(Math.floor(p / tick + 1e-9) * tick * 1e4) / 1e4;

export function norm(s) {
  s = String(s || "").toLowerCase().replaceAll("&", " and ").replaceAll("st.", "state");
  return s.replace(/[^a-z0-9]+/g, " ").trim();
}

// 3 exact name, 2 same abbreviation, 1 prefix ("New York J"), 0 no match
export function score(team, label, abbr) {
  const lab = norm(label), name = norm(team.name), short = norm(team.short);
  if (!lab) return 0;
  if (lab === name || lab === short) return 3;
  if (abbr && abbr.toLowerCase() === team.abbr.toLowerCase()) return 2;
  if (name.startsWith(lab)) return 1;
  return 0;
}

// Pair two venue labels [[label, abbr], ...] with two teams; null unless
// one pairing fits both and beats the other.
export function assign(teams, labels) {
  if (teams.length !== 2 || labels.length !== 2) return null;
  const st = [score(teams[0], ...labels[0]), score(teams[1], ...labels[1])];
  const sw = [score(teams[1], ...labels[0]), score(teams[0], ...labels[1])];
  const s = Math.min(...st), w = Math.min(...sw);
  const sum = (a) => a[0] + a[1];
  if (s > 0 && (s > w || (s === w && sum(st) > sum(sw)))) return [0, 1];
  if (w > 0 && (w > s || (w === s && sum(sw) > sum(st)))) return [1, 0];
  return null;
}

export function bidsAllowed(g, half = null) {
  if (DEAD.has(g.status) || g.state === "post") return false;
  if (g.state === "pre") return true;
  const n = half == null ? (HALF_PERIOD[g.league] ?? 2) : half;
  if (g.period > n || n === 0) return false;
  return !(g.period === n && BREAKS.has(g.status));
}

export function phase(g) {
  if (g.state === "pre") return "pre-game";
  if (g.state === "post") return "final";
  const unit = g.league === "mlb" ? "Inning" : g.league === "nhl" ? "Period" :
    (g.league === "ncaab" ? "Half" : "Q");
  const brk = BREAKS.has(g.status) ? (g.status === "STATUS_HALFTIME" ? " · halftime" : " · break") : "";
  return unit === "Q" ? `Q${g.period} ${g.clock}${brk}` : `${unit} ${g.period}${brk}`;
}

export function parseEspn(league, e) {
  const comp = e.competitions[0], st = comp.status || {};
  const teams = [...comp.competitors]
    .sort((a, b) => (a.homeAway === "away" ? -1 : 1) - (b.homeAway === "away" ? -1 : 1))
    .map((c) => ({ name: c.team.displayName || "", abbr: c.team.abbreviation || "",
                   short: c.team.shortDisplayName || c.team.name || "", home: c.homeAway === "home",
                   winner: !!c.winner, logo: c.team.logo || "" }));
  return { league, id: String(e.id), start: Date.parse(e.date) / 1000, state: st.type?.state || "pre",
           status: st.type?.name || "", period: Number(st.period || 0),
           clock: String(st.displayClock || ""), teams, legs: {} };
}

const MONTHS = "JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split(" ");
export function tickerDate(ev) {
  const m = /-(\d{2})([A-Z]{3})(\d{2})/.exec(ev);
  if (!m || !MONTHS.includes(m[2])) return null;
  return `20${m[1]}-${String(MONTHS.indexOf(m[2]) + 1).padStart(2, "0")}-${m[3]}`;
}

export const usDate = (ts) => new Date((ts - 5 * 3600) * 1000).toISOString().slice(0, 10);

function pick(cands) {
  if (!cands.length) return null;
  cands.sort((a, b) => b[0] - a[0]);
  if (cands.length > 1 && cands[0][0] === cands[1][0]) return null;
  return cands[0];
}

const num = (x) => (x === undefined || x === null || x === "" ? 0 : Number(x));

export function attachKalshi(games, events, mult = 1) {
  for (const ev of events) {
    const ms = (ev.markets || []).filter((m) => ["active", "open", undefined].includes(m.status));
    if (ms.length !== 2) continue;
    const day = tickerDate(ev.event_ticker);
    const labels = ms.map((m) => [m.yes_sub_title || "", m.ticker.split("-").pop()]);
    const cands = [];
    for (const g of games) {
      if (day && Math.abs(Date.parse(day) - Date.parse(usDate(g.start))) > 86400e3) continue;
      const a = assign(g.teams, labels);
      if (a) cands.push([score(g.teams[a[0]], ...labels[0]) + score(g.teams[a[1]], ...labels[1]), g, a]);
    }
    const best = pick(cands);
    if (!best) continue;
    const [, g, a] = best;
    ms.forEach((m, i) => {
      const bid = num(m.yes_bid_dollars), ask = num(m.yes_ask_dollars);
      g.legs[`${g.teams[a[i]].abbr}|kalshi`] = {
        venue: "kalshi", id: m.ticker, group: ev.event_ticker, label: m.yes_sub_title,
        url: kalshiUrl(ev.event_ticker), bid: bid || null, ask: ask > 0 && ask < 1 ? ask : null,
        tick: 0.01, fee: { multiplier: mult } };
    });
  }
}

const SERIES_TITLES = { KXNFLGAME: "NFL Game", KXNCAAFGAME: "College Football Game",
  KXNBAGAME: "NBA Game", KXWNBAGAME: "Women's Pro Basketball Game",
  KXNCAAMBGAME: "Men's College Basketball Men's Game", KXMLBGAME: "Professional Baseball Game",
  KXNHLGAME: "NHL Game" };
export function kalshiUrl(ev) {
  const s = ev.split("-")[0];
  const slug = norm(SERIES_TITLES[s] || s).replaceAll(" ", "-");
  return `https://kalshi.com/markets/${s.toLowerCase()}/${slug}/${ev.toLowerCase()}`;
}

export function parseTs(s) {
  if (!s) return null;
  s = String(s).trim().replace(" ", "T").replace("Z", "+00:00");
  if (/[+-]\d{2}$/.test(s)) s += ":00";
  const t = Date.parse(s);
  return Number.isNaN(t) ? null : t / 1000;
}

const jsonish = (x) => (typeof x === "string" ? JSON.parse(x) : x || []);

export function attachPolymarket(games, events) {
  for (const ev of events) {
    const m = (ev.markets || []).find((x) => x.sportsMarketType === "moneyline" && x.clobTokenIds);
    if (!m || m.acceptingOrders === false) continue;
    const names = jsonish(m.outcomes), toks = jsonish(m.clobTokenIds);
    if (names.length !== 2) continue;
    const outs = names.map((n, i) => {
      const t = (ev.teams || []).find((t) => norm(t.alias) === norm(n) || norm(t.name) === norm(n));
      return [t?.name || n, t?.abbreviation || null, toks[i]];
    });
    const st = parseTs(ev.startTime || m.gameStartTime);
    const cands = [];
    for (const g of games) {
      if (st != null && Math.abs(st - g.start) > 6 * 3600) continue;
      const a = assign(g.teams, outs.map((o) => [o[0], o[1]]));
      if (a) cands.push([score(g.teams[a[0]], outs[0][0], outs[0][1]) +
                         score(g.teams[a[1]], outs[1][0], outs[1][1]), g, a]);
    }
    const best = pick(cands);
    if (!best) continue;
    const [, g, a] = best;
    outs.forEach(([lab, , tok], i) => {
      let bid = m.bestBid, ask = m.bestAsk;
      if (i === 1 && bid != null && ask != null) [bid, ask] = [1 - ask, 1 - bid];
      g.legs[`${g.teams[a[i]].abbr}|polymarket`] = {
        venue: "polymarket", id: tok, group: m.conditionId, label: lab,
        url: `https://polymarket.com/event/${ev.slug}`,
        bid: bid ? Math.round(bid * 1e4) / 1e4 : null, ask: ask ? Math.round(ask * 1e4) / 1e4 : null,
        tick: Number(m.orderPriceMinTickSize || 0.01), fee: m.feeSchedule || {} };
    });
  }
}

export const mid = (l) => (l && l.bid && l.ask && l.ask > l.bid ? (l.bid + l.ask) / 2 : null);

export function fair(g, abbr) {
  const mids = Object.entries(g.legs).filter(([k]) => k.startsWith(abbr + "|"))
    .map(([, l]) => mid(l)).filter((x) => x != null);
  return mids.length ? mids.reduce((a, b) => a + b, 0) / mids.length : null;
}

// The video's rule: a resting buy `discount` below the consensus price
export function stinkLevel(g, abbr, discount = 0.3, tick = 0.01) {
  const f = fair(g, abbr);
  return f == null ? null : floorTick(f * (1 - discount), tick);
}

// Best cross-venue lock for a game: [edge, abbr on kalshi, abbr on polymarket]
export function bestLock(g) {
  const [a, b] = g.teams.map((t) => t.abbr);
  let best = null;
  for (const [x, y] of [[a, b], [b, a]]) {
    const k = g.legs[`${x}|kalshi`], p = g.legs[`${y}|polymarket`];
    if (!k || !p) continue;
    const e = lockedEdge(k, p);
    if (e != null && (!best || e > best[0])) best = [e, x, y];
  }
  return best;
}

// One taker order sweeping several resting orders = several trades in the
// same second. Merge them, volume-weighted.
export function groupTrades(rows) {
  const acc = new Map();
  for (const r of rows) {
    const k = `${r.id}|${r.ts}|${r.side}`;
    const a = acc.get(k) || { ...r, n: 0, usd: 0 };
    a.n += r.n;
    a.usd += r.n * r.price;
    acc.set(k, a);
  }
  return [...acc.values()].map((a) => ({ ...a, price: a.usd / a.n }));
}
