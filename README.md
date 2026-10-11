# tt — a trading and copy-trading toolkit that runs on your phone (Termux)

**Website:** https://tt1-rouge.vercel.app (interactive results dashboard)

`tt` is a small Python toolkit for Android/Termux. It lets you:

1. **Download** market data (no API key needed).
2. **Backtest** strategies with realistic costs: fees, slippage, next-bar fills and stop gaps.
3. **Walk-forward test** them. Parameters are optimised on past data and judged only on unseen data.
4. **Paper trade** on live prices with a simulated account.
5. **Trade live** on 100+ crypto exchanges through `ccxt`, spot or perpetual futures.
6. **Copy trade**: mirror a leader's positions into any number of follower accounts, each with its own risk limits.
7. **Sports prediction markets** (Polymarket and Kalshi): match today's games across both venues, scan for whale trades, and run a stink-bid bot, [tested first](#sports-prediction-markets-whale-scanner-and-stink-bids-polymarket--kalshi).

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
| `scalp` | For 1–15 minute candles. `mode: "bounce"` buys a close below the lower Bollinger band in an uptrend. `mode: "break"` buys a new 20-candle high. Both exit at a 1×ATR target, a 1×ATR stop, or after 12 candles. | Hundreds to thousands of trades a year; **fees beat it, see below** |

Add your own by subclassing `Strategy` in `tt/strategies.py`. Implement `prepare()` (compute indicators once) and `signal(i, side, bars_held)`. The same class then works in the backtest, the paper bot and the live bot.

### Scalping: tested, and it loses

The `scalp` strategy was tested on real Binance candles:
- **Data:** one year of 5-minute candles for BTC, ETH and SOL (Oct 2025 to Oct 2026), plus 90 days of 1-minute BTC.
- **Cost levels:** four, from Bybit spot market orders down to zero fees.
- **Walk-forward:** the cheapest real cost level was also tested on unseen data.

| Cost level | Bounce, BTC 5m | Break, BTC 5m |
|---|---:|---:|
| Bybit spot, market orders (0.1% fee) | **−68%** | **−100%** |
| Bybit perps, market orders (0.055%) | −71% | −100% |
| Bybit perps, limit orders (0.02%) | −28% | −98% |
| No fees at all | +8% | −54% |

- **The edge is smaller than any fee.** "Bounce" catches about +0.008% per trade on BTC and +0.015% on SOL before costs. A limit-order round trip costs 0.04%, and a spot market-order round trip costs 0.2%. "Break" loses even with no fees.
- **Unseen data confirms it.** Re-tuned on the past and traded on the next unseen period, it was profitable in **0 of 32** walk-forward periods (both modes, 4 datasets, 4 periods each).
- **The pattern holds everywhere.** ETH, SOL and 1-minute BTC show the same thing.
- **Before trusting any scalper,** run `python -m tt backtest --data <5m csv> --strategy scalp --fee <your fee>`. It prints, per trade, what the market gave and what costs took.
- **It's in the toolkit and the live demo so you can watch it,** not because it works. `config.scalp.example.json` runs it in paper mode only.

### Other short-term strategies: tested, none held up

Four more short-term families were also tested, with settings chosen on Oct 2020 to Oct 2024 and judged once on the untouched Oct 2024 to Oct 2026:
- volatility breakout;
- 1-hour trend breakout;
- 1-hour EMA cross;
- funding carry.

Three lost money after fees. Funding carry stayed positive but earned about 0.15% a year, because funding has been low since 2024. Over the same two years the 4-hour trend breakout was positive on BTC, ETH and SOL. Full method and results: [docs/SHORT_TERM_RESEARCH.md](docs/SHORT_TERM_RESEARCH.md).

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

## Sports prediction markets: whale scanner and stink bids (Polymarket + Kalshi)

