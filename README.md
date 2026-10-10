# tt — a trading and copy-trading toolkit that runs on your phone (Termux)

`tt` is a small Python toolkit for Android/Termux. It lets you:

1. **Download** market data (no API key needed).
2. **Backtest** strategies with realistic costs: fees, slippage, next-bar fills and stop gaps.
3. **Walk-forward test** them. Parameters are optimised on past data and judged only on unseen data.
4. **Paper trade** on live prices with a simulated account.
5. **Trade live** on 100+ crypto exchanges through `ccxt`, spot or perpetual futures.
6. **Copy trade**: mirror a leader's positions into any number of follower accounts, each with its own risk limits.

The core uses only the Python standard library, so it installs in seconds on a phone. `ccxt` is needed only for live exchange access.

**Trading from Kenya on Bybit?** Start with [docs/BYBIT_KENYA.md](docs/BYBIT_KENYA.md). It covers the 4 November 2026 licensing deadline, tax, M-Pesa funding, API keys, demo trading, minimum capital, and ready-made Bybit configs.

---

## Read this first: what "profitable" honestly means

No code can promise profit, and anyone who sells a "guaranteed profitable bot" or copy-trade signal is selling marketing. The evidence on retail trading is harsh:

- EU-regulated brokers must publish how many retail CFD accounts lose money. The figure is typically **70–80%+**.
- A study of Brazilian futures day traders who persisted for more than 300 days (Chague, De-Losso & Giovannetti, 2019) found **97% lost money**.
- Copy-trading leaderboards suffer from survivorship bias. You see the leaders who got lucky, not the ones who blew up. Many top leaders use martingale or no-stop strategies that look smooth until one bad move wipes the account.

What *does* have long-run evidence is **trend following / time-series momentum**. Moskowitz, Ooi & Pedersen (2012) and Hurst, Ooi & Pedersen (2017, "A Century of Evidence on Trend-Following") document it across asset classes. It is not magic either: it wins rarely but big, loses often but small, and can lag buy-and-hold for years.

So this toolkit is built to **find out honestly whether an idea works before it touches money**, and to keep losses small when it doesn't.

### What the included strategies did on real data

Binance spot, 4h candles, 2020-10-12 → 2026-10-10. Settings: start equity 1,000, 1% of equity risked per trade, 1x max leverage, 0.1% fee + 0.05% slippage on every fill.

| Market | Strategy | Trades | Return | Max drawdown | Sharpe |
|---|---|---:|---:|---:|---:|
| BTC | `trend` (Donchian 55/20 breakout) | 110 | +52.9% | 7.6% | 1.09 |
| BTC | `ema` (20/100 cross) | 191 | +52.3% | 10.2% | 1.09 |
| BTC | `rsi` (dip-buying) | 184 | **−10.4%** | 15.3% | −0.59 |
| BTC | buy & hold | — | +581% | **77.0%** | — |
| ETH | `ema` | 182 | +40.8% | 8.8% | 0.88 |
| SOL | `ema` | 181 | +73.4% | 10.7% | 1.07 |

Walk-forward results (re-optimised on the past, traded on the next unseen ~15 months, 4 folds):

| Market / strategy | Out-of-sample compounded | Profitable folds |
|---|---:|---:|
| BTC `trend` | +25.4% | 3/4 |
| BTC `ema` | +34.6% | 4/4 |
| BTC `rsi` | **−17.3%** | 0/4 |
| ETH `ema` | +20.8% | 4/4 |

**How to read this:**

- The trend strategies made modest, steady money with small drawdowns: roughly 4–8% a year at 1% risk.
- Buy-and-hold made far more in this crypto bull era, but it fell 77% on the way. Most people can't sit through that.
- Short-term dip-buying lost money once costs were included, even though it won 65% of its trades. A high win rate is not an edge.
- Raising risk to 3% per trade roughly tripled returns (BTC `ema`: +174%) and also tripled drawdown (27.6%). Leverage scales both sides.
- Past results don't predict future ones. Re-run these yourself; the numbers change as new data arrives.

---

## Install on Termux

Install Termux from **F-Droid** or the Termux GitHub releases. Then:

```bash
pkg install -y git
git clone https://github.com/mkb08/Tt1 && cd Tt1
bash install_termux.sh           # core only: backtest + paper trading
bash install_termux.sh --ccxt    # add this for live exchange trading
```

The script runs the test suite and an offline demo at the end, so you know the install works.

## Workflow: idea → money, in this order

```bash
# 1. Data (Binance public data, no key). Use --exchange <id> with ccxt for others.
python -m tt fetch --symbol BTC/USDT --timeframe 4h --days 2190 --out data/btc_4h.csv

# 2. Compare all strategies vs buy & hold
python -m tt compare --data data/btc_4h.csv

# 3. Look at one closely (adds Monte Carlo drawdown estimates)
python -m tt backtest --data data/btc_4h.csv --strategy trend --params '{"entry": 55, "exit": 20}'
#    perpetual futures: count funding too, e.g. --fee 0.00055 --funding 0.0001,
#    or real history: --funding-csv from `python -m tt fetch-funding`

# 4. The honest test: walk-forward, out-of-sample only
python -m tt walkforward --data data/btc_4h.csv --strategy trend

# 5. Pre-flight: exchange minimums, the account size you need, API key safety
cp config.example.json config.json      # edit symbol / strategy / risk
python -m tt check --config config.json

# 6. Paper trade on live prices for 1–3 months
scripts/run.sh paper --config config.json

# 7. Demo or testnet, then live with money you can afford to lose
#    set "mode": "demo" (or "testnet" / "live") in config.json, export your keys:
export TT_API_KEY=...  TT_API_SECRET=...
scripts/run.sh live --config config.json --i-accept-the-risk
```

