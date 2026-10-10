"""Brokers: a paper broker for risk-free testing and a ccxt wrapper for real
or testnet exchange accounts. Quantities are signed base-currency units
(+ long, - short)."""

from __future__ import annotations

import json
import math
import os
import time
from typing import Callable

EIGHT_HOURS_MS = 8 * 3_600_000


def load_json(path: str, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path: str, data) -> None:
    """Atomic write so a crash (or Android killing Termux) can't corrupt state."""
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


class PaperBroker:
    """Simulated account filled at live prices, with fees and slippage.

    Uses margin-style accounting: equity = cash + unrealised PnL, which works
    the same for longs and shorts. For perpetuals, `funding_8h` charges
    longs (and credits shorts) continuously at that rate per 8 hours.
    """

    def __init__(self, price_fn: Callable[[str], float], equity: float = 1000.0,
                 fee: float = 0.001, slippage: float = 0.0005,
                 state_path: str | None = None, allow_short: bool = True,
                 funding_8h: float = 0.0, clock: Callable[[], float] = time.time):
        self.price_fn, self.fee, self.slippage = price_fn, fee, slippage
        self.state_path, self.allow_short = state_path, allow_short
        self.funding_8h, self.clock = funding_8h, clock
        st = load_json(state_path, None) if state_path else None
        self.cash = st["cash"] if st else equity
        self.pos: dict[str, dict] = st["positions"] if st else {}
        self.fills: list[dict] = st.get("fills", []) if st else []
        self.funding_paid = st.get("funding_paid", 0.0) if st else 0.0
        self.accrued_to = st.get("accrued_to") if st else None

    def _now_ms(self) -> int:
        return int(self.clock() * 1000)

    def _save(self):
        if self.state_path:
            save_json(self.state_path, {"cash": self.cash, "positions": self.pos,
                                        "fills": self.fills[-500:],
                                        "funding_paid": self.funding_paid,
                                        "accrued_to": self.accrued_to})

    def _accrue_funding(self):
        now = self._now_ms()
        if self.funding_8h and self.accrued_to is not None:
            frac = (now - self.accrued_to) / EIGHT_HOURS_MS
            for sym, p in self.pos.items():
                if p["qty"]:
                    paid = p["qty"] * self.price(sym) * self.funding_8h * frac
                    self.cash -= paid
                    self.funding_paid += paid
        self.accrued_to = now

    def price(self, symbol: str) -> float:
        return self.price_fn(symbol)

    def position(self, symbol: str) -> float:
        return self.pos.get(symbol, {}).get("qty", 0.0)

    def positions(self) -> dict[str, float]:
        return {s: p["qty"] for s, p in self.pos.items() if p["qty"]}

    def equity(self) -> float:
        self._accrue_funding()
        upnl = sum(p["qty"] * (self.price(s) - p["entry"]) for s, p in self.pos.items() if p["qty"])
        return self.cash + upnl

    def round_qty(self, symbol: str, qty: float) -> float:
        return round(qty, 8)

    def min_notional(self, symbol: str) -> float:
        return 1.0

    def market_order(self, symbol: str, qty: float, reduce_only: bool = False) -> dict:
        self._accrue_funding()
        qty = self.round_qty(symbol, qty)
        cur = self.position(symbol)
        if reduce_only:
            if cur == 0 or (qty > 0) == (cur > 0):
                return {"skipped": "reduce_only with nothing to reduce"}
            qty = max(-abs(cur), min(abs(cur), qty))
        new = cur + qty
        if new < 0 and not self.allow_short:
            raise ValueError("short positions disabled for this account")
        px = self.price(symbol) * (1 + self.slippage * (1 if qty > 0 else -1))
        fee = abs(qty) * px * self.fee
        self.cash -= fee
        p = self.pos.setdefault(symbol, {"qty": 0.0, "entry": 0.0})
        if cur == 0 or (cur > 0) == (qty > 0):          # open or add
            p["entry"] = (p["entry"] * abs(cur) + px * abs(qty)) / abs(new)
        else:                                            # reduce, close or flip
            closed = min(abs(qty), abs(cur))
            self.cash += closed * (px - p["entry"]) * (1 if cur > 0 else -1)
            if abs(qty) > abs(cur):
                p["entry"] = px
        p["qty"] = round(new, 12)
        if p["qty"] == 0:
            p["entry"] = 0.0
        fill = {"ts": self._now_ms(), "symbol": symbol, "qty": qty,
                "price": px, "fee": fee}
        self.fills.append(fill)
        self._save()
        return fill


