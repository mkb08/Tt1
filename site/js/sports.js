// Sports page: today's games on Kalshi and Polymarket, where the video's
// stink bids would sit, the cross-venue lock after fees, a whale feed, and
// what the backtest found. Read-only: it never places an order.
import { attachKalshi, attachPolymarket, bestLock, bidsAllowed, fair, groupTrades, mid,
         parseEspn, phase, stinkLevel } from "./sports-core.js";

const $ = (id) => document.getElementById(id);
const LEAGUES = {
  nfl: { name: "NFL", espn: "football/nfl", kalshi: "KXNFLGAME", poly: "nfl" },
  nba: { name: "NBA", espn: "basketball/nba", kalshi: "KXNBAGAME", poly: "nba" },
  mlb: { name: "MLB", espn: "baseball/mlb", kalshi: "KXMLBGAME", poly: "mlb" },
  nhl: { name: "NHL", espn: "hockey/nhl", kalshi: "KXNHLGAME", poly: "nhl" },
  ncaaf: { name: "College FB", espn: "football/college-football", kalshi: "KXNCAAFGAME",
           poly: "cfb", extra: "&groups=80&limit=300" },
};
const POLY_SERIES = { nfl: 12185, nba: 10345, mlb: 3, nhl: 10346, cfb: 12756 };
const DISCOUNT = 0.3;
const state = { league: null, games: [], whales: [], seen: new Set(), minUsd: 1000, timer: null };

const getJson = async (url) => {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${r.status} ${url.split("?")[0]}`);
  return r.json();
};
const kalshi = (path, params = {}) =>
  getJson(`/api/kalshi?${new URLSearchParams({ path, ...params })}`);
const cents = (x) => (x == null ? "–" : (x * 100).toFixed(0) + "¢");
const quote = (l) => (l && l.bid != null && l.ask != null ? `${cents(l.bid)}/${cents(l.ask)}` : "–");
const hhmm = (ts) => new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
const day = (ts) => new Date(ts * 1000).toLocaleDateString([], { weekday: "short" });
const el = (tag, cls, text) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
};

let polySeries = null;
async function polySeriesId(code) {
  if (!polySeries) {
    polySeries = { ...POLY_SERIES };
    try {
      for (const s of await getJson("https://gamma-api.polymarket.com/sports")) {
        if (s.series) polySeries[s.sport] = Number(s.series);
      }
    } catch { /* keep the known ids */ }
  }
  return polySeries[code];
}

async function loadGames(league) {
  const L = LEAGUES[league], now = Date.now() / 1000;
  const days = new Set([-8, 6, 20, 34].map((h) =>
    new Date((now + (h - 5) * 3600) * 1000).toISOString().slice(0, 10).replaceAll("-", "")));
  const boards = await Promise.all([...days].map((d) =>
    getJson(`https://site.api.espn.com/apis/site/v2/sports/${L.espn}/scoreboard?dates=${d}${L.extra || ""}`)
      .catch(() => ({ events: [] }))));
  const byId = new Map();
  for (const b of boards) for (const e of b.events || []) {
    const g = parseEspn(league, e);
    if (g.teams.length === 2 && g.start >= now - 8 * 3600 && g.start <= now + 36 * 3600) byId.set(g.id, g);
  }
  const games = [...byId.values()].sort((a, b) => a.start - b.start);
  if (!games.length) return games;
  const iso = (t) => new Date(t * 1000).toISOString().replace(/\.\d+Z$/, "Z");
  const [kEvents, kSeries, pEvents] = await Promise.all([
    kalshi("events", { series_ticker: L.kalshi, status: "open", with_nested_markets: "true", limit: 200 })
      .then((d) => d.events || []).catch(() => null),
    kalshi(`series/${L.kalshi}`).then((d) => Number(d.series?.fee_multiplier) || 1).catch(() => 1),
    polySeriesId(L.poly).then((sid) => getJson(`https://gamma-api.polymarket.com/events?${new URLSearchParams({
      series_id: sid, closed: "false", limit: 500, end_date_min: iso(now - 9 * 3600),
      end_date_max: iso(now + 43 * 3600) })}`)).catch(() => null),
  ]);
  if (kEvents) attachKalshi(games, kEvents, kSeries);
  if (pEvents) attachPolymarket(games, pEvents);
  games.sources = { kalshi: !!kEvents, polymarket: !!pEvents };
  return games;
}