This builds the system from [Moon Dev's 10 October 2026 stream](https://x.com/MoonDevOnYT/status/2108988433062355089) ("a bot that exploits Polymarket and Kalshi to steal massive discounts from panicking whales"). His rules, from the video:

1. **Stink bids.** On every game, rest a limit buy on each team **30% below** its current price, **$2** each.
2. **Cancel at halftime.** Bids stay up before the game and through the first half; unfilled ones are cancelled when the second half starts.
3. **Hold.** A filled bid is held to settlement.
4. **Hedge (his "third unlock").** When a whale's dump fills your bid on one venue, buy the *other* team on the *other* venue, so you profit whoever wins.

**Tested first. It loses as specified.** The rule was replayed on 7,852 settled Kalshi games (NBA, NFL, NHL, MLB and college football, April 2025 to October 2026):

| Rule: 30% below, $2, until halftime | Result |
|---|---|
| Bids placed / filled | 15,450 / 7,952 (51%) |
| Filled before kick-off (the "panicking whale" case) | **17** |
| Filled during play, as the game turned against the team | 7,935 |
| Filled team went on to win | 30%, at an average price of 31¢ |
| Return on money staked, after Kalshi fees | **−6.3%** (95% range −9.4% to −3.0%) |
| Same with no fees (a maker on Polymarket) | −5.0% |
| Other discounts (10–50%) and windows | 15 of 15 lost money |

- **The whale case exists, but rarely.** A resting bid gets filled mostly by news (a score, an injury) and only rarely by a whale. The 1 fill in 5 where the price bounced back within 10 minutes made +49%. The 4 in 5 where it kept falling lost −20%, and you can't tell them apart when the fill happens.
- **The hedge rarely helps.** At the moment of a fill, the other venue's price shows which case it is. In 9% of NFL and NBA fills, Polymarket hadn't moved: a one-venue whale dip, which made +29% held (or a locked profit hedged). In the other 91%, Polymarket had moved too, because it was news. Those lost −9.3%, and no hedge exists for them.
- **So the bot is built and runs, but treat it as a paper-trading experiment, not an income source.** The one lead in the data is a different design: buy only when one venue is cheap *and* the other hasn't moved, rather than resting blind bids. That isn't tested yet.

Full method and numbers: [docs/STINK_BID_RESEARCH.md](docs/STINK_BID_RESEARCH.md).

### Dashboard

**https://tt1-rouge.vercel.app/sports** (`site/sports.html`, live once this is merged to the default branch) shows the system live in the browser, read-only:
- **Live board:** today's games with both venues' prices, where each stink bid would sit, and whether bids are still up (they're struck through after halftime).
- **Cross-venue lock:** what buying the two teams on opposite venues would lock in after fees.
- **Whale feed:** big trades next to the other venue's price for the same team.
- **The study:** the backtest numbers above.

Kalshi refuses browser requests, so the page reads it through a small read-only proxy (`site/api/kalshi.js`, a Vercel function that allows only public market-data endpoints).

### The three tools

```bash
# Today's games matched across both venues, each team's price on each, and
# what buying the two teams on opposite venues would lock in after fees
python -m tt games --leagues nfl,nba --live

# Whale scanner: trades >= $1,000 on today's games, both venues, every 15 s.
# Each row shows the other venue's price for the same team: a SELL at 0.18
# next to "K 0.23/0.24" is a dump on one venue, not news.
python -m tt whales --leagues nfl,nba,mlb,nhl --min-usd 1000

# Stink-bid bot. Paper trading is the default and needs no account.
cp stink.example.json stink.json
python -m tt stink --config stink.json --dry-run   # list the bids it would place now
scripts/run.sh stink --config stink.json           # run it (paper), restarts on crash
python -m tt stink --config stink.json --status    # P&L, resting bids, open positions
```

No API keys are needed for `games`, `whales` or paper trading. Leagues: `nfl`, `ncaaf`, `nba`, `wnba`, `ncaab`, `mlb`, `nhl` (game-winner markets).

**How the bot works:**
- **Games and clock.** ESPN's public scoreboard supplies teams, start times and the period. Each venue's market is matched to the game team by team; a match must be unambiguous, or the game is skipped.
- **Reference price.** The bid sits `discount` below the average of the two venues' mid prices. Before the game it moves if that price drifts more than `reprice`; during the game it stays put.
- **Halftime cancel, with a backstop.** Bids are cancelled when ESPN reports the first half over (`half_period`: quarters 2, college basketball 1, NHL 1, MLB 5 innings; `0` = pre-game only). Every bid also carries an expiry on the exchange about 15 minutes after a typical halftime, so it lapses even if your phone dies.
- **Hedge.** With `hedge.enabled`, for `window_s` seconds after a fill the bot looks for the other team on the other venue. It buys there only if that locks at least `min_locked_edge` per share after both venues' fees. Otherwise it holds, because the other venue moving too means news, not a whale.
- **Paper fills are strict.** A paper bid fills only when the market trades *below* it, or its ask drops to it. A trade exactly at your price isn't a fill, because in reality you'd be behind everyone already waiting there.
- **Caps.** `max_total_usd` limits money in bids plus positions; `max_orders` limits resting bids. With hedging on, bids are at least 5 contracts (Polymarket's minimum), so a hedge can match.

### Going live (only after paper trading)

- **Kalshi:** create an API key (Account & security → API Keys). Then `export KALSHI_KEY_ID=...` and `export KALSHI_PRIVATE_KEY_PATH=~/kalshi.key`, and install `pkg install python-cryptography` on Termux (`pip install cryptography` elsewhere). Test on the demo exchange first: `"kalshi_env": "demo"` with a demo.kalshi.co account.
- **Polymarket:** `pip install polymarket-client` (it needs pydantic and eth-account; on a phone that may mean `pkg install rust` and a long build, so a VPS is easier). Then `export POLY_PRIVATE_KEY=...` and `POLY_WALLET=<your Polymarket address>`. Make one small trade on polymarket.com first so the wallet's approvals are set. That private key controls the wallet, so keep only what you're trading in it.
- `python -m tt stink --config stink.json --check` verifies keys and balances without trading. Then set `"mode": "live"` and run with `--i-accept-the-risk`.
- The live order code follows each venue's current API documentation and official SDK (October 2026). It is unit-tested against canned responses, but it has **not** been run against a real account. Start with one league, `$2` bids and a low `max_total_usd`.

**Before you put money in:**
- **Can you use them from Kenya?** Polymarket's geoblock list (October 2026) doesn't restrict Kenya. Kalshi accepts many non-US countries; its Member Agreement lists which, and sign-up checks your country. Kenya's Gambling Control Act 2025 doesn't mention prediction markets, so whether sports contracts count as betting is unsettled. Treat the gains as taxable and keep the logs (`state/stink.json`, `logs/`).
- **Settlement rules differ.** A hedge only locks a profit if both venues settle the game the same way. Ties settle at 50/50 on both for the NFL. Postponements differ: Kalshi pays a "fair price" if a game doesn't start within 48 hours; Polymarket waits or pays 50/50. Read both rules for each league you hedge.
- **The hedge can fail.** Its order is fill-or-kill at the price that locks the profit. If the book moved, it doesn't fill and you hold an unhedged position.

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

## Website

The project site (https://tt1-rouge.vercel.app) lives in `site/`: a static page with no build step, deployed on Vercel from this repo. Every push to the default branch redeploys it.

It includes a **live 10 USDT paper-trading demo** in two parts:
- **Your bot:** pick a market and a strategy (scalper on 5-minute candles, or trend/EMA on 4-hour), then press **Start**. It trades 10 USDT of fake money live from that moment. **Stop** sells anything it holds at the live price and freezes the result. The run is saved in your browser (`localStorage`) as just its start time, start price and choices. Everything else is replayed from candles, so it keeps going while the page is closed. The last five runs are listed.
- **Public accounts:** trend and EMA bots on BTC, ETH and SOL, running since 11 October 2026 at 00:00 UTC, next to a buy-and-hold benchmark. They can't be stopped, and every visitor sees the same accounts.
- **How it works.** The account is rebuilt in the visitor's browser from public price history since the start time. Every visitor sees the same account, and it needs no server or database. The live price streams over Binance's WebSocket, with REST polling as a fallback.
- **Same rules as the bot.** `site/js/engine.js` is a port of the Python engine. `tests/test_site_engine.py` checks that it makes identical trades (it needs Node.js and is skipped without it).

To refresh the backtest numbers after downloading new data with `tt fetch`, run:

```bash
python scripts/build_site.py      # rewrites site/data/results.json (and scalp.json if 5m data exists)
```

## Development

```bash
python -m unittest discover -s tests -t .
```

Layout: `tt/data.py` (candles, download), `bybit.py` (Bybit public data, stdlib), `check.py` (pre-flight), `indicators.py`, `strategies.py`, `risk.py`, `backtest.py` (backtest, walk-forward, Monte Carlo), `broker.py` (paper + ccxt), `live.py` (bot), `copytrade.py`, `notify.py`, `__main__.py` (CLI). Sports: `tt/sports/` (`kalshi.py`, `polymarket.py`, `espn.py`, `games.py` matching, `fees.py`, `whales.py`, `stink.py` bot, `venues.py` paper and live orders).

*Nothing here is financial advice. Trading can lose more than you expect. Only trade money you can afford to lose.*