class CCXTBroker:
    """Real (or testnet) exchange account through ccxt.

    market_type "spot": long-only; the position is the base-asset balance, so
    use a dedicated (sub-)account.
    market_type "swap": linear perpetual futures; supports shorts. The
    account is switched to one-way position mode and the exchange leverage
    is set to `leverage` for each symbol.

    demo=True uses the exchange's demo-trading environment (real prices,
    fake money; Bybit and Binance support it). testnet=True uses the
    separate testnet.
    """

    def __init__(self, exchange_id: str, api_key: str = "", secret: str = "",
                 password: str = "", market_type: str = "spot",
                 testnet: bool = False, quote: str = "USDT",
                 symbols: list[str] | None = None, exchange=None,
                 demo: bool = False, leverage: float | None = None):
        if exchange is None:
            import ccxt  # type: ignore
            opts = {"apiKey": api_key, "secret": secret, "enableRateLimit": True,
                    "options": {"defaultType": market_type}}
            if password:
                opts["password"] = password
            exchange = getattr(ccxt, exchange_id)(opts)
            if testnet:
                exchange.set_sandbox_mode(True)
            elif demo:
                exchange.enable_demo_trading(True)
        self.ex = exchange
        self.market_type, self.quote = market_type, quote
        self.symbols = list(symbols or [])
        self.setup_notes: list[str] = []
        self.ex.load_markets()
        if market_type == "swap":
            self._setup_swap(leverage)

    def _setup_swap(self, leverage: float | None) -> None:
        """Best effort: exchanges reject these calls when nothing changes or a
        position is open, so failures are recorded rather than raised."""
        for s in self.symbols:
            try:
                self.ex.set_position_mode(False, s)  # one-way: one net position per symbol
            except Exception as e:  # noqa: BLE001
                self.setup_notes.append(f"{s} position mode: {e}")
            if leverage:
                try:
                    self.ex.set_leverage(leverage, s)
                except Exception as e:  # noqa: BLE001
                    self.setup_notes.append(f"{s} leverage {leverage}: {e}")

    def _contract_size(self, symbol: str) -> float:
        return float(self.ex.market(symbol).get("contractSize") or 1.0)

    def price(self, symbol: str) -> float:
        return float(self.ex.fetch_ticker(symbol)["last"])

    def positions(self) -> dict[str, float]:
        if self.market_type == "spot":
            bal = self.ex.fetch_balance()["total"]
            out = {}
            for s in self.symbols:
                base = self.ex.market(s)["base"]
                q = float(bal.get(base) or 0.0)
                if q:
                    out[s] = q
            return out
        out = {}
        for p in self.ex.fetch_positions(self.symbols or None):
            n = float(p.get("contracts") or 0.0) * float(p.get("contractSize") or 1.0)
            if n:
                out[p["symbol"]] = out.get(p["symbol"], 0.0) + (n if p.get("side") == "long" else -n)
        return out

    def position(self, symbol: str) -> float:
        if symbol not in self.symbols:
            self.symbols.append(symbol)
        return self.positions().get(symbol, 0.0)

    def equity(self) -> float:
        bal = self.ex.fetch_balance()
        cash = float(bal["total"].get(self.quote) or 0.0)
        if self.market_type == "spot":
            return cash + sum(q * self.price(s) for s, q in self.positions().items())
        upnl = sum(float(p.get("unrealizedPnl") or 0.0)
                   for p in self.ex.fetch_positions(self.symbols or None))
        return cash + upnl

    def round_qty(self, symbol: str, qty: float) -> float:
        cs = self._contract_size(symbol)
        sign = 1 if qty >= 0 else -1
        try:
            contracts = float(self.ex.amount_to_precision(symbol, abs(qty) / cs))
        except Exception:  # below minimum precision -> ccxt raises
            return 0.0
        return sign * contracts * cs

    def min_notional(self, symbol: str) -> float:
        lim = self.ex.market(symbol).get("limits", {})
        cost = (lim.get("cost") or {}).get("min") or 0.0
        amt = (lim.get("amount") or {}).get("min") or 0.0
        return max(float(cost), float(amt) * self._contract_size(symbol) * self.price(symbol), 1.0)

    def market_order(self, symbol: str, qty: float, reduce_only: bool = False) -> dict:
        qty = self.round_qty(symbol, qty)
        if qty == 0:
            return {"skipped": "rounds to zero"}
        side = "buy" if qty > 0 else "sell"
        amount = abs(qty) / self._contract_size(symbol)
        params = {"reduceOnly": True} if reduce_only and self.market_type != "spot" else {}
        return self.ex.create_order(symbol, "market", side, amount, None, params)


def make_broker(cfg: dict, price_fn: Callable[[str], float] | None = None,
                state_path: str | None = None):
    """Build a broker from a config dict (see config.example.json)."""
    exch = cfg.get("exchange", "binance")
    if cfg.get("mode") == "paper" or exch == "paper":
        if price_fn is None:
            from .data import fetch_price
            src = cfg.get("price_source", exch if exch != "paper" else "binance")
            price_fn = lambda s: fetch_price(s, src)  # noqa: E731
        swap = cfg.get("market_type", "spot") == "swap"
        return PaperBroker(price_fn, equity=cfg.get("paper_equity", 1000.0),
                           fee=cfg.get("fee", 0.001), slippage=cfg.get("slippage", 0.0005),
                           state_path=state_path, allow_short=swap,
                           funding_8h=cfg.get("funding_8h", 0.0) if swap else 0.0)
    env = os.environ.get
    key = env(cfg.get("api_key_env", "TT_API_KEY"), "")
    secret = env(cfg.get("secret_env", "TT_API_SECRET"), "")
    if not key or not secret:
        raise SystemExit(f"missing API credentials: set ${cfg.get('api_key_env', 'TT_API_KEY')} "
                         f"and ${cfg.get('secret_env', 'TT_API_SECRET')}")
    symbols = cfg.get("symbols") or ([cfg["symbol"]] if "symbol" in cfg else [])
    return CCXTBroker(exch, key, secret, env(cfg.get("password_env", "TT_API_PASSWORD"), ""),
                      market_type=cfg.get("market_type", "spot"),
                      testnet=cfg.get("mode") == "testnet" or cfg.get("testnet", False),
                      demo=cfg.get("mode") == "demo",
                      quote=cfg.get("quote", "USDT"), symbols=symbols,
                      leverage=exchange_leverage(cfg))


def exchange_leverage(cfg: dict) -> int:
    """Leverage to set on the exchange for perps. The bot's own cap
    (risk.max_leverage) limits position size; this only decides how much margin
    a position locks. One step above the cap leaves room for fees, so a
    full-size 1x position isn't rejected for insufficient margin."""
    cap = cfg.get("max_leverage") or (cfg.get("risk") or {}).get("max_leverage", 1.0)
    return int(cfg.get("exchange_leverage") or math.ceil(cap) + 1)
