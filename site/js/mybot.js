// "Your bot": a paper-trading bot the visitor starts and stops.
//
// A run is just {market, strategy, start time, start price, anchor, stop}:
// everything else is replayed from Binance candles with the same engine as
// the Python bot, so the run keeps going while the page is closed and picks
// up exactly where it was on the next visit. Stop sells any position at the
// live price and freezes the result. Saved in this browser only.

import { run as runEngine, closeOut, STRATEGIES } from "./engine.js";
import { h, legend, lineChart, niceTicks, timeTicks } from "./chart.js";
import { Feed, INTERVAL_MS } from "./feed.js";
import { $, fmtPx, fmtUsd, signed, pct, when, clock, heroNumber, deltaEl, setPill,
         keepTooltip, focusedIn, countdown, describe } from "./ui.js";

const KEY = "tt.mybot.v1";
const MARKETS = { BTC: "BTCUSDT", ETH: "ETHUSDT", SOL: "SOLUSDT" };
const STRATS = {
  scalp: { label: "Scalper", interval: "5m", window: 72 },        // chart: last 6 hours
  trend: { label: "Trend breakout", interval: "4h", window: 42 }, // chart: last 7 days
  ema: { label: "EMA cross", interval: "4h", window: 42 },
};
const BOT = {
  equity: 10, fee: 0.001, slippage: 0.0001, minNotional: 5, live: true,
  risk: { riskPerTrade: 0.02, maxLeverage: 1, maxDailyLoss: 0.03, maxDrawdown: 0.2 },
};
const WARMUP_BARS = 300;

let saved = load();            // { run, history }
let sel = saved.run ? { market: saved.run.market, strat: saved.run.strat } : { market: "BTC", strat: "scalp" };
let feed = null, feedKey = "", dirty = true, charts = {}, chartsDrawnAt = 0, logKey = "", scalpStats = null;

function load() {
  try {
    const v = JSON.parse(localStorage.getItem(KEY) || "null");
    if (v && typeof v === "object") return { run: v.run || null, history: Array.isArray(v.history) ? v.history : [] };
  } catch { /* storage blocked or corrupt: start fresh */ }
  return { run: null, history: [] };
}
function save() {
  try { localStorage.setItem(KEY, JSON.stringify(saved)); } catch { /* works for this visit only */ }
}

const stepOf = (strat) => INTERVAL_MS[STRATS[strat].interval];

// One feed per (market, interval). A run uses its own fixed anchor so the
// indicators, and therefore every decision, replay identically on each visit.
function ensureFeed() {
  const r = saved.run;
  const market = r ? r.market : sel.market, strat = r ? r.strat : sel.strat;
  const interval = STRATS[strat].interval;
  const anchor = r ? r.anchor : Math.floor(Date.now() / stepOf(strat)) * stepOf(strat) - WARMUP_BARS * stepOf(strat);
  const key = `${market}/${interval}/${r ? r.anchor : "preview"}`;
  if (key === feedKey) return;
  if (feed) feed.stop();
  feedKey = key;
  charts = {};
  feed = new Feed({
    markets: { [market]: MARKETS[market] }, interval, anchor,
    onData: () => { dirty = true; },
    onState: (st) => setPill($("mb-feed"), st),
  });
  feed.start().then(() => { dirty = true; render(true); }).catch(() => {
    $("mb-why").textContent = "Couldn't load market data from Binance. Retrying every 5 seconds.";
  });
}

// ---------------- the bot's account ----------------
function compute() {
  const r = saved.run;
  const market = r ? r.market : sel.market, strat = r ? r.strat : sel.strat;
  let c = feed ? feed.store[market] : [];
  if (!c || c.length < 2) return null;
  const step = stepOf(strat);
  if (r && r.stop) c = [...c.filter((x) => x.ts < r.stop.candle.ts), r.stop.candle];
  const startBar = r ? Math.floor(r.startTs / step) * step : c[c.length - 1].ts;
  const res = runEngine(c, new STRATEGIES[strat](), { ...BOT, start: startBar });
  if (r && r.stop) closeOut(res, r.stop.price, r.stop.ts);
  const eq = r && r.stop ? res.cash : (res.equity.length ? res.equity[res.equity.length - 1][1] : BOT.equity);
  // benchmark: 10 USDT of the coin bought at the moment you pressed Start
  let hold = null;
  if (r) {
    const entry = r.startPrice * (1 + BOT.slippage);
    const qty = BOT.equity / entry, fee = qty * entry * BOT.fee;
    const value = (p) => BOT.equity - fee + qty * (p - entry);
    hold = { entry, value, now: value(r.stop ? r.stop.price : c[c.length - 1].c) };
  }
  return { r, market, strat, step, c, res, eq, hold };
}

