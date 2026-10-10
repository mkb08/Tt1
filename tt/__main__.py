"""Command line: python -m tt <command> --help"""

from __future__ import annotations

import argparse
import json
import math
import sys

from . import backtest as bt
from .data import fetch_history, load_csv, save_csv, synthetic
from .risk import RiskConfig
from .strategies import STRATEGIES, make_strategy

GRIDS = {
    "trend": {"entry": [20, 55, 100], "exit": [10, 20], "atr_mult": [2.0, 3.0, 4.0]},
    "ema": {"fast": [10, 20, 50], "slow": [100, 200], "atr_mult": [3.0, 4.0]},
    "rsi": {"entry": [10, 15, 25], "exit": [55, 70], "trend_n": [100, 200]},
}


def pct(x: float) -> str:
    return "n/a" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:+.1%}"


def print_metrics(name: str, m: dict) -> None:
    print(f"\n== {name} ==")
    print(f"  trades        {m['trades']}   (under ~30 trades proves little)")
    print(f"  total return  {pct(m['total_return'])}   buy & hold {pct(m['buy_hold_return'])} "
          f"(its max drawdown {m['buy_hold_mdd']:.1%})")
    print(f"  CAGR          {pct(m['cagr'])}   over {m['years']:.1f} years")
    print(f"  max drawdown  {m['max_drawdown']:.1%}")
    print(f"  sharpe        {m['sharpe']:.2f}")
    print(f"  win rate      {m['win_rate']:.0%}   profit factor {m['profit_factor']:.2f}   "
          f"avg R {m['avg_r']:+.2f}")
    print(f"  exposure      {m['exposure']:.0%} of bars in the market")
    print(f"  fees paid     {m['fees_paid']:.2f}")
    if m["halted"]:
        print(f"  HALTED        {m['halted']}")


def load_data(args) -> list:
    if args.data == "synthetic":
        return synthetic()
    return load_csv(args.data)


def common_bt_args(p):
    p.add_argument("--data", required=True, help="CSV from `fetch`, or 'synthetic'")
    p.add_argument("--equity", type=float, default=1000.0)
    p.add_argument("--fee", type=float, default=0.001, help="per fill, 0.001 = 0.1%%")
    p.add_argument("--slippage", type=float, default=0.0005)
    p.add_argument("--risk", type=float, default=0.01, help="equity risked per trade")
    p.add_argument("--leverage", type=float, default=1.0, help="max notional / equity")


def bt_kwargs(args) -> dict:
    return {"equity": args.equity, "fee": args.fee, "slippage": args.slippage,
            "risk": RiskConfig(risk_per_trade=args.risk, max_leverage=args.leverage,
                               max_daily_loss=1.0, max_drawdown=1.0)}


def cmd_fetch(args):
    candles = fetch_history(args.symbol, args.timeframe, args.days, args.exchange)
    save_csv(args.out, candles)
    print(f"saved {len(candles)} candles to {args.out}")


def cmd_backtest(args):
    candles = load_data(args)
    strat = make_strategy(args.strategy, json.loads(args.params))
    res = bt.backtest(candles, strat, **bt_kwargs(args))
    print_metrics(f"{strat.name} {strat.params()}", res.metrics())
    mc = bt.monte_carlo(res.trades, args.equity)
    if mc:
        print(f"  monte carlo   median DD {mc['dd_median']:.1%}, 95th pct DD {mc['dd_95']:.1%}, "
              f"P(loss) {mc['prob_loss']:.0%}")
    if args.trades:
        for t in res.trades:
            print(f"  {t.entry_ts} {'L' if t.side > 0 else 'S'} {t.entry:.2f} -> {t.exit:.2f} "
                  f"{t.reason:6s} pnl {t.pnl:+.2f}")


def cmd_compare(args):
    candles = load_data(args)
    rows = []
    for name in STRATEGIES:
        m = bt.backtest(candles, make_strategy(name), **bt_kwargs(args)).metrics()
        rows.append((name, m))
    print(f"{'strategy':10s} {'trades':>6s} {'return':>9s} {'maxDD':>7s} {'sharpe':>7s} "
          f"{'win%':>5s} {'PF':>5s} {'expo':>5s}")
    for name, m in rows:
        print(f"{name:10s} {m['trades']:6d} {pct(m['total_return']):>9s} "
              f"{m['max_drawdown']:7.1%} {m['sharpe']:7.2f} {m['win_rate']:5.0%} "
              f"{m['profit_factor']:5.2f} {m['exposure']:5.0%}")
    bh = rows[0][1]
    print(f"{'buy&hold':10s} {'':6s} {pct(bh['buy_hold_return']):>9s} {bh['buy_hold_mdd']:7.1%}")