function teamRow(g, t) {
  const tr = el("tr");
  const name = el("td", "team");
  name.append(el("b", null, t.short), el("span", "muted", " " + t.abbr));
  tr.append(name);
  for (const v of ["kalshi", "polymarket"]) {
    const l = g.legs[`${t.abbr}|${v}`];
    const td = el("td");
    if (l) {
      const a = el("a", null, quote(l));
      a.href = l.url;
      a.target = "_blank";
      a.rel = "noopener";
      td.append(a);
    } else td.textContent = "–";
    tr.append(td);
  }
  const f = fair(g, t.abbr);
  const lvl = stinkLevel(g, t.abbr, DISCOUNT);
  tr.append(el("td", null, f == null ? "–" : cents(f)));
  const s = el("td", "stink", lvl == null || f < 0.1 || f > 0.9 ? "–" : cents(lvl));
  if (lvl != null && !bidsAllowed(g)) s.classList.add("off");
  tr.append(s);
  return tr;
}

function gameCard(g) {
  const card = el("article", "card game");
  const head = el("div", "ghead");
  const live = g.state === "in";
  head.append(el("span", "when", g.state === "pre" ? `${day(g.start)} ${hhmm(g.start)}` : phase(g)));
  const up = bidsAllowed(g);
  const badge = el("span", "badge " + (up ? "on" : "off"),
    g.state === "post" ? "final" : up ? (live ? "bids up · 1st half" : "bids up") : "bids cancelled");
  head.append(badge);
  card.append(head);
  const tbl = el("table", "compact board");
  const thead = el("thead");
  const hr = el("tr");
  for (const [long, short] of [["Team", "Team"], ["Kalshi", "Kalshi"], ["Polymarket", "Poly"],
                               ["Price", "Price"], ["Stink bid", "Stink"]]) {
    const th = el("th");
    th.append(el("span", "long", long), el("span", "short", short));
    hr.append(th);
  }
  thead.append(hr);
  const tb = el("tbody");
  for (const t of g.teams) tb.append(teamRow(g, t));
  tbl.append(thead, tb);
  const wrap = el("div", "table-wrap");
  wrap.append(tbl);
  card.append(wrap);
  const lock = bestLock(g);
  const foot = el("p", "lockline");
  if (lock) {
    const [e, x, y] = lock;
    foot.innerHTML = "";
    foot.append(document.createTextNode(`Cross-venue: ${x} on Kalshi + ${y} on Polymarket `));
    const b = el("b", e > 0 ? "pos" : "neg", `${e >= 0 ? "+" : "−"}${Math.abs(e * 100).toFixed(1)}¢`);
    foot.append(b, document.createTextNode(e > 0 ? " per $1 after fees: an arbitrage" :
      " per $1 after fees (no arbitrage)"));
  } else {
    foot.textContent = "Listed on one venue only: no cross-venue hedge possible.";
  }
  card.append(foot);
  return card;
}

function renderGames() {
  const box = $("games-list");
  box.textContent = "";
  const games = state.games;
  const listed = games.filter((g) => Object.keys(g.legs).length);
  $("games-count").textContent = games.length
    ? `${games.length} games from 8 h ago to 36 h ahead · ${listed.length} listed on a venue`
    : "";
  if (!games.length) {
    box.append(el("p", "empty", `No ${LEAGUES[state.league].name} games in the next 36 hours.`));
    return;
  }
  const src = games.sources || {};
  const missing = ["kalshi", "polymarket"].filter((v) => src[v] === false);
  $("games-warn").textContent = missing.length
    ? `Couldn't reach ${missing.join(" and ")}; showing what loaded.` : "";
  const order = { in: 0, pre: 1, post: 2 };
  for (const g of [...games].sort((a, b) => order[a.state] - order[b.state] || a.start - b.start)) {
    box.append(gameCard(g));
  }
}

