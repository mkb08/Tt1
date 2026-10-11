// Trading engine for the live demo: a line-for-line port of tt/indicators.py,
// tt/strategies.py (long-only), tt/risk.py and the tt/backtest.py loop, so the
// website trades by exactly the same rules as the Python bot. Parity with the
// Python backtester is checked by tests/test_site_engine.py.
//
// Candles are {ts, o, h, l, c} with ts = open time in ms (UTC). With
// `live: true` the last candle may still be forming: pending orders fill at
// its open, stops are checked against its high/low so far, and equity is
// marked to its latest price. No decision is taken until it closes.

export const H4 = 4 * 3600 * 1000;
export const DAY = 86400000;
export const LONG = 1, FLAT = 0;

// ---------- indicators (causal; null during warm-up) ----------
export function ema(values, n) {
  const out = new Array(values.length).fill(null);
  if (values.length < n) return out;
  const k = 2 / (n + 1);
  let e = 0;
  for (let i = 0; i < n; i++) e += values[i];
  e = e / n;
  out[n - 1] = e;
  for (let i = n; i < values.length; i++) {
    e = values[i] * k + e * (1 - k);
    out[i] = e;
  }
  return out;
}

export function atr(candles, n) {
  const tr = candles.map((c, i) => i === 0 ? c.h - c.l
    : Math.max(c.h - c.l, Math.abs(c.h - candles[i - 1].c), Math.abs(c.l - candles[i - 1].c)));
  const out = new Array(candles.length).fill(null);
  if (tr.length < n) return out;
  let a = 0;
  for (let i = 0; i < n; i++) a += tr[i];
  a = a / n;
  out[n - 1] = a;
  for (let i = n; i < tr.length; i++) {
    a = (a * (n - 1) + tr[i]) / n;
    out[i] = a;
  }
  return out;
}

// highest high / lowest low of the n bars *before* i
export function donchian(candles, n) {
  const up = new Array(candles.length).fill(null);
  const lo = new Array(candles.length).fill(null);
  for (let i = n; i < candles.length; i++) {
    let hi = -Infinity, low = Infinity;
    for (let j = i - n; j < i; j++) {
      if (candles[j].h > hi) hi = candles[j].h;
      if (candles[j].l < low) low = candles[j].l;
    }
    up[i] = hi; lo[i] = low;
  }
  return [up, lo];
}

// ---------- strategies ----------
export class TrendBreakout {
  constructor({ entry = 55, exit = 20, atrN = 20, atrMult = 3.0 } = {}) {
    Object.assign(this, { entry, exit, atrN, atrMult });
    this.name = "trend";
    this.warmup = Math.max(entry, exit, atrN) + 1;
  }
  prepare(c) {
    this.c = c.map((x) => x.c);
    [this.up, this.dn] = donchian(c, this.entry);
    [this.xup, this.xdn] = donchian(c, this.exit);
    this.atr = atr(c, this.atrN);
  }
  signal(i, side) {
    const c = this.c[i], a = this.atr[i], up = this.up[i], xdn = this.xdn[i];
    if (a == null || up == null || xdn == null || this.dn[i] == null || this.xup[i] == null) return { side: FLAT, stop: null };
    if (side === LONG) return c < xdn ? { side: FLAT, stop: null } : { side: LONG, stop: c - this.atrMult * a };
    if (c > up) return { side: LONG, stop: c - this.atrMult * a };
    return { side: FLAT, stop: null };
  }
  // What closing price of candle i (still forming) would change the position.
  trigger(i, side) {
    if (side === LONG) return this.xdn[i] == null ? null : { sellBelow: this.xdn[i] };
    return this.up[i] == null ? null : { buyAbove: this.up[i] };
  }
}

export class EmaCross {
  constructor({ fast = 20, slow = 100, atrN = 14, atrMult = 4.0 } = {}) {
    Object.assign(this, { fast, slow, atrN, atrMult });
    this.name = "ema";
    this.warmup = Math.max(fast, slow, atrN) + 1;
  }
  prepare(c) {
    const closes = c.map((x) => x.c);
    this.c = closes;
    this.f = ema(closes, this.fast);
    this.s = ema(closes, this.slow);
    this.atr = atr(c, this.atrN);
  }
  signal(i) {
    const f = this.f[i], s = this.s[i], a = this.atr[i], c = this.c[i];
    if (f == null || s == null || a == null) return { side: FLAT, stop: null };
    if (f > s) return { side: LONG, stop: c - this.atrMult * a };
    return { side: FLAT, stop: null };
  }
  // The fast average crosses the slow one when the close passes this price.
  trigger(i, side) {
    const fp = this.f[i - 1], sp = this.s[i - 1];
    if (fp == null || sp == null) return null;
    const kf = 2 / (this.fast + 1), ks = 2 / (this.slow + 1);
    const level = (sp * (1 - ks) - fp * (1 - kf)) / (kf - ks);
    return side === LONG ? { sellBelow: level } : { buyAbove: level };
  }
}

export const STRATEGIES = { trend: TrendBreakout, ema: EmaCross };

// ---------- risk ----------
export function positionSize(equity, entry, stop, risk) {
  if (equity <= 0 || entry <= 0 || stop == null) return 0;
  const dist = Math.abs(entry - stop);
  if (dist <= 0) return 0;
  return Math.max(Math.min(equity * risk.riskPerTrade / dist, equity * risk.maxLeverage / entry), 0);
}

