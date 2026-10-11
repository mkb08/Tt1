// Read-only proxy to Kalshi's public market data for the sports page.
// Kalshi refuses requests that carry a browser Origin header, so the page
// asks this function instead. Only a few public GET endpoints are allowed,
// and responses are cached at the edge for a few seconds.
const BASE = "https://api.elections.kalshi.com/trade-api/v2/";
const ALLOWED = [
  /^events$/,
  /^markets\/trades$/,
  /^series\/KX[A-Z0-9]+$/,
];
const PARAMS = new Set(["series_ticker", "status", "with_nested_markets", "limit", "cursor",
                        "ticker", "min_ts", "max_ts"]);

module.exports = async (req, res) => {
  const path = String(req.query.path || "");
  if (req.method !== "GET" || !ALLOWED.some((re) => re.test(path))) {
    res.status(400).json({ error: "unsupported path" });
    return;
  }
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(req.query)) {
    if (PARAMS.has(k)) qs.set(k, String(v).slice(0, 200));
  }
  try {
    const r = await fetch(`${BASE}${path}?${qs}`, { headers: { "User-Agent": "tt-site/1.0" } });
    const body = await r.text();
    res.setHeader("Content-Type", "application/json");
    res.setHeader("Cache-Control", path.startsWith("series/")
      ? "public, s-maxage=3600" : "public, s-maxage=10, stale-while-revalidate=20");
    res.status(r.status).send(body);
  } catch (e) {
    res.status(502).json({ error: "kalshi unreachable" });
  }
};
