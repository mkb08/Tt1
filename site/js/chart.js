// Small dependency-free SVG line chart with a crosshair tooltip, shared by
// the backtest dashboard and the live demo.

const NS = "http://www.w3.org/2000/svg";

export const h = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
};

export const svgEl = (tag, attrs = {}, parent) => {
  const n = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  if (parent) parent.appendChild(n);
  return n;
};

// One listener hides every chart's readout when the pointer goes down elsewhere
// (touch screens fire pointerleave on lift, so taps must not hide it).
const registry = new Map();
document.addEventListener("pointerdown", (e) => {
  for (const c of registry.values()) if (!c.svg.contains(e.target)) c.hide();
});

export function legend(target, series) {
  target.textContent = "";
  for (const s of series) {
    const sp = h("span");
    const i = h("i");
    i.style.borderColor = s.color;
    sp.append(i, document.createTextNode(s.name));
    target.appendChild(sp);
  }
}

export function niceLogTicks(lo, hi) {
  const out = [];
  for (let p = Math.floor(Math.log10(lo)); p <= Math.ceil(Math.log10(hi)); p++) {
    for (const k of [1, 2, 5]) { const v = k * 10 ** p; if (v >= lo && v <= hi) out.push(v); }
  }
  return out;
}

export function niceTicks(lo, hi, count = 5) {
  const span = hi - lo || Math.abs(hi) || 1;
  const raw = span / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((k) => k * mag).find((s) => span / s <= count) || 10 * mag;
  const out = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + step * 1e-9; v += step) out.push(+v.toPrecision(12));
  return out;
}

// Ticks for a millisecond time axis, labelled in the viewer's time zone.
export function timeTicks(x0, x1, max = 6) {
  const H = 3600e3, D = 24 * H;
  const steps = [H, 2 * H, 3 * H, 4 * H, 6 * H, 12 * H, D, 2 * D, 7 * D, 14 * D, 30 * D];
  const step = steps.find((s) => (x1 - x0) / s <= max) || 60 * D;
  const d = new Date(x0);
  if (step >= D) {
    d.setHours(0, 0, 0, 0);
    if (d.getTime() < x0) d.setDate(d.getDate() + 1);
  } else {
    d.setMinutes(0, 0, 0);
    if (d.getTime() < x0) d.setHours(d.getHours() + 1);
    while (d.getHours() % (step / H)) d.setHours(d.getHours() + 1);
  }
  const out = [];
  while (d.getTime() <= x1) {
    const label = step < D && d.getHours() !== 0
      ? d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
      : d.toLocaleDateString([], { day: "numeric", month: "short" });
    out.push({ x: d.getTime(), label });
    if (step >= D) d.setDate(d.getDate() + step / D); else d.setHours(d.getHours() + step / H);
  }
  return out;
}

