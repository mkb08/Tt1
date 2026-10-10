"""Single-symbol bot for paper, testnet or live trading.

Each poll it reconciles with the broker (the broker is the source of truth),
enforces the stop, and on every newly closed bar asks the strategy for a
decision. State is saved after every action so restarts are safe.
"""

from __future__ import annotations

import time

from .broker import load_json, save_json
from .data import TIMEFRAME_MS, Candle, fetch_recent
from .notify import Notifier
from .risk import RiskConfig, RiskGuard, position_size
from .strategies import FLAT, LONG, Strategy, make_strategy


def _sign(x: float) -> int:
    return (x > 0) - (x < 0)


class Bot:
    def __init__(self, cfg: dict, broker, notifier: Notifier | None = None,
                 strategy: Strategy | None = None):
        self.cfg, self.broker = cfg, broker
        self.symbol, self.tf = cfg["symbol"], cfg["timeframe"]
        self.strategy = strategy or make_strategy(cfg["strategy"], cfg.get("params"))
        self.risk = RiskConfig.from_dict(cfg.get("risk"))
        self.state_path = cfg.get("state_file", "state/bot.json")
        self.publish_path = cfg.get("publish_file")
        self.n = notifier or Notifier(title=f"tt {self.symbol}")
        self.state = load_json(self.state_path, {})
        self.guard = RiskGuard(self.risk, self.state.get("guard"))

    # -- persistence -------------------------------------------------------
    def _save(self):
        self.state["guard"] = self.guard.state()
        save_json(self.state_path, self.state)

    def _publish(self, equity: float, qty: float):
        """Write this bot's position as a copy-trade leader feed."""
        if self.publish_path:
            save_json(self.publish_path, {"ts": int(time.time() * 1000), "equity": equity,
                                          "positions": {self.symbol: qty} if qty else {}})

    # -- actions -----------------------------------------------------------
    def _close(self, qty: float, why: str):
        res = self.broker.market_order(self.symbol, -qty, reduce_only=True)
        self.n.alert(f"CLOSE {self.symbol} {qty:+.6f} ({why}) -> {res}")
        self.state.update(side=FLAT, stop=None, entry_ts=None)

    def check_stop(self, price: float, qty: float) -> bool:
        stop = self.state.get("stop")
        side = _sign(qty)
        if stop is None or side == FLAT:
            return False
        if (side == LONG and price <= stop) or (side != LONG and price >= stop):
            self._close(qty, f"stop {stop:.4f} hit at {price:.4f}")
            self._save()
            return True
        return False

    def on_bar(self, candles: list[Candle], qty: float, equity: float) -> None:
        last = candles[-1]
        self.strategy.prepare(candles)
        side = _sign(qty)
        held = 0
        if side and self.state.get("entry_ts"):
            held = max(0, (last.ts - self.state["entry_ts"]) // TIMEFRAME_MS[self.tf])
        sig = self.strategy.signal(len(candles) - 1, side, held)

        if sig.side == side:
            if side and sig.stop is not None:
                old = self.state.get("stop")
                new = sig.stop if old is None else (max(old, sig.stop) if side == LONG
                                                    else min(old, sig.stop))
                if new != old:
                    self.n.log(f"trail stop {old} -> {new:.4f}")
                self.state["stop"] = new
            return

        if side:
            self._close(qty, "signal")
            equity = self.broker.equity()
        if sig.side == FLAT:
            return
        if sig.side != LONG and self.cfg.get("market_type", "spot") == "spot":
            self.n.log("short signal ignored: spot accounts are long-only")
            return
        ok, why = self.guard.allow_entries(equity)
        if not ok:
            self.n.alert(f"entry {sig.side:+d} blocked: {why}")
            return
        price = self.broker.price(self.symbol)
        if sig.stop is None or (sig.stop - price) * sig.side >= 0:
            self.n.log(f"skip entry: stop {sig.stop} invalid vs price {price}")
            return
        size = self.broker.round_qty(self.symbol, position_size(equity, price, sig.stop, self.risk))
        if size <= 0 or size * price < self.broker.min_notional(self.symbol):
            self.n.alert(f"entry skipped: size {size} below exchange minimum "
                         f"(equity {equity:.2f} too small for this stop distance)")
            return
        res = self.broker.market_order(self.symbol, size * sig.side)
        # filled during the bar after `last`; matches the backtester's count
        self.state.update(side=sig.side, stop=sig.stop,
                          entry_ts=last.ts + TIMEFRAME_MS[self.tf])
        self.n.alert(f"OPEN {'LONG' if sig.side == LONG else 'SHORT'} {self.symbol} "
                     f"{size:.6f} @~{price:.4f} stop {sig.stop:.4f} -> {res}")

    def step(self, candles: list[Candle], now_ms: int | None = None) -> None:
        now_ms = now_ms or int(time.time() * 1000)
        equity = self.broker.equity()
        self.guard.update(equity, now_ms)
        qty = self.broker.position(self.symbol)

        # reconcile: if the exchange position differs from our record (manual
        # trade, liquidation, exchange-side stop) trust the exchange
        if _sign(qty) != self.state.get("side", FLAT):
            self.n.alert(f"reconcile: broker position {qty:+.6f}, state said "
                         f"{self.state.get('side', FLAT)}")
            self.state.update(side=_sign(qty), stop=self.state.get("stop") if qty else None,
                              entry_ts=self.state.get("entry_ts") if qty else None)

        if self.guard.halted and qty:
            self._close(qty, self.guard.reason)
            qty = 0.0
        elif qty and self.check_stop(self.broker.price(self.symbol), qty):
            qty = 0.0

        if candles and candles[-1].ts > self.state.get("last_bar_ts", 0):
            if len(candles) <= self.strategy.warmup:
                self.n.log(f"need more than {self.strategy.warmup} candles, got {len(candles)}")
            else:
                self.on_bar(candles, qty, equity)
            self.state["last_bar_ts"] = candles[-1].ts
            qty = self.broker.position(self.symbol)
            self.n.log(f"bar {candles[-1].ts} close {candles[-1].close:.4f} "
                       f"equity {self.broker.equity():.2f} position {qty:+.6f} "
                       f"stop {self.state.get('stop')}")
        self._publish(equity, qty)
        self._save()

    def run(self) -> None:
        poll = self.cfg.get("poll_seconds", 60)
        lookback = max(self.strategy.warmup * 2, 300)
        ccxt_ex = getattr(self.broker, "ex", None)
        self.n.alert(f"bot started: {self.cfg.get('mode')} {self.symbol} {self.tf} "
                     f"{self.strategy.name} {self.strategy.params()}")
        errors = 0
        while True:
            try:
                candles = fetch_recent(self.symbol, self.tf, lookback,
                                       self.cfg.get("exchange", "binance"), ccxt_ex)
                self.step(candles)
                errors = 0
                time.sleep(poll)
            except KeyboardInterrupt:
                self.n.alert("bot stopped by user")
                return
            except Exception as e:  # noqa: BLE001 - keep running, back off
                errors += 1
                wait = min(poll * 2 ** errors, 900)
                self.n.alert(f"error #{errors}: {type(e).__name__}: {e}; retry in {wait}s")
                time.sleep(wait)
