// Live 10 USDT paper-trading demo.
//
// The account is rebuilt in the browser from public Binance market data since
// a fixed start time, using the same rules as the Python bot (engine.js). It is
// deterministic, so every visitor sees the same account and its history can't
// be edited. Live candle updates stream over a WebSocket, with REST polling as
// a fallback, so the open position is marked to the market as it moves.

import { H4, run, buyAndHold, STRATEGIES } from "./engine.js";
import { legend, lineChart, niceTicks, timeTicks, h } from "./chart.js";
import { Feed } from "./feed.js";
import { $, fmtPx, fmtUsd, signed, pct, qtyFmt, when, clock, heroNumber, deltaEl, setPill,
         keepTooltip, focusedIn, countdown, describe } from "./ui.js";

const START = Date.UTC(2026, 9, 11, 0, 0, 0);          // 11 Oct 2026 00:00 UTC
const ANCHOR = START - 300 * H4;                        // fixed history window: identical indicators for everyone
const DEMO = {
  start: START, equity: 10, fee: 0.001, slippage: 0.0005, minNotional: 5, live: true,
  risk: { riskPerTrade: 0.02, maxLeverage: 1, maxDailyLoss: 0.03, maxDrawdown: 0.2 },
};
const MARKETS = { BTC: "BTCUSDT", ETH: "ETHUSDT", SOL: "SOLUSDT" };
const STRATS = { trend: "Trend breakout", ema: "EMA cross" };

const results = {};          // market -> { trend, ema, hold }
const dirty = new Set();
let sel = { market: "BTC", strat: "trend" };
let chartsDrawnAt = 0;
let charts = {};
const feed = new Feed({
  markets: MARKETS, interval: "4h", anchor: ANCHOR,
  onData: (m) => dirty.add(m),
  onState: (st) => setPill($("live-status"), st),
});
const store = feed.store;

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
function renderAccount() {
  const m = sel.market, c = store[m], R = results[m];
  if (!R || !c.length) return;
  const r = R[sel.strat];
  const eq = equityNow(r), hold = holdNow(R.hold);
  const last = c[c.length - 1];

  $("demo-name").textContent = `${STRATS[sel.strat]} bot · ${m}/USDT`;
  heroNumber($("demo-equity"), eq);
  deltaEl($("demo-change"), eq - DEMO.equity, DEMO.equity);
  $("demo-why").textContent = describe(r, c, sel.strat, m, H4);
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
  $("demo-price-meta").textContent = `${m}/USDT · Binance · ${feed.lastTick ? Math.max(0, Math.round((Date.now() - feed.lastTick) / 1000)) + " s ago" : "…"}`;

  const next = last.ts + H4;
  $("demo-next").textContent = countdown(next - Date.now());
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

function renderCharts() {
  const m = sel.market, c = store[m], R = results[m];
  if (!R || c.length < 2) return;
  const r = R[sel.strat];
  const live = c[c.length - 1];
  const now = Math.max(Date.now(), live.ts + 1);   // device clocks can run behind the exchange

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
  keepTooltip(oldP, charts.price, focP);

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
  keepTooltip(oldE, charts.eq, focE);
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
document.addEventListener("visibilitychange", () => { if (!document.hidden) render(true); });

feed.start()
  .then(() => render(true))
  .catch((e) => {
    $("demo-why").textContent = `Couldn't load market data from Binance (${e.message}). Retrying every 5 seconds.`;
  });
setInterval(() => render(false), 1000);