// ---------------- rendering ----------------
function runLength(ms) {
  const m = Math.floor(ms / 60e3), hrs = Math.floor(m / 60), d = Math.floor(hrs / 24);
  return d ? `${d}d ${hrs % 24}h` : hrs ? `${hrs}h ${m % 60}m` : `${m}m`;
}

function renderControls() {
  const r = saved.run, running = r && !r.stop;
  document.querySelectorAll("[data-mbm]").forEach((b) => {
    b.setAttribute("aria-pressed", String(b.dataset.mbm === sel.market));
    b.disabled = running;
  });
  document.querySelectorAll("[data-mbs]").forEach((b) => {
    b.setAttribute("aria-pressed", String(b.dataset.mbs === sel.strat));
    b.disabled = running;
  });
  const ready = feed && feed.store[sel.market] && feed.store[sel.market].length > 1;
  $("mb-start").disabled = running || !ready;
  $("mb-start").textContent = r && r.stop ? "Start a new run" : "Start bot";
  $("mb-stop").disabled = !running;
  const badge = $("mb-state");
  badge.dataset.state = running ? "running" : r ? "stopped" : "idle";
  badge.lastElementChild.textContent = running ? `Running · ${runLength(Date.now() - r.startTs)}`
    : r ? `Stopped after ${runLength(r.stop.ts - r.startTs)}` : "Not started";
  $("mb-lock").hidden = !running;
}

function renderNote() {
  const note = $("mb-note");
  if (sel.strat !== "scalp") {
    note.textContent = `${STRATS[sel.strat].label} decides once every 4 hours, so it can go days without trading. ` +
      `The public accounts below have run it since 11 October.`;
    return;
  }
  const s = scalpStats && scalpStats[sel.market];
  const abs = (x, d) => `${Math.abs(x * 100).toFixed(d)}%`;
  note.textContent = s
    ? `Warning: in a backtest on ${sel.market} 5-minute candles (${s.start} to ${s.end}) at these same costs, this scalper ` +
      `lost ${abs(s.spot.total_return, 0)} over ${s.spot.trades} trades. The moves it caught averaged ${pct(s.spot.avg_gross, 3)} per trade ` +
      `after slippage, and fees took another ${abs(s.spot.avg_cost, 2)} each. Even at a 0.02% limit-order fee it lost ${abs(s.limit.total_return, 0)}. ` +
      `It is here so you can watch why scalping is hard, not because it works.`
    : "Warning: in backtests this scalper lost money at every fee level tested. It is here so you can watch why scalping is hard, not because it works.";
}

