// Live 10 USDT paper-trading demo.
//
// The account is rebuilt in the browser from public Binance market data since
// a fixed start time, using the same rules as the Python bot (engine.js). It is
// deterministic, so every visitor sees the same account and its history can't
// be edited. Live candle updates stream over a WebSocket, with REST polling as
// a fallback, so the open position is marked to the market as it moves.

import { H4, run, buyAndHold, STRATEGIES } from "./engine.js";
import { h, legend, lineChart, niceTicks, timeTicks } from "./chart.js";

const START = Date.UTC(2026, 9, 11, 0, 0, 0);          // 11 Oct 2026 00:00 UTC
const ANCHOR = START - 300 * H4;                        // fixed history window: identical indicators for everyone
const DEMO = {
  start: START, equity: 10, fee: 0.001, slippage: 0.0005, minNotional: 5, live: true,
  risk: { riskPerTrade: 0.02, maxLeverage: 1, maxDailyLoss: 0.03, maxDrawdown: 0.2 },
};
const MARKETS = { BTC: "BTCUSDT", ETH: "ETHUSDT", SOL: "SOLUSDT" };
const STRATS = { trend: "Trend breakout", ema: "EMA cross" };
const REST = "https://data-api.binance.vision/api/v3/klines";
const WS_URL = "wss://data-stream.binance.vision/stream?streams=" +
  Object.values(MARKETS).map((s) => s.toLowerCase() + "@kline_4h").join("/");
const POLL_MS = 5000;

const store = Object.fromEntries(Object.keys(MARKETS).map((m) => [m, []]));
const results = {};          // market -> { trend, ema, hold }
const dirty = new Set();
let sel = { market: "BTC", strat: "trend" };
let feed = "connecting";     // stream | poll | down | connecting
let lastTick = 0, ws = null, wsRetry = 0, pollTimer = null, chartsDrawnAt = 0;
let charts = {};