def cmd_walkforward(args):
    candles = load_data(args)
    grid = json.loads(args.grid) if args.grid else GRIDS[args.strategy]
    wf = bt.walk_forward(candles, STRATEGIES[args.strategy], grid, args.folds, **bt_kwargs(args))
    for f in wf["folds"]:
        m = f["oos"]
        print(f"fold {f['fold']}: params {f['params']} -> out-of-sample "
              f"{pct(m['total_return'])}, maxDD {m['max_drawdown']:.1%}, "
              f"{m['trades']} trades (buy&hold {pct(m['buy_hold_return'])})")
    print(f"\nout-of-sample compounded: {pct(wf['oos_compounded_return'])}, "
          f"profitable folds {wf['oos_profitable_folds']}/{len(wf['folds'])}")
    print("Only out-of-sample numbers estimate live results. In-sample bests are curve-fit.")


def cmd_demo(args):
    print("Offline demo on synthetic data (proves the install works, not that "
          "any strategy makes money).")
    args.data = "synthetic"
    cmd_compare(args)


def cmd_bot(args):
    from .broker import make_broker
    from .live import Bot
    with open(args.config) as f:
        cfg = json.load(f)
    if args.command == "paper":
        cfg["mode"] = "paper"
    elif cfg.get("mode") == "live" and not args.i_accept_the_risk:
        sys.exit("live mode trades real money: re-run with --i-accept-the-risk")
    broker = make_broker(cfg, state_path=cfg.get("paper_state_file", "state/paper.json"))
    Bot(cfg, broker).run()


def cmd_copy(args):
    from .copytrade import build_engine
    with open(args.config) as f:
        cfg = json.load(f)
    if not cfg.get("dry_run", True) and not args.i_accept_the_risk:
        sys.exit("dry_run is false, so real orders will be sent: re-run with --i-accept-the-risk")
    engine = build_engine(cfg)
    if args.once:
        print(json.dumps(engine.sync_once(), indent=2))
    else:
        engine.run(cfg.get("poll_seconds", 15))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="tt", description="Termux trading toolkit")
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("fetch", help="download candles to CSV")
    p.add_argument("--symbol", default="BTC/USDT")
    p.add_argument("--timeframe", default="4h")
    p.add_argument("--days", type=float, default=1460)
    p.add_argument("--exchange", default="binance")
    p.add_argument("--out", required=True)
    p.set_defaults(fn=cmd_fetch)

    p = sub.add_parser("backtest", help="backtest one strategy")
    common_bt_args(p)
    p.add_argument("--strategy", choices=sorted(STRATEGIES), default="trend")
    p.add_argument("--params", default="{}", help='JSON, e.g. \'{"entry": 55}\'')
    p.add_argument("--trades", action="store_true", help="list every trade")
    p.set_defaults(fn=cmd_backtest)

    p = sub.add_parser("compare", help="all strategies with default params vs buy & hold")
    common_bt_args(p)
    p.set_defaults(fn=cmd_compare)

    p = sub.add_parser("walkforward", help="out-of-sample test with re-optimisation")
    common_bt_args(p)
    p.add_argument("--strategy", choices=sorted(STRATEGIES), default="trend")
    p.add_argument("--grid", help="JSON param grid; defaults per strategy")
    p.add_argument("--folds", type=int, default=4)
    p.set_defaults(fn=cmd_walkforward)

    p = sub.add_parser("demo", help="offline smoke test")
    p.set_defaults(fn=cmd_demo, equity=1000.0, fee=0.001, slippage=0.0005,
                   risk=0.01, leverage=1.0)

    for name, helptext in (("paper", "run the bot on live prices with a simulated account"),
                           ("live", "run the bot per config mode (testnet or live)")):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("--config", default="config.json")
        p.add_argument("--i-accept-the-risk", action="store_true")
        p.set_defaults(fn=cmd_bot)

    p = sub.add_parser("copy", help="mirror a leader's positions into followers")
    p.add_argument("--config", default="copy.json")
    p.add_argument("--once", action="store_true", help="one sync, print the plan, exit")
    p.add_argument("--i-accept-the-risk", action="store_true")
    p.set_defaults(fn=cmd_copy)

    args = ap.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