export function lineChart(root, cfg) {
  root.textContent = "";
  const W = Math.max(280, root.clientWidth);
  const narrow = W < 520;
  const H = cfg.height || (narrow ? 230 : 280);
  const m = { top: 12, right: cfg.right ?? (narrow ? 10 : 98), bottom: 26, left: cfg.left ?? 44 };
  const iw = W - m.left - m.right, ih = H - m.top - m.bottom;
  const xs = cfg.x;
  const x0 = xs[0], x1 = xs[xs.length - 1] === x0 ? x0 + 1 : xs[xs.length - 1];
  const sx = (d) => m.left + ((d - x0) / (x1 - x0)) * iw;
  const tf = cfg.log ? Math.log10 : (v) => v;
  const [d0, d1] = cfg.domain;
  const sy = (v) => m.top + ih - ((tf(v) - tf(d0)) / (tf(d1) - tf(d0))) * ih;

  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: "img", tabindex: "0",
    "aria-label": cfg.label + ". Use left and right arrow keys to read values." });
  root.appendChild(svg);

  for (const t of cfg.ticks) {
    const y = sy(t);
    svgEl("line", { x1: m.left, x2: m.left + iw, y1: y, y2: y,
      stroke: t === cfg.base ? "var(--baseline)" : "var(--grid)", "stroke-width": 1 }, svg);
    const tx = svgEl("text", { x: m.left - 8, y: y + 4, "text-anchor": "end", "font-size": 11,
      fill: "var(--axis)", "font-variant-numeric": "tabular-nums" }, svg);
    tx.textContent = cfg.tickFmt(t);
  }
  for (const t of cfg.xTicks || []) {
    const tx = svgEl("text", { x: sx(t.x), y: H - 6, "text-anchor": "middle", "font-size": 11, fill: "var(--axis)" }, svg);
    tx.textContent = t.label;
  }
  // annotations: vertical event lines and horizontal price levels (hairlines)
  for (const v of cfg.vlines || []) {
    if (v.x < x0 || v.x > x1) continue;
    const x = sx(v.x), right = x > m.left + iw * 0.7;
    svgEl("line", { x1: x, x2: x, y1: m.top, y2: m.top + ih, stroke: "var(--baseline)", "stroke-width": 1 }, svg);
    const tx = svgEl("text", { x: right ? x - 4 : x + 4, y: m.top + ih - 6, "text-anchor": right ? "end" : "start",
      "font-size": 11, fill: "var(--muted)" }, svg);
    tx.textContent = v.label;
  }
  for (const l of cfg.levels || []) {
    if (l.y < Math.min(d0, d1) || l.y > Math.max(d0, d1)) continue;
    const y = sy(l.y);
    svgEl("line", { x1: m.left, x2: m.left + iw, y1: y, y2: y, stroke: "var(--text-2)", "stroke-width": 1, opacity: 0.55 }, svg);
    const tx = svgEl("text", { x: m.left + iw - 4, y: y - 5, "text-anchor": "end", "font-size": 11, fill: "var(--text-2)" }, svg);
    tx.textContent = l.label;
  }

  const ends = [];
  for (const s of cfg.series) {
    let dstr = "", started = false;
    for (let i = 0; i < xs.length; i++) {
      const v = s.values[i];
      if (v == null) { started = false; continue; }
      dstr += (started ? "L" : "M") + sx(xs[i]).toFixed(1) + " " + sy(v).toFixed(1);
      started = true;
    }
    svgEl("path", { d: dstr, fill: "none", stroke: s.color, "stroke-width": 2,
      "stroke-linejoin": "round", "stroke-linecap": "round" }, svg);
    const lastV = s.values[s.values.length - 1];
    if (lastV != null) ends.push({ s, x: sx(xs[xs.length - 1]), y: sy(lastV), v: lastV });
  }
  if (cfg.livePulse && ends[0]) {
    svgEl("circle", { cx: ends[0].x, cy: ends[0].y, r: 4, fill: "none", stroke: ends[0].s.color,
      "stroke-width": 2, class: "pulse" }, svg);
  }
  for (const e of ends) {
    svgEl("circle", { cx: e.x, cy: e.y, r: 4, fill: e.s.color, stroke: "var(--surface)", "stroke-width": 2 }, svg);
  }
  for (const mk of cfg.markers || []) {
    if (mk.x < x0 || mk.x > x1) continue;
    const x = sx(mk.x), y = sy(mk.y), up = mk.kind === "buy";
    const pts = up ? `${x},${y + 2} ${x - 6},${y + 12} ${x + 6},${y + 12}` : `${x},${y - 2} ${x - 6},${y - 12} ${x + 6},${y - 12}`;
    svgEl("polygon", { points: pts, fill: "var(--text)", stroke: "var(--surface)", "stroke-width": 2, "stroke-linejoin": "round" }, svg);
  }
  if (!narrow && cfg.endFmt) {
    const sorted = [...ends].sort((a, b) => a.y - b.y);
    sorted.forEach((e, i) => {
      const prev = sorted[i - 1], next = sorted[i + 1];
      if ((prev && e.y - prev.y < 15) || (next && next.y - e.y < 15)) return;
      const t = svgEl("text", { x: e.x + 9, y: e.y + 4, "font-size": 12, fill: "var(--text-2)" }, svg);
      t.textContent = (e.s.short ? e.s.short + " " : "") + cfg.endFmt(e.v);
    });
  }

  const cross = svgEl("line", { y1: m.top, y2: m.top + ih, stroke: "var(--axis)", "stroke-width": 1, opacity: 0 }, svg);
  const dots = cfg.series.map((s) => svgEl("circle", { r: 4, fill: s.color, stroke: "var(--surface)", "stroke-width": 2, opacity: 0 }, svg));
  const tip = h("div", "tip");
  tip.setAttribute("aria-hidden", "true");
  root.appendChild(tip);
  let idx = xs.length - 1;

  function show(i) {
    idx = Math.max(0, Math.min(xs.length - 1, i));
    const x = sx(xs[idx]);
    cross.setAttribute("x1", x); cross.setAttribute("x2", x); cross.setAttribute("opacity", 1);
    tip.textContent = "";
    tip.appendChild(h("div", "date", cfg.tipTitle(xs[idx], idx)));
    cfg.series.forEach((s, k) => {
      const v = s.values[idx];
      if (v == null) { dots[k].setAttribute("opacity", 0); return; }
      dots[k].setAttribute("cx", x); dots[k].setAttribute("cy", sy(v)); dots[k].setAttribute("opacity", 1);
      const row = h("div", "row");
      const key = h("i"); key.style.borderColor = s.color;
      row.append(key, h("b", null, cfg.tipFmt(v)), h("span", null, s.name));
      tip.appendChild(row);
    });
    for (const mk of cfg.markers || []) {
      if (mk.x === xs[idx]) tip.appendChild(h("div", "event", mk.label));
    }
    tip.style.opacity = 1;
    const scale = root.clientWidth / W;
    let left = x * scale + 12;
    if (left + tip.offsetWidth > root.clientWidth) left = x * scale - tip.offsetWidth - 12;
    tip.style.left = Math.max(0, left) + "px";
    tip.style.top = Math.max(0, m.top * scale + 4) + "px";
  }
  function hide() {
    cross.setAttribute("opacity", 0);
    dots.forEach((d) => d.setAttribute("opacity", 0));
    tip.style.opacity = 0;
  }
  function nearest(clientX) {
    const r = svg.getBoundingClientRect();
    const px = ((clientX - r.left) / r.width) * W;
    const d = x0 + ((px - m.left) / iw) * (x1 - x0);
    let lo = 0, hi = xs.length - 1;
    while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (xs[mid] < d) lo = mid; else hi = mid; }
    return Math.abs(xs[lo] - d) <= Math.abs(xs[hi] - d) ? lo : hi;
  }
  svg.addEventListener("pointermove", (e) => show(nearest(e.clientX)));
  svg.addEventListener("pointerdown", (e) => show(nearest(e.clientX)));
  svg.addEventListener("pointerleave", (e) => { if (e.pointerType === "mouse") hide(); });
  svg.addEventListener("focus", () => show(idx));
  svg.addEventListener("blur", hide);
  svg.addEventListener("keydown", (e) => {
    const step = e.shiftKey ? (cfg.bigStep || 30) : (cfg.step || 7);
    if (e.key === "ArrowLeft") { show(idx - step); e.preventDefault(); }
    if (e.key === "ArrowRight") { show(idx + step); e.preventDefault(); }
  });
  registry.set(root.id, { svg, hide });
  return { svg, show, hide, isOpen: () => tip.style.opacity === "1", index: () => idx };
}
