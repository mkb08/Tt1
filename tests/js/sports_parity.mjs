// Runs site/js/sports-core.js on the cases in argv[2] (JSON) and prints the
// answers, for tests/test_site_sports.py to compare with tt/sports. Also
// exercises site/api/kalshi.js with fetch stubbed out.
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import * as S from "../../site/js/sports-core.js";

const cases = JSON.parse(readFileSync(process.argv[2], "utf8"));
const out = {
  kalshiFee: cases.fees.map(([n, p, maker, mult]) => S.kalshiFee(n, p, maker, mult)),
  polyFee: cases.fees.map(([n, p, maker]) => S.polyFee(n, p, cases.schedule, maker)),
  score: cases.scores.map(([team, label, abbr]) => S.score(team, label, abbr)),
  assign: cases.assigns.map(([teams, labels]) => S.assign(teams, labels)),
  bids: cases.games.map(([g, half]) => S.bidsAllowed(g, half)),
  floorTick: cases.ticks.map(([p, t]) => S.floorTick(p, t)),
};

const games = cases.match.games.map((g) => ({ ...g, legs: {} }));
S.attachKalshi(games, cases.match.kalshi, 1);
S.attachPolymarket(games, cases.match.polymarket);
out.legs = games.map((g) => Object.fromEntries(Object.entries(g.legs).map(([k, l]) => [k, [l.id, l.bid, l.ask]])));
out.stink = games.map((g) => g.teams.map((t) => S.stinkLevel(g, t.abbr, 0.3)));
out.lock = games.map((g) => S.bestLock(g));

// the Vercel proxy
const require = createRequire(import.meta.url);
const handler = require("../../site/api/kalshi.js");
const calls = [];
globalThis.fetch = async (url) => { calls.push(url); return { status: 200, text: async () => "{}" }; };
const run = async (query, method = "GET") => {
  let status = 0;
  const res = { setHeader() {}, status(c) { status = c; return this; }, json() {}, send() {} };
  await handler({ query, method }, res);
  return status;
};
out.proxy = [
  await run({ path: "events", series_ticker: "KXNFLGAME", status: "open", evil: "x" }),
  await run({ path: "portfolio/balance" }),
  await run({ path: "markets/trades", ticker: "KXNFLGAME-26OCT11PHIJAC-PHI", min_ts: "1" }),
  await run({ path: "events" }, "POST"),
  await run({ path: "series/KXNBAGAME" }),
];
out.proxyUrls = calls;
console.log(JSON.stringify(out));
