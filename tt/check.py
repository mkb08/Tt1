"""Pre-flight check: is this config tradeable, with this much money, on this
account, with this API key? Run it before paper, demo or live trading."""

from __future__ import annotations

from . import indicators as ind
from .data import fetch_recent
from .risk import RiskConfig
from .strategies import make_strategy


class Report:
    def __init__(self):
        self.problems = 0

    def ok(self, msg):
        print(f"  OK    {msg}")

    def info(self, msg):
        print(f"  ..    {msg}")

    def warn(self, msg):
        print(f"  WARN  {msg}")

    def fail(self, msg):
        self.problems += 1
        print(f"  FAIL  {msg}")


def market_rules(cfg: dict) -> dict:
    """min_qty, min_notional, price, funding info for the configured symbol."""
    exch, symbol = cfg.get("exchange", "binance"), cfg["symbol"]
    if exch == "bybit":
        from . import bybit
        rules = bybit.instrument(symbol)
        rules.update(bybit.ticker(symbol))
        return rules
    from .data import _ccxt_public
    ex = _ccxt_public(exch)
    ex.load_markets()
    m = ex.market(symbol)
    lim = m.get("limits", {})
    cs = float(m.get("contractSize") or 1.0)
    rules = {"min_qty": float((lim.get("amount") or {}).get("min") or 0) * cs,
             "min_notional": float((lim.get("cost") or {}).get("min") or 0),
             "max_leverage": float((lim.get("leverage") or {}).get("max") or 1),
             "last": float(ex.fetch_ticker(symbol)["last"]),
             "funding_rate": None, "funding_interval_h": None}
    if m.get("swap"):
        try:
            fr = ex.fetch_funding_rate(symbol)
            rules["funding_rate"] = fr.get("fundingRate")
            rules["funding_interval_h"] = 8
        except Exception:  # noqa: BLE001
            pass
    return rules


def min_equity(price: float, stop_pct: float, rules: dict, risk: RiskConfig) -> tuple[float, float]:
    """(smallest order value the exchange accepts, equity needed to place it)."""
    order_value = max(rules["min_qty"] * price, rules["min_notional"], 1e-9)
    frac = min(risk.risk_per_trade / stop_pct, risk.max_leverage) if stop_pct > 0 else risk.max_leverage
    return order_value, order_value / frac


def check_bybit_key(ex, rep: Report, trading: bool) -> None:
    fn = (getattr(ex, "private_get_v5_user_query_api", None)
          or getattr(ex, "privateGetV5UserQueryApi", None))
    if fn is None:
        rep.warn("this ccxt version can't read API key info; check it on bybit.com")
        return
    try:
        k = fn()["result"]
    except Exception as e:  # noqa: BLE001 - not available on demo trading
        rep.info(f"API key details unavailable here ({type(e).__name__}); check them on bybit.com")
        return
    perms = k.get("permissions") or {}
    flat = {p for v in perms.values() for p in (v or [])}
    if "Withdraw" in flat:
        rep.fail("API key can WITHDRAW funds: create a new key without withdrawal permission")
    else:
        rep.ok("API key cannot withdraw")
    if str(k.get("readOnly")) == "1":
        (rep.fail if trading else rep.ok)("API key is read-only"
                                          + (": it can't place orders" if trading else ""))
    if str(k.get("uta")) != "1":
        rep.fail("not a Unified Trading Account: upgrade it on Bybit (this toolkit assumes UTA)")
    else:
        rep.ok("Unified Trading Account")
    if k.get("ips"):
        rep.ok(f"key bound to IP(s) {', '.join(k['ips'])}")
    else:
        days = k.get("deadlineDay")
        rep.warn("key has no IP binding, so Bybit expires it"
                 + (f" in {days} days" if days not in (None, "") else "")
                 + "; bind it to a fixed IP (e.g. a VPS) to avoid a surprise stop")