export class RiskGuard {
  constructor(risk) { this.risk = risk; this.peak = 0; this.day = null; this.dayStart = 0; this.halted = false; this.reason = ""; }
  update(eq, ts) {
    const day = Math.floor(ts / DAY);
    if (day !== this.day) { this.day = day; this.dayStart = eq; }
    this.peak = Math.max(this.peak, eq);
    if (this.peak > 0 && eq <= this.peak * (1 - this.risk.maxDrawdown)) {
      this.halted = true;
      this.reason = `max drawdown hit: ${((1 - eq / this.peak) * 100).toFixed(1)}% below peak`;
    }
  }
  allowEntries(eq) {
    if (this.halted) return [false, this.reason];
    if (this.dayStart > 0 && eq <= this.dayStart * (1 - this.risk.maxDailyLoss))
      return [false, `daily loss limit: ${((1 - eq / this.dayStart) * 100).toFixed(1)}% down today`];
    return [true, ""];
  }
}

// ---------- the trading loop (mirrors tt.backtest.backtest) ----------
export function run(candles, strategy, cfg) {
  const { equity = 1000, fee = 0.001, slippage = 0.0005, minNotional = 0, live = false } = cfg;
  const risk = { riskPerTrade: 0.01, maxLeverage: 1, maxDailyLoss: 0.03, maxDrawdown: 0.2, ...cfg.risk };
  strategy.prepare(candles);
  const guard = new RiskGuard(risk);
  const startIdx = candles.findIndex((x) => x.ts >= (cfg.start ?? -Infinity));
  const first = Math.max(startIdx < 0 ? candles.length : startIdx, strategy.warmup);
  const out = { trades: [], events: [], equity: [], position: null, cash: equity, first, halted: "" };
  if (first >= candles.length - (live ? 0 : 1)) return out;

  let cash = equity, pos = null, entryBar = 0, pending = null;
  const fillPrice = (px, side) => px * (1 + slippage * side);
  const close = (t, px, ts, reason) => {
    const fill = fillPrice(px, -t.side);
    const f = Math.abs(t.qty) * fill * fee;
    Object.assign(t, { exit: fill, exitTs: ts, reason });
    t.fees += f;
    const gross = t.side * t.qty * (fill - t.entry);
    t.pnl = gross - t.fees;
    out.trades.push(t);
    out.events.push({ ts, kind: "sell", price: fill, qty: t.qty, fee: f, pnl: t.pnl, reason });
    return gross - f;
  };

  for (let i = first; i < candles.length; i++) {
    const bar = candles[i];
    // 1) act on the previous close's decision at this bar's open
    if (pending) {
      const want = pending.side;
      if (pos && want !== pos.side) { cash += close(pos, bar.o, bar.ts, "signal"); pos = null; }
      if (pos == null && want !== FLAT) {
        const [ok, why] = guard.allowEntries(cash);
        const entry = fillPrice(bar.o, want);
        const stopOk = pending.stop != null && (pending.stop - entry) * want < 0;
        if (ok && stopOk) {
          const qty = positionSize(cash, entry, pending.stop, risk);
          if (qty > 0 && qty * entry >= minNotional) {
            const f = qty * entry * fee;
            cash -= f;
            pos = { side: want, qty, entryTs: bar.ts, entry, stop: pending.stop,
                    risk: qty * Math.abs(entry - pending.stop), fees: f, pnl: 0 };
            entryBar = i;
            out.events.push({ ts: bar.ts, kind: "buy", price: entry, qty, fee: f, stop: pending.stop });
          } else if (qty > 0) {
            out.events.push({ ts: bar.ts, kind: "skip", price: entry,
              reason: `order of ${(qty * entry).toFixed(2)} USDT is below the ${minNotional} USDT exchange minimum` });
          }
        } else if (!ok) {
          if (guard.halted) out.halted = why;
          out.events.push({ ts: bar.ts, kind: "skip", price: entry, reason: why });
        } else {
          out.events.push({ ts: bar.ts, kind: "skip", price: entry, reason: "price opened past the planned stop" });
        }
      } else if (pos && pending.stop != null) {
        pos.stop = Math.max(pos.stop, pending.stop);  // trail: only ever tighten
      }
      pending = null;
    }
    // 2) protective stop inside this bar
    if (pos && pos.stop != null && bar.l <= pos.stop) {
      cash += close(pos, Math.min(bar.o, pos.stop), bar.ts, "stop");
      pos = null;
    }
    // 3) mark to market
    const eq = cash + (pos ? pos.side * pos.qty * (bar.c - pos.entry) : 0);
    out.equity.push([bar.ts, eq]);
    guard.update(eq, bar.ts);
    // 4) decide for the next bar
    if (i < candles.length - 1) {
      const side = pos ? pos.side : FLAT;
      pending = strategy.signal(i, side, pos ? i - entryBar : 0);
      if (guard.halted && pos == null) { out.halted = guard.reason; pending = null; }
    }
  }
  out.cash = cash;
  out.position = pos;
  out.guard = guard;
  out.strategy = strategy;
  if (live && candles.length) {
    const j = candles.length - 1;
    out.trigger = strategy.trigger(j, pos ? pos.side : FLAT);
  }
  return out;
}

// 10 USDT bought at the open of the first candle at/after `start`.
export function buyAndHold(candles, { start, equity = 10, fee = 0.001, slippage = 0.0005 }) {
  const i = candles.findIndex((x) => x.ts >= start);
  if (i < 0) return null;
  const entry = candles[i].o * (1 + slippage);
  const qty = equity / entry;
  const f = qty * entry * fee;
  const curve = candles.slice(i).map((x) => [x.ts, equity - f + qty * (x.c - entry)]);
  return { entry, qty, fee: f, curve, entryTs: candles[i].ts };
}