async function scanWhales() {
  const games = state.games.filter((g) => g.state !== "post" && Object.keys(g.legs).length);
  const legs = new Map();
  for (const g of games) for (const [k, l] of Object.entries(g.legs)) legs.set(l.id, { g, abbr: k.split("|")[0], l });
  const since = Math.floor(Date.now() / 60000) * 60 - 15 * 60;  // shared edge cache per minute
  const rows = [];
  const kLegs = [...legs.values()].filter((x) => x.l.venue === "kalshi");
  for (let i = 0; i < kLegs.length; i += 4) {
    await Promise.all(kLegs.slice(i, i + 4).map(async ({ l }) => {
      try {
        const d = await kalshi("markets/trades", { ticker: l.id, min_ts: since, limit: 1000 });
        for (const t of d.trades || []) {
          rows.push({ id: l.id, ts: Math.floor(Date.parse(t.created_time) / 1000),
                      side: t.taker_side === "no" ? "SELL" : "BUY",
                      price: Number(t.yes_price_dollars), n: Number(t.count_fp) });
        }
      } catch { /* skip this market this round */ }
    }));
  }
  const conds = [...new Set([...legs.values()].filter((x) => x.l.venue === "polymarket").map((x) => x.l.group))];
  for (let i = 0; i < conds.length; i += 40) {
    try {
      const ts = await getJson(`https://data-api.polymarket.com/trades?${new URLSearchParams({
        market: conds.slice(i, i + 40).join(","), limit: 500, takerOnly: "true",
        filterType: "CASH", filterAmount: state.minUsd / 5 })}`);
      for (const t of ts) {
        if (legs.has(String(t.asset)) && t.timestamp >= since) {
          rows.push({ id: String(t.asset), ts: t.timestamp, side: t.side, price: Number(t.price),
                      n: Number(t.size) });
        }
      }
    } catch { /* skip */ }
  }
  const now = Date.now() / 1000;
  for (const w of groupTrades(rows)) {
    const key = `${w.id}|${w.ts}|${w.side}`;
    if (state.seen.has(key) || w.usd < state.minUsd || w.price < 0.03 || w.price > 0.97) continue;
    state.seen.add(key);
    const { g, abbr, l } = legs.get(w.id);
    const other = g.legs[`${abbr}|${l.venue === "kalshi" ? "polymarket" : "kalshi"}`];
    const m = mid(other);
    state.whales.push({ ...w, g, abbr, l, other, gap: m != null && now - w.ts < 120 ? w.price - m : null });
  }
  state.whales.sort((a, b) => b.ts - a.ts);
  state.whales = state.whales.slice(0, 25);
  renderWhales();
}

function renderWhales() {
  const tb = $("whale-rows");
  tb.textContent = "";
  $("whale-empty").hidden = state.whales.length > 0;
  for (const w of state.whales) {
    const tr = el("tr");
    tr.append(el("td", null, hhmm(w.ts)));
    const game = el("td", "note-cell");
    const a = el("a", null, `${w.g.teams[0].short} @ ${w.g.teams[1].short}`);
    a.href = w.l.url;
    a.target = "_blank";
    a.rel = "noopener";
    game.append(a, el("span", "muted", ` · ${w.l.venue === "kalshi" ? "Kalshi" : "Polymarket"}`));
    tr.append(game);
    tr.append(el("td", w.side === "SELL" ? "neg" : "pos", `${w.side} ${w.abbr}`));
    tr.append(el("td", null, cents(w.price)));
    tr.append(el("td", null, "$" + Math.round(w.usd).toLocaleString("en-US")));
    tr.append(el("td", null, w.other ? quote(w.other) : "–"));
    const gap = el("td", w.gap == null ? "" : w.gap < -0.015 ? "neg" : "", w.gap == null ? "" :
      `${w.gap >= 0 ? "+" : "−"}${Math.abs(w.gap * 100).toFixed(0)}¢`);
    tr.append(gap);
    tb.append(tr);
  }
}

