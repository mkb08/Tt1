// Runs the site's JS engine on a fixture and prints the results as JSON, for
// tests/test_site_engine.py to compare against the Python backtester.
import { readFileSync } from "node:fs";
import { run, STRATEGIES } from "../../site/js/engine.js";

const fx = JSON.parse(readFileSync(process.argv[2], "utf8"));
const candles = fx.candles.map(([ts, o, h, l, c]) => ({ ts, o, h, l, c }));
const results = fx.cases.map((k) => {
  const r = run(candles, new STRATEGIES[k.strategy](k.params || {}), k.cfg);
  return {
    trades: r.trades.map((t) => [t.entryTs, t.entry, t.qty, t.exitTs, t.exit, t.reason, t.pnl]),
    equity: r.equity,
    halted: r.halted,
  };
});
process.stdout.write(JSON.stringify(results));