function renderAccount(v) {
  const { r, market, strat, step, c, res, eq, hold } = v;
  const last = c[c.length - 1];
  $("mb-name").textContent = `${STRATS[strat].label} bot · ${market}/USDT · ${STRATS[strat].interval} candles`;
  heroNumber($("mb-equity"), eq);
  if (r) deltaEl($("mb-change"), eq - BOT.equity, BOT.equity, r.stop ? "final result" : "since you started");
  else { $("mb-change").className = "delta"; $("mb-change").textContent = "Press Start to give it 10 USDT of fake money"; }

  $("mb-why").textContent = r && r.stop
    ? `Stopped by you at ${when(r.stop.ts)}${res.trades.some((t) => t.reason === "stopped by you") ? ", selling its position at the live price" : ""}. Press “Start a new run” to try again.`
    : r ? describe(res, c, strat, market, step)
        : "Not started. " + describe(res, c, strat, market, step).replace(/^No position\. /, "");

  const wins = res.trades.filter((t) => t.pnl > 0).length;
  $("mb-f-trades").textContent = String(res.trades.length);
  $("mb-f-win").textContent = res.trades.length ? `${Math.round((wins / res.trades.length) * 100)}%` : "—";
  $("mb-f-fees").textContent = fmtUsd(r ? res.events.reduce((a, e) => a + (e.fee || 0), 0) : 0);
  $("mb-f-time").textContent = r ? runLength((r.stop ? r.stop.ts : Date.now()) - r.startTs) : "—";

  if (hold) {
    heroNumber($("mb-hold"), hold.now, false);
    const hc = hold.now - BOT.equity;
    $("mb-hold-meta").className = "meta " + (hc > 0 ? "up" : hc < 0 ? "down" : "");
    $("mb-hold-meta").textContent = `${signed(hc)} USDT (${pct(hc / BOT.equity)}) · bought ${fmtPx(hold.entry)}`;
  } else {
    $("mb-hold").textContent = "—";
    $("mb-hold-meta").className = "meta";
    $("mb-hold-meta").textContent = "10 USDT bought when you press Start";
  }
  $("mb-price").textContent = fmtPx(r && r.stop ? r.stop.price : last.c);
  $("mb-price-meta").textContent = r && r.stop ? `${market}/USDT when you stopped`
    : `${market}/USDT · Binance · ${feed.lastTick ? Math.max(0, Math.round((Date.now() - feed.lastTick) / 1000)) + " s ago" : "…"}`;
  if (r && r.stop) {
    $("mb-next").textContent = "—";
    $("mb-next-meta").textContent = "the bot is stopped";
  } else {
    const next = last.ts + step;
    $("mb-next").textContent = countdown(next - Date.now());
    $("mb-next-meta").textContent = `at ${clock(next)}, when this ${STRATS[strat].interval} candle closes`;
  }
}

function renderLog(v) {
  const { r, res, market, strat } = v;
  const events = r ? [...res.events].reverse() : [];
  const key = `${feedKey}/${r ? r.startTs : 0}/${r && r.stop ? 1 : 0}/${events.length}/${events.length ? events[0].ts : 0}`;
  if (key === logKey) return;
  logKey = key;
  const table = $("mb-log");
  table.textContent = "";
  $("mb-log-empty").hidden = events.length > 0;
  $("mb-log-empty").textContent = r ? "No trades yet. Most of the time it is waiting for its entry condition." : "Start the bot to see its trades here.";
  table.hidden = events.length === 0;
  if (!events.length) return;
  const head = h("thead"), hr = h("tr");
  ["Time", "Action", "Price", "Value", "Result"].forEach((x) => hr.appendChild(h("th", null, x)));
  head.appendChild(hr);
  const body = h("tbody");
  const why = { target: "target", stop: "stop", "stopped by you": "you stopped it", signal: strat === "scalp" ? "time limit" : "exit rule" };
  for (const e of events) {
    const tr = h("tr");
    tr.appendChild(h("td", null, when(e.ts)));
    tr.appendChild(h("td", null, e.kind === "buy" ? "Buy" : e.kind === "sell" ? `Sell (${why[e.reason] || e.reason})` : "Skipped buy"));
    tr.appendChild(h("td", null, fmtPx(e.price)));
    const value = h("td", null, e.kind === "skip" ? "—" : `${fmtUsd(e.qty * e.price, 2)} USDT`);
    if (e.kind !== "skip") value.title = `${e.qty.toPrecision(4)} ${market}, fee ${fmtUsd(e.fee)} USDT`;
    tr.appendChild(value);
    if (e.kind === "sell") tr.appendChild(h("td", e.pnl >= 0 ? "pos" : "neg", `${signed(e.pnl)} USDT`));
    else if (e.kind === "buy") tr.appendChild(h("td", null, e.take != null ? `target ${fmtPx(e.take)} · stop ${fmtPx(e.stop)}` : `stop ${fmtPx(e.stop)}`));
    else tr.appendChild(h("td", "note-cell", e.reason));
    body.appendChild(tr);
  }
  table.append(head, body);
}

function renderHistory() {
  const list = $("mb-history");
  list.textContent = "";
  $("mb-history-wrap").hidden = saved.history.length === 0;
  for (const p of saved.history) {
    const li = h("li");
    const ch = p.final - BOT.equity;
    li.append(document.createTextNode(`${when(p.startTs)} · ${STRATS[p.strat] ? STRATS[p.strat].label : p.strat} on ${p.market} · ran ${runLength(p.stopTs - p.startTs)} · ${p.trades} trades · `),
      h("b", ch > 0 ? "pos" : ch < 0 ? "neg" : "", `${signed(ch)} USDT (${pct(ch / BOT.equity)})`));
    list.appendChild(li);
  }
}