async function refresh() {
  const pill = $("pill");
  try {
    const league = state.league;
    const games = await loadGames(league);
    if (league !== state.league) return;
    state.games = games;
    renderGames();
    pill.dataset.state = "poll";
    pill.lastElementChild.textContent = `Live · updated ${new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}`;
    await scanWhales();
  } catch (e) {
    pill.dataset.state = "down";
    pill.lastElementChild.textContent = "Can't reach market data · retrying";
  }
}

function setLeague(lg) {
  state.league = lg;
  state.whales = [];
  state.seen = new Set();
  renderWhales();
  for (const b of document.querySelectorAll("#leagues button")) b.setAttribute("aria-pressed", String(b.dataset.lg === lg));
  $("games-list").textContent = "";
  $("games-list").append(el("p", "empty loading", "Loading games…"));
  try { localStorage.setItem("tt-sports-league", lg); } catch { /* private mode */ }
  clearInterval(state.timer);
  refresh();
  state.timer = setInterval(refresh, 20000);
}

const pctS = (x, d = 1) => `${x > 0 ? "+" : x < 0 ? "−" : ""}${Math.abs(x * 100).toFixed(d)}%`;

async function renderStudy() {
  let s;
  try { s = await getJson("data/stink.json"); } catch { return; }
  const p = s.primary.all;
  $("study-verdict").textContent = s.verdict;
  $("t-fills").textContent = p.fills.toLocaleString("en-US");
  $("t-fills-meta").textContent = `of ${p.eligible.toLocaleString("en-US")} bids (${(p.fill_rate * 100).toFixed(0)}%) · ${s.primary.pregame_fills.fills} before kick-off`;
  $("t-win").textContent = `${(p.win_rate * 100).toFixed(0)}%`;
  $("t-win-meta").textContent = `paid ${(p.avg_price * 100).toFixed(0)}¢ on average, so ~${(p.avg_price * 100).toFixed(0)}% was break-even`;
  $("t-roi").textContent = pctS(p.roi_net);
  $("t-roi").className = "value " + (p.roi_net < 0 ? "neg" : "pos");
  $("t-roi-meta").textContent = `95% range ${pctS(p.roi_net_ci95[0])} to ${pctS(p.roi_net_ci95[1])}`;
  const tb = $("study-rows");
  const rows = [["All fills", p], ["Filled before the game", s.primary.pregame_fills],
    ["Filled during play", s.primary.ingame_fills],
    ["Bounced back within 10 min*", s.primary.bounced_fills],
    ["Kept falling*", s.primary.stayed_down_fills],
    ...Object.entries(s.primary.by_league).map(([k, v]) => [LEAGUES[k]?.name || k.toUpperCase(), v])];
  for (const [name, v] of rows) {
    if (!v || !v.fills) continue;
    const tr = el("tr");
    tr.append(el("td", null, name), el("td", null, v.fills.toLocaleString("en-US")),
      el("td", null, `${(v.win_rate * 100).toFixed(0)}%`), el("td", null, cents(v.avg_price)),
      el("td", v.roi_net < 0 ? "neg" : "pos", pctS(v.roi_net)),
      el("td", null, `${pctS(v.roi_net_ci95[0])} to ${pctS(v.roi_net_ci95[1])}`));
    tb.append(tr);
  }
  if (s.hedge) $("study-hedge").textContent = s.hedge_text;
  $("study-period").textContent = s.period_text;
}

document.querySelectorAll("#leagues button").forEach((b) => b.addEventListener("click", () => setLeague(b.dataset.lg)));
document.querySelectorAll("#min-usd button").forEach((b) => b.addEventListener("click", () => {
  state.minUsd = Number(b.dataset.v);
  for (const x of document.querySelectorAll("#min-usd button")) x.setAttribute("aria-pressed", String(x === b));
  state.whales = [];
  state.seen = new Set();
  renderWhales();
  scanWhales();
}));
let saved = null;
try { saved = localStorage.getItem("tt-sports-league"); } catch { /* private mode */ }
setLeague(LEAGUES[saved] ? saved : "nfl");
renderStudy();