def run(cfg: dict) -> int:
    rep = Report()
    symbol, tf = cfg["symbol"], cfg["timeframe"]
    mtype, mode, exch = cfg.get("market_type", "spot"), cfg.get("mode", "paper"), cfg.get("exchange", "binance")
    risk = RiskConfig.from_dict(cfg.get("risk"))
    print(f"\n{exch} {mtype} {symbol} {tf} | mode {mode} | strategy {cfg['strategy']} {cfg.get('params', {})}")

    print("\nConfig")
    if mtype == "swap" and ":" not in symbol:
        rep.fail(f"perpetual symbols look like BTC/USDT:USDT, not {symbol}")
    elif mtype == "spot" and ":" in symbol:
        rep.fail(f"{symbol} is a perpetual; use BTC/USDT for spot or set market_type swap")
    else:
        rep.ok("symbol matches market type")
    if risk.risk_per_trade > 0.02:
        rep.warn(f"risk_per_trade {risk.risk_per_trade:.1%}: above 2% a normal losing streak hurts badly")
    if risk.max_leverage > 3:
        rep.warn(f"max_leverage {risk.max_leverage}x: a single gap can do serious damage")
    if mtype == "swap" and not cfg.get("funding_8h") and mode == "paper":
        rep.warn("funding_8h is 0: paper results on perps will look better than reality")

    print("\nMarket")
    try:
        rules = market_rules(cfg)
    except Exception as e:  # noqa: BLE001
        rep.fail(f"can't load market rules: {type(e).__name__}: {e}")
        return rep.problems
    price = rules["last"]
    rep.ok(f"price {price:,.4f}; min order {rules['min_qty']:g} units / {rules['min_notional']:g} quote"
           + (f"; max leverage {rules['max_leverage']:g}x" if mtype == "swap" else ""))
    if rules.get("funding_rate") is not None:
        per_h = rules.get("funding_interval_h") or 8
        yearly = rules["funding_rate"] * 24 / per_h * 365
        rep.info(f"funding now {rules['funding_rate']:+.4%} every {per_h:g}h "
                 f"(~{yearly:+.1%}/yr of position value; positive = longs pay)")

    strat = make_strategy(cfg["strategy"], cfg.get("params"))
    candles = fetch_recent(symbol, tf, max(300, strat.warmup * 2), exch)
    if len(candles) <= strat.warmup:
        rep.fail(f"only {len(candles)} candles available; strategy needs {strat.warmup}")
        return rep.problems
    rep.ok(f"{len(candles)} recent {tf} candles loaded")
    atr_n = getattr(strat, "atr_n", 14)
    atr_mult = getattr(strat, "atr_mult", getattr(strat, "sl", 3.0))  # scalp calls its stop multiple `sl`
    atr = ind.atr(candles, atr_n)[-1]
    stop_pct = atr_mult * atr / candles[-1].close
    order_value, need = min_equity(price, stop_pct, rules, risk)
    rep.info(f"typical stop now {stop_pct:.1%} away ({atr_mult:g} x ATR{atr_n})")
    rep.info(f"at {risk.risk_per_trade:.1%} risk/trade a position is ~"
             f"{min(risk.risk_per_trade / stop_pct, risk.max_leverage):.0%} of equity")
    rep.info(f"smallest order the exchange accepts: {order_value:,.2f} {cfg.get('quote', 'USDT')}")
    print(f"        => you need at least ~{need:,.0f} {cfg.get('quote', 'USDT')} in this account "
          f"(more to be safe) for the bot to take every signal")

    if mode in ("demo", "testnet", "live") and exch != "paper":
        print(f"\nAccount ({mode})")
        try:
            from .broker import make_broker
            broker = make_broker(cfg)
        except SystemExit as e:
            rep.fail(str(e))
            return rep.problems
        except Exception as e:  # noqa: BLE001
            rep.fail(f"can't connect: {type(e).__name__}: {e}")
            return rep.problems
        for note in broker.setup_notes:
            rep.info(f"setup: {note}")
        eq = broker.equity()
        (rep.ok if eq >= need else rep.warn)(
            f"equity {eq:,.2f} {cfg.get('quote', 'USDT')}"
            + ("" if eq >= need else f": below the ~{need:,.0f} needed; signals will be skipped"))
        pos = broker.positions()
        rep.info(f"open positions: {pos or 'none'}")
        if exch == "bybit" and mode != "demo":
            check_bybit_key(broker.ex, rep, trading=True)

    print("\n" + ("All checks passed." if not rep.problems
                  else f"{rep.problems} problem(s) to fix before trading."))
    return rep.problems