function renderCharts(v) {
  const { r, market, strat, step, c, res, hold } = v;
  const live = c[c.length - 1];
  // never before the latest candle, even if this device's clock runs behind the exchange's
  const now = Math.max(r && r.stop ? r.stop.ts : Date.now(), live.ts + 1);
  const win = STRATS[strat].window;
  const fromTs = r ? Math.min(live.ts - win * step, Math.floor(r.startTs / step) * step - 6 * step) : live.ts - win * step;
  const closed = c.filter((x) => x.ts >= fromTs && x.ts < live.ts);
  const xs = [...closed.map((x) => x.ts + step), now];
  const px = [...closed.map((x) => x.c), r && r.stop ? r.stop.price : live.c];
  const levels = [];
  const pos = res.position, t = res.trigger;
  if (pos) {
    levels.push({ y: pos.stop, label: `Stop ${fmtPx(pos.stop)}` });
    if (pos.take != null) levels.push({ y: pos.take, label: `Target ${fmtPx(pos.take)}` });
  } else if (t && t.buyBelow != null) levels.push({ y: t.buyBelow, label: `Buys below ~${fmtPx(t.buyBelow)}` });
  else if (t && t.buyAbove != null) levels.push({ y: t.buyAbove, label: `Buys above ${fmtPx(t.buyAbove)}` });
  const near = levels.filter((l) => Math.abs(l.y / px[px.length - 1] - 1) < (strat === "scalp" ? 0.03 : 0.12));
  let lo = Math.min(...px, ...near.map((l) => l.y)), hi = Math.max(...px, ...near.map((l) => l.y));
  const pad = (hi - lo) * 0.08 || px[px.length - 1] * 0.002;
  lo -= pad; hi += pad;
  const markers = r ? res.events.filter((e) => e.kind !== "skip").map((e) => ({
    x: e.ts, y: e.price, kind: e.kind, label: `${e.kind === "buy" ? "Bought" : "Sold"} at ${fmtPx(e.price)}`,
  })).filter((mk) => xs.includes(mk.x)) : [];
  const old = charts.price, foc = focusedIn("mb-price-chart");
  charts.price = lineChart($("mb-price-chart"), {
    label: `${market}/USDT price with your bot's trades`,
    x: xs, xTicks: timeTicks(xs[0], now, 6),
    series: [{ name: `${market}/USDT`, color: "var(--s1)", values: px }],
    domain: [lo, hi], ticks: niceTicks(lo, hi, 4),
    tickFmt: (v) => v >= 1000 ? (v / 1000).toLocaleString("en-US", { maximumFractionDigits: 2 }) + "K" : String(+v.toFixed(2)),
    tipFmt: fmtPx, tipTitle: (x, i) => i === xs.length - 1 ? (r && r.stop ? `Stopped · ${clock(x)}` : `Live · ${clock(x)}`) : `Candle closing ${when(x)}`,
    levels: near, vlines: r ? [{ x: r.startTs, label: "You started" }] : [], markers, livePulse: !(r && r.stop),
    right: 12, left: 52, step: 1, bigStep: 12,
  });
  keepTooltip(old, charts.price, foc);

  const eqWrap = $("mb-eq-card");
  eqWrap.hidden = !r;
  if (!r) { charts.eq = null; return; }
  const rows = res.equity;                                  // [open ts, value at that candle's close]
  const ex = [r.startTs], bot = [BOT.equity], hv = [BOT.equity];   // r.startTs is never before its candle (see start())
  const closeOf = new Map(c.map((x) => [x.ts, x.c]));
  for (const [ts, val] of rows.slice(0, -1)) {
    if (ts + step <= r.startTs) continue;
    ex.push(ts + step); bot.push(val); hv.push(hold.value(closeOf.get(ts)));
  }
  ex.push(Math.max(now, ex[ex.length - 1] + 1));
  bot.push(r.stop ? res.cash : (rows.length ? rows[rows.length - 1][1] : BOT.equity));
  hv.push(hold.now);
  const series = [
    { name: `${STRATS[strat].label} bot`, color: "var(--s1)", values: bot },
    { name: "Buy & hold", color: "var(--s3)", values: hv },
  ];
  let elo = Math.min(...bot, ...hv), ehi = Math.max(...bot, ...hv);
  const mid = (elo + ehi) / 2, half = Math.max(((ehi - elo) / 2) * 1.15, 0.02);
  elo = mid - half; ehi = mid + half;
  const est = niceTicks(elo, ehi, 4);
  const dec = Math.max(2, (String(+(est[1] - est[0]).toPrecision(6)).split(".")[1] || "").length);
  legend($("mb-eq-legend"), series);
  const oldE = charts.eq, focE = focusedIn("mb-equity-chart");
  charts.eq = lineChart($("mb-equity-chart"), {
    label: "Your bot's account value against buy and hold",
    x: ex, xTicks: timeTicks(r.startTs, Math.max(now, r.startTs + 60e3), 5), series,
    domain: [elo, ehi], ticks: est, base: BOT.equity, tickFmt: (v) => v.toFixed(dec),
    tipFmt: (v) => fmtUsd(v) + " USDT",
    tipTitle: (x, i) => i === 0 ? `Started · ${when(x)}` : i === ex.length - 1 ? (r.stop ? `Stopped · ${clock(x)}` : `Live · ${clock(x)}`) : when(x),
    livePulse: !r.stop, right: 12, left: 52, step: 1, bigStep: 12,
  });
  keepTooltip(oldE, charts.eq, focE);
}

