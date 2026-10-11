// Live Binance candles for one interval and several symbols: REST history,
// then a WebSocket kline stream, falling back to REST polling when the
// stream is unavailable. Market data only; no keys.

const REST = "https://data-api.binance.vision/api/v3/klines";
const WS = "wss://data-stream.binance.vision/stream?streams=";
export const INTERVAL_MS = { "1m": 60e3, "5m": 300e3, "15m": 900e3, "1h": 3600e3, "4h": 4 * 3600e3 };

export class Feed {
  // markets: { BTC: "BTCUSDT", ... }; anchor: ms, the first candle to load
  constructor({ markets, interval, anchor, onData, onState, pollMs = 5000 }) {
    Object.assign(this, { markets, interval, anchor, onData, onState, pollMs });
    this.step = INTERVAL_MS[interval];
    this.store = Object.fromEntries(Object.keys(markets).map((m) => [m, []]));
    this.state = "connecting";
    this.lastTick = 0;
    this.ws = null; this.retry = 0; this.poll = null; this.stopped = false;
  }

  upsert(market, k) {
    const arr = this.store[market];
    const last = arr[arr.length - 1];
    if (!last || k.ts > last.ts) arr.push(k);
    else if (k.ts === last.ts) arr[arr.length - 1] = k;
    else {
      const i = arr.findIndex((x) => x.ts === k.ts);
      if (i >= 0) arr[i] = k;
    }
    this.lastTick = Date.now();
    this.onData && this.onData(market);
  }

  async fetchFrom(market, startTime) {
    for (let from = startTime; ;) {
      const url = `${REST}?symbol=${this.markets[market]}&interval=${this.interval}&limit=1000&startTime=${from}`;
      const r = await fetch(url, { cache: "no-store" });
      if (!r.ok) throw new Error(`Binance ${r.status}`);
      const rows = await r.json();
      if (this.stopped) return;
      for (const x of rows) this.upsert(market, { ts: x[0], o: +x[1], h: +x[2], l: +x[3], c: +x[4] });
      if (rows.length < 1000) return;
      from = rows[rows.length - 1][0] + this.step;
    }
  }

  // First call loads history from the anchor; later calls fetch from the latest candle on.
  catchUp() {
    return Promise.all(Object.keys(this.markets).map((m) => {
      const arr = this.store[m];
      return this.fetchFrom(m, arr.length ? arr[arr.length - 1].ts : this.anchor);
    }));
  }

  setState(s) {
    this.state = s;
    this.onState && this.onState(s);
  }

  startPolling() {
    if (this.poll || this.stopped) return;
    this.poll = setInterval(async () => {
      if (document.hidden) return;
      try { await this.catchUp(); if (this.state !== "stream") this.setState("poll"); }
      catch { if (this.state !== "stream") this.setState("down"); }
    }, this.pollMs);
  }

  stopPolling() { clearInterval(this.poll); this.poll = null; }

  connect() {
    if (this.stopped) return;
    if (!("WebSocket" in window)) { this.startPolling(); return; }
    const streams = Object.values(this.markets).map((s) => `${s.toLowerCase()}@kline_${this.interval}`).join("/");
    let ws;
    try { ws = this.ws = new WebSocket(WS + streams); } catch { this.startPolling(); return; }
    ws.onopen = () => { this.retry = 0; this.setState("stream"); this.stopPolling(); this.catchUp().catch(() => {}); };
    ws.onmessage = (ev) => {
      let msg;
      try { msg = JSON.parse(ev.data); } catch { return; }
      const k = msg && msg.data && msg.data.k;
      const market = k && Object.keys(this.markets).find((m) => this.markets[m] === k.s);
      if (market && k.i === this.interval) this.upsert(market, { ts: k.t, o: +k.o, h: +k.h, l: +k.l, c: +k.c });
    };
    ws.onclose = () => {
      if (this.ws === ws) this.ws = null;
      if (this.stopped) return;
      if (this.state === "stream") this.setState("poll");
      this.startPolling();
      this.retry = Math.min(this.retry + 1, 6);
      setTimeout(() => this.connect(), 5000 * 2 ** (this.retry - 1));   // 5 s, 10 s … ~2.5 min
    };
    ws.onerror = () => { try { ws.close(); } catch { /* closing anyway */ } };
  }

  async start() {
    this.setState("connecting");
    try {
      await this.catchUp();
    } catch (e) {
      this.setState("down");
      this.startPolling();
      this.connect();
      throw e;
    }
    this.connect();
    this.watchdog = setInterval(() => {
      // if the stream goes quiet, fetch over REST
      if (this.state === "stream" && Date.now() - this.lastTick > 30000 && !document.hidden) this.catchUp().catch(() => {});
    }, 5000);
    this.onVisible = () => { if (!document.hidden) this.catchUp().catch(() => {}); };
    document.addEventListener("visibilitychange", this.onVisible);
  }

  stop() {
    this.stopped = true;
    this.stopPolling();
    clearInterval(this.watchdog);
    if (this.onVisible) document.removeEventListener("visibilitychange", this.onVisible);
    if (this.ws) { try { this.ws.close(); } catch { /* ignore */ } this.ws = null; }
  }
}
