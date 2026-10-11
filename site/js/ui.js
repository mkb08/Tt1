// Formatting and small display helpers shared by the live demo panels.
import { h } from "./chart.js";

export const $ = (id) => document.getElementById(id);
export const fmtPx = (v) => v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
export const fmtUsd = (v, d = 4) => v.toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
export const signed = (v, d = 4) => (v > 0 ? "+" : v < 0 ? "−" : "") + fmtUsd(Math.abs(v), d);
export const pct = (x, d = 2) => (x > 0 ? "+" : x < 0 ? "−" : "") + Math.abs(x * 100).toFixed(d) + "%";
export const qtyFmt = (q) => q.toPrecision(4);
export const when = (ts) => new Date(ts).toLocaleString([], { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
export const clock = (ts) => new Date(ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

// 10.0371 -> "10.03" + muted "71": a 10 USDT account moves in fractions of a cent
export function heroNumber(el, v, unit = true) {
  const s = fmtUsd(v, 4);
  el.textContent = "";
  el.append(document.createTextNode(s.slice(0, -2)), h("small", "dim", s.slice(-2)));
  if (unit) el.append(h("small", null, " USDT"));
}

export function deltaEl(el, change, base, suffix = "since start") {
  el.className = "delta " + (change > 0 ? "up" : change < 0 ? "down" : "");
  el.textContent = `${signed(change)} USDT (${pct(change / base)}) ${suffix}`;
}

export const FEED_LABELS = {
  stream: "Live · streaming prices",
  poll: "Live · updating every 5 s",
  down: "Can't reach market data · retrying",
  connecting: "Connecting…",
};

export function setPill(pill, state) {
  pill.dataset.state = state;
  pill.lastElementChild.textContent = FEED_LABELS[state];
}

// A redraw replaces the SVG: carry over keyboard focus and an open readout.
export function keepTooltip(old, fresh, hadFocus) {
  if (hadFocus) fresh.svg.focus({ preventScroll: true });
  if (old && (old.isOpen() || hadFocus)) fresh.show(old.index());
}
export const focusedIn = (id) => $(id).contains(document.activeElement);

export function countdown(ms) {
  const left = Math.max(0, ms);
  const hh = Math.floor(left / 3600e3), mm = Math.floor((left % 3600e3) / 60e3), ss = Math.floor((left % 60e3) / 1000);
  return hh ? `${hh}h ${String(mm).padStart(2, "0")}m` : `${mm}m ${String(ss).padStart(2, "0")}s`;
}

const pctAbs = (x, d = 1) => `${Math.abs(x * 100).toFixed(d)}%`;

// Plain-language status for a bot: what it holds, or what it's waiting for.
// r: engine result; c: candles (last one forming); step: candle length in ms.
export function describe(r, c, strat, market, step) {
  const price = c[c.length - 1].c;
  const t = r.trigger, pos = r.position, S = r.strategy;
  if (r.halted) return `Stopped for good: ${r.halted}. A real bot would now wait for you to review it.`;
  if (pos) {
    const parts = [`Holding ${qtyFmt(pos.qty)} ${market}, bought at ${fmtPx(pos.entry)}.`];
    if (pos.take != null) {
      parts.push(`Target ${fmtPx(pos.take)} (${pctAbs(pos.take / price - 1, 2)} above the live price), ` +
        `stop ${fmtPx(pos.stop)} (${pctAbs(pos.stop / price - 1, 2)} below), both checked on every price update.`);
      if (S && S.maxBars) parts.push(`If neither is hit, it sells at ${clock(pos.entryTs + (S.maxBars + 1) * step)}.`);
    } else {
      parts.push(`Stop at ${fmtPx(pos.stop)}, ${pctAbs(pos.stop / price - 1)} below the live price, checked on every price update.`);
      if (t && t.sellBelow != null) parts.push(`It also sells if this candle closes below ${fmtPx(t.sellBelow)}.`);
    }
    return parts.join(" ");
  }
  if (!t) return "Warming up its indicators.";
  if (strat === "scalp") {
    if (price <= t.trendAbove)
      return `No position. The price is below its 100-candle trend line (${fmtPx(t.trendAbove)}), so the scalper isn't buying dips right now.`;
    return `No position. Waiting for a dip: it buys if a 5-minute candle closes below about ${fmtPx(t.buyBelow)} ` +
      `(the lower Bollinger band, ${pctAbs(t.buyBelow / price - 1, 2)} below the live price) while staying above its trend line at ${fmtPx(t.trendAbove)}.`;
  }
  if (t.buyAbove == null) return "Warming up its indicators.";
  const gap = t.buyAbove / price - 1;
  const away = `${pctAbs(gap)} ${gap > 0 ? "above" : "below"} the live price`;
  if (strat === "ema") {
    const i = c.length - 2;                                    // last closed candle
    const f = S.f[i], sl = S.s[i];
    const spread = `the 20-candle average is ${pctAbs(f / sl - 1, 2)} ${f < sl ? "below" : "above"} the 100-candle average`;
    return `No position. It buys when the 20-candle average closes above the 100-candle average; right now ${spread}. ` +
      (gap > 0 ? `To cross on this candle alone it would need to close above ${fmtPx(t.buyAbove)}, ${away}, so it is more likely to happen gradually.`
               : `The price is high enough that it crosses if this candle closes above ${fmtPx(t.buyAbove)}.`);
  }
  if (gap > 0) return `No position. It buys if a candle closes above ${fmtPx(t.buyAbove)}, a new 55-candle high, which is ${away}.`;
  return `No position. The price is above the 55-candle high of ${fmtPx(t.buyAbove)}, so it buys when this candle closes unless the price falls back below it.`;
}