function render(force) {
  renderControls();
  if (!feed) return;
  const changed = dirty;
  dirty = false;
  const v = compute();
  if (!v) return;
  renderAccount(v);
  if (changed || force) renderLog(v);
  if (force || (changed && Date.now() - chartsDrawnAt > 2000)) { renderCharts(v); chartsDrawnAt = Date.now(); }
}

// ---------------- start / stop ----------------
function start() {
  const c = feed && feed.store[sel.market];
  if (!c || c.length < 2) return;
  if (saved.run && saved.run.stop) archive();
  saved.run = {
    // a device clock running behind must not date the start before the current candle
    market: sel.market, strat: sel.strat, startTs: Math.max(Date.now(), c[c.length - 1].ts), startPrice: c[c.length - 1].c,
    anchor: feed.anchor, stop: null,
  };
  // the preview feed is already anchored where the run needs it; keep it
  feedKey = `${sel.market}/${STRATS[sel.strat].interval}/${feed.anchor}`;
  save();
  logKey = "";
  dirty = true;
  render(true);
}

function stop() {
  const r = saved.run;
  if (!r || r.stop) return;
  const c = feed.store[r.market];
  const last = c[c.length - 1];
  r.stop = { ts: Math.max(Date.now(), last.ts, r.startTs), price: last.c, candle: { ...last } };
  save();
  dirty = true;
  render(true);
}

function archive() {
  const v = compute();
  const r = saved.run;
  if (v && r && r.stop) {
    saved.history.unshift({ market: r.market, strat: r.strat, startTs: r.startTs, stopTs: r.stop.ts,
                            final: v.eq, trades: v.res.trades.length });
    saved.history = saved.history.slice(0, 5);
  }
  saved.run = null;
  renderHistory();
}

function choose(market, strat) {
  if (saved.run && !saved.run.stop) return;          // locked while running
  if (saved.run && saved.run.stop) { archive(); save(); }
  sel = { market, strat };
  renderNote();
  logKey = "";
  ensureFeed();
  render(true);
}

document.querySelectorAll("[data-mbm]").forEach((b) => b.addEventListener("click", () => choose(b.dataset.mbm, sel.strat)));
document.querySelectorAll("[data-mbs]").forEach((b) => b.addEventListener("click", () => choose(sel.market, b.dataset.mbs)));
$("mb-start").addEventListener("click", start);
$("mb-stop").addEventListener("click", stop);
let rt;
window.addEventListener("resize", () => { clearTimeout(rt); rt = setTimeout(() => render(true), 150); });

fetch("data/scalp.json").then((r) => r.ok ? r.json() : null).then((d) => { scalpStats = d; renderNote(); }).catch(() => {});
renderNote();
renderHistory();
ensureFeed();
setInterval(() => render(false), 1000);