Only move to the next step when the current one holds up. If paper results differ a lot from the backtest, find out why before going live.

### Strategies

| Name | Idea | Character |
|---|---|---|
| `trend` | Buy a breakout above the 55-bar high; exit below the 20-bar low or on a 3×ATR trailing stop. Set `allow_short` for perps. | ~35–45% win rate, big winners |
| `ema` | Long while the fast EMA is above the slow EMA, with an ATR stop. | Similar; more trades |
| `rsi` | Buy RSI(3) < 15 dips only above the 200-bar SMA; exit on a bounce or after 10 bars. | High win rate; **lost money after costs here** |

Add your own by subclassing `Strategy` in `tt/strategies.py`. Implement `prepare()` (compute indicators once) and `signal(i, side, bars_held)`. The same class then works in the backtest, the paper bot and the live bot.

### Risk controls (in `config.json` → `risk`)

| Setting | Default | Meaning |
|---|---|---|
| `risk_per_trade` | 0.01 | If the stop is hit, lose 1% of equity. This sets position size. |
| `max_leverage` | 1.0 | Position value can't exceed equity × this. |
| `max_daily_loss` | 0.03 | After −3% in a UTC day, no new entries until tomorrow. |
| `max_drawdown` | 0.20 | At −20% from peak, close everything and stop. To re-enable, delete `state/bot.json`, and only after you have understood why. |

---

## Copy trading

The engine copies **positions, not orders**. On every poll it reads what the leader holds, scales that to each follower's equity, and trades only the difference. A missed poll, phone restart or rejected order therefore fixes itself on the next sync instead of leaving a follower out of step.

```
 your strategy bot ──► state/leader.json ──┐
 (or an exchange account, read-only key) ──┴─► copy engine ──► follower 1 (Bybit sub-account)
                                                            ├─► follower 2 (Binance futures)
                                                            └─► follower 3 (paper)
```

```bash
cp copy.example.json copy.json   # edit leader + followers
python -m tt copy --config copy.json --once     # dry run: prints the orders it *would* send
# when happy: set "dry_run": false
scripts/run.sh copy --config copy.json --i-accept-the-risk
```

**Leader types:**

- `"type": "feed"` reads a JSON file or HTTPS URL of the form `{"equity": ..., "positions": {...}, "ts": ...}`. `tt`'s own bot writes this to `publish_file`, so one strategy bot can drive many accounts. A feed older than `max_age_s` is refused.
- `"type": "exchange"` reads an exchange account through a **read-only** API key.

**Each follower** has its own settings:

- `multiplier`: copy at 0.5×, 2× and so on, relative to equity.
- `max_leverage`: cap on total position value.
- `min_notional`: skip tiny adjustments.
- `symbols`: a whitelist.
- Its own daily-loss and drawdown kill switch (`on_halt`: `flatten` or `freeze`).

**Copy-trading rules:**

- Use a **dedicated sub-account** per follower. The engine treats everything held in a copied symbol as the copied position. On a spot account it would sell coins you already owned.
- Copying a stranger's account needs their read-only API key, given with their consent. Exchanges' built-in copy trading (Bybit, Bitget, Binance, OKX) is the simpler route for that. Before you follow anyone, check their max drawdown and whether they use stops.
- **Copying for other people** — taking their money or keys and trading for them, or selling signals as an execution service — is regulated investment management in most countries. Don't do it without a licence.

---

## Keeping it running on Android

Android kills background apps to save battery. To keep the bot alive:

1. Settings → Apps → Termux → Battery → **Unrestricted** (disable battery optimisation).
2. Use `scripts/run.sh` (it holds `termux-wake-lock` and restarts on crash) inside `tmux`:
   `tmux new -s tt 'scripts/run.sh paper --config config.json'`. Detach with `Ctrl-b d`; reattach with `tmux attach -t tt`.
3. Optional: install **Termux:Boot** and copy `scripts/boot-tt.sh` to `~/.termux/boot/` to auto-start after a reboot.
4. Alerts: install **Termux:API** for phone notifications, and/or set `TT_TELEGRAM_TOKEN` and `TT_TELEGRAM_CHAT` for Telegram messages.

**Be aware:** stops are enforced by the bot polling every `poll_seconds`, not by orders resting on the exchange. If the phone dies or loses signal, positions are unprotected until it's back. Paper trading on a phone is fine. For real money, run the same code on an always-on machine (a cheap VPS, an old laptop or a Raspberry Pi), or keep leverage at 1× so a gap can't liquidate you.

## API key safety

- Create keys with **trading only, withdrawals disabled**. Add an IP whitelist if your exchange supports it.
- Keys live in environment variables, never in `config.json`. Real configs, state and logs are git-ignored.
- One key per bot or follower, so you can revoke one without stopping the others.

## Scope and extending

- **Crypto** (spot and perps, 100+ exchanges) works now via `ccxt`.
- **US stocks**: Alpaca has a free paper-trading REST API. Adding it means a broker class with the same 6 methods as `CCXTBroker`.
- **Forex**: OANDA has a REST API (same approach). MetaTrader 5's Python package is Windows-only, so it can't run in Termux.
- Taxes: every trade can be a taxable event. Keep the fill logs (`state/`).

## Development

```bash
python -m unittest discover -s tests -t .
```

Layout: `tt/data.py` (candles, download), `bybit.py` (Bybit public data, stdlib), `check.py` (pre-flight), `indicators.py`, `strategies.py`, `risk.py`, `backtest.py` (backtest, walk-forward, Monte Carlo), `broker.py` (paper + ccxt), `live.py` (bot), `copytrade.py`, `notify.py`, `__main__.py` (CLI).

*Nothing here is financial advice. Trading can lose more than you expect. Only trade money you can afford to lose.*