const $ = (id) => document.getElementById(id);
const fmtPx = (v) => v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const fmtUsd = (v, d = 4) => v.toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
const signed = (v, d = 4) => (v > 0 ? "+" : v < 0 ? "−" : "") + fmtUsd(Math.abs(v), d);
const pct = (x, d = 2) => (x > 0 ? "+" : x < 0 ? "−" : "") + Math.abs(x * 100).toFixed(d) + "%";
const qtyFmt = (q) => q.toPrecision(4);
const when = (ts) => new Date(ts).toLocaleString([], { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
const clock = (ts) => new Date(ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

// ---------------- market data ----------------
function upsert(market, k) {
  const arr = store[market];
  const last = arr[arr.length - 1];
  if (!last || k.ts > last.ts) arr.push(k);
  else if (k.ts === last.ts) arr[arr.length - 1] = k;
  else {
    const i = arr.findIndex((x) => x.ts === k.ts);
    if (i >= 0) arr[i] = k;
  }
  dirty.add(market);
  lastTick = Date.now();
}

async function fetchFrom(market, startTime) {
  for (let from = startTime; ;) {
    const url = `${REST}?symbol=${MARKETS[market]}&interval=4h&limit=1000&startTime=${from}`;
    const r = await fetch(url, { cache: "no-store" });
    if (!r.ok) throw new Error(`Binance ${r.status}`);
    const rows = await r.json();
    for (const x of rows) upsert(market, { ts: x[0], o: +x[1], h: +x[2], l: +x[3], c: +x[4] });
    if (rows.length < 1000) return;
    from = rows[rows.length - 1][0] + H4;
  }
}

// Load history the first time; afterwards fetch only from the latest candle on.
async function catchUp() {
  await Promise.all(Object.keys(MARKETS).map((m) => {
    const arr = store[m];
    return fetchFrom(m, arr.length ? arr[arr.length - 1].ts : ANCHOR);
  }));
}

function setFeed(state) {
  feed = state;
  const pill = $("live-status");
  pill.dataset.state = state;
  pill.lastElementChild.textContent = {
    stream: "Live · streaming prices",
    poll: "Live · updating every 5 s",
    down: "Can't reach market data · retrying",
    connecting: "Connecting…",
  }[state];
}

function startPolling() {
  if (pollTimer) return;
  pollTimer = setInterval(async () => {
    if (document.hidden) return;
    try { await catchUp(); if (feed !== "stream") setFeed("poll"); }
    catch { if (feed !== "stream") setFeed("down"); }
  }, POLL_MS);
}
function stopPolling() { clearInterval(pollTimer); pollTimer = null; }

function connect() {
  if (!("WebSocket" in window)) { startPolling(); return; }
  try { ws = new WebSocket(WS_URL); } catch { startPolling(); return; }
  ws.onopen = () => { wsRetry = 0; setFeed("stream"); stopPolling(); catchUp().catch(() => {}); };
  ws.onmessage = (ev) => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch { return; }
    const k = msg && msg.data && msg.data.k;
    const market = k && Object.keys(MARKETS).find((m) => MARKETS[m] === k.s);
    if (!market) return;
    upsert(market, { ts: k.t, o: +k.o, h: +k.h, l: +k.l, c: +k.c });
  };
  ws.onclose = () => {
    ws = null;
    if (feed === "stream") setFeed("poll");
    startPolling();
    wsRetry = Math.min(wsRetry + 1, 6);
    setTimeout(connect, 5000 * 2 ** (wsRetry - 1));   // 5 s, 10 s … ~2.5 min
  };
  ws.onerror = () => { try { ws.close(); } catch { /* closing anyway */ } };
}

// ---------------- computing the accounts ----------------
function compute() {
  for (const m of dirty) {
    const c = store[m];
    if (!c.length) continue;
    results[m] = {
      hold: buyAndHold(c, DEMO),
      trend: run(c, new STRATEGIES.trend(), DEMO),
      ema: run(c, new STRATEGIES.ema(), DEMO),
    };
  }
  dirty.clear();
}

const equityNow = (r) => r.equity.length ? r.equity[r.equity.length - 1][1] : DEMO.equity;
const holdNow = (hd) => hd && hd.curve.length ? hd.curve[hd.curve.length - 1][1] : DEMO.equity;

// ---------------- rendering ----------------
// 10.0371 -> "10.03" with the last two digits smaller: a 10 USDT account moves in fractions of a cent
function heroNumber(el, v, unit = true) {
  const s = fmtUsd(v, 4);
  el.textContent = "";
  el.append(document.createTextNode(s.slice(0, -2)), h("small", "dim", s.slice(-2)));
  if (unit) el.append(h("small", null, " USDT"));
}

function deltaEl(el, change, base) {
  el.className = "delta " + (change > 0 ? "up" : change < 0 ? "down" : "");
  el.textContent = `${signed(change)} USDT (${pct(change / base)}) since start`;
}

function whyText(r, c, market) {
  const price = c[c.length - 1].c;
  const t = r.trigger;
  const pos = r.position;
  if (r.halted) return `Stopped for good: ${r.halted}. A real bot would now wait for you to review it.`;
  if (pos) {
    const parts = [`Holding ${qtyFmt(pos.qty)} ${market}, bought at ${fmtPx(pos.entry)}.`,
      `Stop at ${fmtPx(pos.stop)}, ${Math.abs((pos.stop / price - 1) * 100).toFixed(1)}% below the live price, checked on every price update.`];
    if (t && t.sellBelow != null) parts.push(`It also sells if this 4-hour candle closes below ${fmtPx(t.sellBelow)}.`);
    return parts.join(" ");
  }
  if (!t || t.buyAbove == null) return "Warming up its indicators.";
  const gap = t.buyAbove / price - 1;
  const away = `${Math.abs(gap * 100).toFixed(1)}% ${gap > 0 ? "above" : "below"} the live price`;
  if (sel.strat === "ema") {
    const S = r.strategy, i = c.length - 2;                  // last closed candle
    const f = S.f[i], sl = S.s[i];
    const spread = `the 20-candle average is ${Math.abs((f / sl - 1) * 100).toFixed(2)}% ${f < sl ? "below" : "above"} the 100-candle average`;
    return `No position. It buys when the 20-candle average closes above the 100-candle average; right now ${spread}. ` +
      (gap > 0 ? `To cross on this candle alone it would need to close above ${fmtPx(t.buyAbove)}, ${away}, so it is more likely to happen gradually.`
               : `The price is high enough that it crosses if this candle closes above ${fmtPx(t.buyAbove)}.`);
  }
  if (gap > 0)
    return `No position. It buys if a 4-hour candle closes above ${fmtPx(t.buyAbove)}, a new 55-candle high, which is ${away}.`;
  return `No position. The price is above the 55-candle high of ${fmtPx(t.buyAbove)}, so it buys when this candle closes unless the price falls back below it.`;
}

function renderAccount() {
  const m = sel.market, c = store[m], R = results[m];
  if (!R || !c.length) return;
  const r = R[sel.strat];
  const eq = equityNow(r), hold = holdNow(R.hold);
  const last = c[c.length - 1];

  $("demo-name").textContent = `${STRATS[sel.strat]} bot · ${m}/USDT`;
  heroNumber($("demo-equity"), eq);
  deltaEl($("demo-change"), eq - DEMO.equity, DEMO.equity);
  $("demo-why").textContent = whyText(r, c, m);
  const wins = r.trades.filter((t) => t.pnl > 0).length;
  let peak = DEMO.equity, worst = 0;
  for (const [, v] of r.equity) { peak = Math.max(peak, v); worst = Math.max(worst, 1 - v / peak); }
  $("demo-f-trades").textContent = String(r.trades.length);
  $("demo-f-win").textContent = r.trades.length ? `${Math.round((wins / r.trades.length) * 100)}%` : "—";
  $("demo-f-fees").textContent = fmtUsd(r.events.reduce((a, e) => a + (e.fee || 0), 0));
  $("demo-f-dd").textContent = `${(worst * 100).toFixed(2)}%`;

  heroNumber($("demo-hold"), hold, false);
  const hc = hold - DEMO.equity;
  $("demo-hold-meta").className = "meta " + (hc > 0 ? "up" : hc < 0 ? "down" : "");
  $("demo-hold-meta").textContent = `${signed(hc)} USDT (${pct(hc / DEMO.equity)}) · bought ${R.hold ? fmtPx(R.hold.entry) : "—"}`;

  $("demo-price").textContent = fmtPx(last.c);
  $("demo-price-meta").textContent = `${m}/USDT · Binance · ${lastTick ? Math.max(0, Math.round((Date.now() - lastTick) / 1000)) + " s ago" : "…"}`;

  const next = last.ts + H4;
  const left = Math.max(0, next - Date.now());
  const hh = Math.floor(left / 3600e3), mm = Math.floor((left % 3600e3) / 60e3), ss = Math.floor((left % 60e3) / 1000);
  $("demo-next").textContent = hh ? `${hh}h ${String(mm).padStart(2, "0")}m` : `${mm}m ${String(ss).padStart(2, "0")}s`;
  $("demo-next-meta").textContent = `at ${clock(next)} your time, when this 4-hour candle closes`;
}

let logKey = "";
function renderLog() {
  const r = results[sel.market][sel.strat];
  const key = `${sel.market}/${sel.strat}/${r.events.length}/${r.events.length ? r.events[r.events.length - 1].ts : 0}`;
  if (key === logKey) return;
  logKey = key;
  const table = $("demo-log");
  table.textContent = "";
  const events = [...r.events].reverse();
  $("demo-log-empty").hidden = events.length > 0;
  table.hidden = events.length === 0;
  if (!events.length) return;
  const head = h("thead"), hr = h("tr");
  ["Time", "Action", "Price", "Value", "Result"].forEach((x) => hr.appendChild(h("th", null, x)));
  head.appendChild(hr);
  table.appendChild(head);
  const body = h("tbody");
  for (const e of events) {
    const tr = h("tr");
    tr.appendChild(h("td", null, when(e.ts)));
    const label = e.kind === "buy" ? "Buy" : e.kind === "sell" ? `Sell (${e.reason === "stop" ? "stop" : "exit rule"})` : "Skipped buy";
    tr.appendChild(h("td", null, label));
    tr.appendChild(h("td", null, fmtPx(e.price)));
    const value = h("td", null, e.kind === "skip" ? "—" : `${fmtUsd(e.qty * e.price, 2)} USDT`);
    if (e.kind !== "skip") value.title = `${qtyFmt(e.qty)} ${sel.market}, fee ${fmtUsd(e.fee)} USDT`;
    tr.appendChild(value);
    if (e.kind === "sell") tr.appendChild(h("td", e.pnl >= 0 ? "pos" : "neg", `${signed(e.pnl)} USDT`));
    else if (e.kind === "buy") tr.appendChild(h("td", null, `stop ${fmtPx(e.stop)}`));
    else tr.appendChild(h("td", "note-cell", e.reason));
    body.appendChild(tr);
  }
  table.appendChild(body);
}

const allRows = new Map();   // "BTC/trend" -> cells
function buildAll() {
  const table = $("demo-all");
  const head = h("thead"), hr = h("tr");
  ["Account", "Value", "Change", "Now"].forEach((x) => hr.appendChild(h("th", null, x)));
  head.appendChild(hr);
  const body = h("tbody");
  for (const m of Object.keys(MARKETS)) {
    for (const key of ["trend", "ema", "hold"]) {
      const tr = h("tr"), td = h("td");
      const name = `${m} · ${key === "hold" ? "Buy & hold" : STRATS[key]}`;
      if (key === "hold") td.textContent = name;
      else {
        const b = h("button", "rowlink", name);
        b.type = "button";
        b.addEventListener("click", () => select(m, key));
        td.appendChild(b);
      }
      const value = h("td"), change = h("td"), status = h("td");
      tr.append(td, value, change, status);
      body.appendChild(tr);
      allRows.set(`${m}/${key}`, { tr, value, change, status });
    }
  }
  table.append(head, body);
}

function renderAll() {
  if (!allRows.size) buildAll();
  for (const [id, row] of allRows) {
    const [m, key] = id.split("/");
    const R = results[m];
    if (!R) continue;
    const r = R[key];
    const v = key === "hold" ? holdNow(R.hold) : equityNow(r);
    const ch = v - DEMO.equity;
    row.tr.className = m === sel.market && key === sel.strat ? "sel" : "";
    row.value.textContent = fmtUsd(v, 4);
    row.change.textContent = pct(ch / DEMO.equity);
    row.change.className = ch > 0 ? "pos" : ch < 0 ? "neg" : "";
    row.status.textContent = key === "hold" ? "Holding" : r.position ? "Holding" : r.halted ? "Stopped" : "Waiting";
  }
}

// A redraw replaces the SVG: carry over keyboard focus and an open readout.
function keepTooltip(old, fresh, root, hadFocus) {
  if (hadFocus) fresh.svg.focus({ preventScroll: true });
  if (old && (old.isOpen() || hadFocus)) fresh.show(old.index());
}
const focusedIn = (id) => $(id).contains(document.activeElement);

function renderCharts() {
  const m = sel.market, c = store[m], R = results[m];
  if (!R || c.length < 2) return;
  const r = R[sel.strat];
  const now = Date.now();
  const live = c[c.length - 1];

  // price, last 7 days: closed candles at their close time, then the live price now
  const from = live.ts - 42 * H4;
  const closed = c.filter((x) => x.ts >= from && x.ts < live.ts);
  const xs = [...closed.map((x) => x.ts + H4), now];
  const px = [...closed.map((x) => x.c), live.c];
  const levels = [];
  if (r.position) levels.push({ y: r.position.stop, label: `Stop ${fmtPx(r.position.stop)}` });
  if (r.trigger && r.trigger.buyAbove != null && !r.position) levels.push({ y: r.trigger.buyAbove, label: `Buys above ${fmtPx(r.trigger.buyAbove)}` });
  if (r.trigger && r.trigger.sellBelow != null && r.position) levels.push({ y: r.trigger.sellBelow, label: `Exit below ${fmtPx(r.trigger.sellBelow)}` });
  const near = levels.filter((l) => Math.abs(l.y / live.c - 1) < 0.12);
  let lo = Math.min(...px, ...near.map((l) => l.y)), hi = Math.max(...px, ...near.map((l) => l.y));
  const pad = (hi - lo) * 0.08 || live.c * 0.005;
  lo -= pad; hi += pad;
  const ticks = niceTicks(lo, hi, 4);
  const markers = r.events.filter((e) => e.kind !== "skip").map((e) => ({
    x: e.ts, y: e.price, kind: e.kind,
    label: `${e.kind === "buy" ? "Bought" : "Sold"} at ${fmtPx(e.price)}`,
  })).filter((mk) => xs.includes(mk.x));
  const priceSeries = [{ name: `${m}/USDT`, color: "var(--s1)", values: px }];
  const oldP = charts.price, focP = focusedIn("demo-price-chart");
  charts.price = lineChart($("demo-price-chart"), {
    label: `${m}/USDT price over the last 7 days with the bot's trades and trigger levels`,
    x: xs, xTicks: timeTicks(xs[0], now, 6), series: priceSeries,
    domain: [lo, hi], ticks, tickFmt: (v) => v >= 1000 ? (v / 1000).toLocaleString("en-US", { maximumFractionDigits: 2 }) + "K" : String(+v.toFixed(2)),
    tipFmt: fmtPx, tipTitle: (x, i) => i === xs.length - 1 ? `Live · ${clock(x)}` : `Candle closing ${when(x)}`,
    levels: near, vlines: [{ x: START, label: "Demo start" }], markers, livePulse: true,
    right: 12, left: 52, step: 1, bigStep: 6,
  });
  keepTooltip(oldP, charts.price, $("demo-price-chart"), focP);

  // account value since the start: bot vs buy & hold
  const eqPts = r.equity;                                   // [open ts, value at that candle's close]
  const holdPts = R.hold ? R.hold.curve : [];
  const ex = [START, ...eqPts.slice(0, -1).map((p) => p[0] + H4), now];
  const bot = [DEMO.equity, ...eqPts.slice(0, -1).map((p) => p[1]), equityNow(r)];
  const holdMap = new Map(holdPts.map((p) => [p[0] + H4, p[1]]));
  const holdVals = ex.map((x, i) => i === 0 ? DEMO.equity : i === ex.length - 1 ? holdNow(R.hold) : holdMap.get(x) ?? null);
  const series = [
    { name: `${STRATS[sel.strat]} bot`, color: "var(--s1)", values: bot },
    { name: "Buy & hold", color: "var(--s3)", values: holdVals },
  ];
  const all = [...bot, ...holdVals.filter((v) => v != null)];
  let elo = Math.min(...all), ehi = Math.max(...all);
  const mid = (elo + ehi) / 2, half = Math.max((ehi - elo) / 2 * 1.15, 0.05);
  elo = mid - half; ehi = mid + half;
  legend($("demo-eq-legend"), series);
  const oldE = charts.eq, focE = focusedIn("demo-equity-chart");
  const est = niceTicks(elo, ehi, 4);
  const dec = Math.max(2, (String(+(est[1] - est[0]).toPrecision(6)).split(".")[1] || "").length);
  charts.eq = lineChart($("demo-equity-chart"), {
    label: "Demo account value since the start, bot versus buy and hold",
    x: ex, xTicks: timeTicks(START, Math.max(now, START + 3600e3), 5), series,
    domain: [elo, ehi], ticks: est, base: DEMO.equity, tickFmt: (v) => v.toFixed(dec),
    tipFmt: (v) => fmtUsd(v) + " USDT", tipTitle: (x, i) => i === 0 ? `Start · ${when(x)}` : i === ex.length - 1 ? `Live · ${clock(x)}` : when(x),
    livePulse: true, right: 12, left: 52, step: 1, bigStep: 6,
  });
  keepTooltip(oldE, charts.eq, $("demo-equity-chart"), focE);
  chartsDrawnAt = Date.now();
}

function render(force) {
  const changed = dirty.size > 0;
  if (changed) compute();
  if (!results[sel.market]) return;
  renderAccount();
  if (changed || force) {
    renderLog();
    renderAll();
  }
  // redraw charts on new data, at most every 2 s (keeps hover readouts steady)
  if (force || (changed && Date.now() - chartsDrawnAt > 2000)) renderCharts();
}

function select(market, strat) {
  sel = { market, strat };
  document.querySelectorAll("[data-dm]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.dm === market)));
  document.querySelectorAll("[data-ds]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.ds === strat)));
  if (results[market]) render(true);
}

// ---------------- start ----------------
document.querySelectorAll("[data-dm]").forEach((b) => b.addEventListener("click", () => select(b.dataset.dm, sel.strat)));
document.querySelectorAll("[data-ds]").forEach((b) => b.addEventListener("click", () => select(sel.market, b.dataset.ds)));
let rt;
window.addEventListener("resize", () => { clearTimeout(rt); rt = setTimeout(() => render(true), 150); });
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) catchUp().then(() => render(true)).catch(() => {});
});

setFeed("connecting");
catchUp()
  .then(() => { render(true); connect(); })
  .catch((e) => {
    setFeed("down");
    $("demo-why").textContent = `Couldn't load market data from Binance (${e.message}). Retrying every 5 seconds.`;
    startPolling();
    connect();
  });
setInterval(() => {
  // watchdog: if the stream goes quiet, fetch over REST
  if (feed === "stream" && Date.now() - lastTick > 30000 && !document.hidden) catchUp().catch(() => {});
  render(false);
}, 1000);
