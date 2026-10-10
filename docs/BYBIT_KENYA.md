# Trading on Bybit from Kenya with `tt`

This is a practical setup guide, current as of **10 October 2026**. Rules in Kenya are changing fast, so re-check the legal section before you put money in.

---

## 1. Legal and tax situation: read before funding

**The deadline to watch is 4 November 2026.**

- **Licensing law.** Kenya's Virtual Asset Service Providers Act, 2025 (Act No. 20 of 2025) is now in force. Its implementing Regulations were gazetted on 22 July 2026 (Legal Notice No. 134 of 2026).
- **Who licenses exchanges.** The Capital Markets Authority (CMA). The Central Bank of Kenya covers wallets and payments.
- **Deadline for existing platforms.** Law firms report that platforms already serving Kenyans have until **4 November 2026** to comply. One guide says this includes offshore platforms that target Kenyan customers.
- **Bybit's status is unknown.** I found **no public confirmation that Bybit has applied for, or received, a CMA licence**. It may restrict Kenyan accounts, or change how it serves them, around that date. Check the CMA's list of licensed providers and Bybit's announcements.

**What that means for you:**

- Keep only your trading capital on the exchange. Withdraw profits regularly.
- Have a second exchange ready. `tt` talks to 100+ exchanges through ccxt, so moving is a change of `exchange` and `symbol` in your config. The strategies, risk rules and copy logic stay the same.

**Tax.**

- The 3% Digital Asset Tax from the Finance Act 2023 was **repealed by the Finance Act 2025**.
- It was replaced by a **10% excise duty on the fees** that licensed providers charge you, not on the size of your trades.
- The Finance Act 2026 clarified that scope. It also adds reporting duties for providers, who will send user and transaction records to KRA.
- Sources disagree on whether an individual's trading profits are taxed as income or capital gains. Keep every record: the bot's `state/` fill logs and Bybit's exports. Ask a KRA-registered tax adviser.

**Copy trading for other people is a licensed activity.** Managing or advising on other people's crypto counts as a "virtual asset manager / investment adviser" under the VASP Act, which needs a CMA licence. Copying your *own* sub-accounts is fine.

## 2. Account setup

1. **Turn on security:** finish KYC, then enable **2FA**, an **anti-phishing code** and a **withdrawal address whitelist**.
2. **Use a Unified Trading Account (UTA).** New accounts are UTA by default. `python -m tt check` confirms it and fails if not.
3. **Create sub-accounts** if you'll run more than one bot or use copy trading: one sub-account per bot or follower. A sub-account limits any mistake to its own balance. Spot copy trading also *requires* a dedicated account, because the engine treats every coin held in a copied symbol as the copied position.

### Funding with M-Pesa (P2P)

Bybit P2P supports M-Pesa for KES (reviews also list Airtel Money and bank transfer). P2P is where most Kenyan scams happen, so:

- Trade only with merchants that show a high completion rate and many orders.
- **Pay only inside an open P2P order.** Never send M-Pesa to someone from Telegram, WhatsApp or a "support agent".
- **When selling USDT,** confirm the money in your **M-Pesa app balance or statement** before releasing. Fake "M-Pesa confirmation" SMS messages and screenshots are the most common trick.
- Keep all communication in Bybit's order chat. That is what an appeal is judged on.

## 3. API keys

Create keys under **Account → API → Create New Key → System-generated**.

| Key for | Permissions |
|---|---|
| Spot bot | Read-Write, with **spot trading** permission only |
| Perpetuals bot | Read-Write, with **contract Orders + Positions** permission only |
| Copy-trade **leader** (an exchange account being copied) | **Read-Only** |
| Anything | **Never** tick Withdrawal. Don't tick transfers you don't need. |

### The IP problem on a phone

Bybit lets you bind a key to fixed IP addresses. Keys **without** an IP binding **expire after about 90 days**, according to several integration guides. Your phone's IP on Safaricom or Airtel data changes constantly, so you can't bind it. Your choices:

- **Phone:** accept the expiry and recreate the key about every 3 months. `python -m tt check` shows how many days the key has left.
- **VPS** (recommended once real money is involved): a small Linux server with a static IP. Bind the key to that IP and it stops expiring. The bot then also keeps running through phone battery, data or power outages. Run `python -m tt check` *from the VPS* before trading to confirm Bybit accepts its IP.

### Storing keys in Termux

Put your keys in a file only you can read, never in `config.json`:

```bash
cat > ~/.tt_env <<'X'
export BYBIT_API_KEY=xxxxxxxx
export BYBIT_API_SECRET=yyyyyyyy
export TT_TELEGRAM_TOKEN=...      # optional alerts
export TT_TELEGRAM_CHAT=...
X
chmod 600 ~/.tt_env
source ~/.tt_env          # add this line to ~/.bashrc to load it automatically
```

## 4. Spot or perpetuals?

These are backtests with Bybit's base-tier costs:

- **Spot:** 0.10% fee.
- **Perpetuals:** 0.055% taker fee, plus funding of 0.01% per 8h. That is Bybit's baseline rate, about 11% a year of position value, paid by longs.

All runs use 0.05% slippage, 1% risk per trade and 1x maximum leverage, over 4h candles from Oct 2020 to Oct 2026. Binance price history stood in for Bybit's because the build machine couldn't reach Bybit; the prices are nearly identical.

| Market | Setup | Trades | Return | Max drawdown | Sharpe |
|---|---|---:|---:|---:|---:|
| BTC | spot, trend, long only | 110 | +52.9% | 7.6% | 1.09 |
| BTC | perp, trend, long only | 110 | +50.1% | 7.6% | 1.05 |
| BTC | perp, trend, **long + short** | 197 | +51.7% | 10.3% | 0.88 |
| ETH | perp, ema, long only | 182 | +37.5% | 8.9% | 0.82 |
| ETH | perp, ema, **long + short** | 360 | +49.1% | 13.7% | 0.82 |
| SOL | perp, ema, long only | 181 | +70.2% | 10.8% | 1.03 |
| SOL | perp, ema, **long + short** | 372 | +60.7% | 11.5% | 0.82 |

- **Shorting didn't add risk-adjusted return.** It roughly doubled the number of trades and raised drawdowns.
- **Perps vs spot was a wash.** Lower fees were cancelled out by funding.
- **Minimum capital differs a lot.** For BTC/USDT, Bybit's minimum order is about 5 USDT on spot. On the perpetual it is 0.001 BTC, about 83 USDT at today's price. At 1% risk, a typical stop makes a position about 20–40% of equity. So the bot needs roughly **15–40 USDT for spot**, but **200–400 USDT for BTC perps**, or it will skip signals. `python -m tt check` calculates this from live data. Aim for a few times that figure so rounding and fees don't dominate.

**Recommendation:** start with **spot, BTC/USDT, 4h, `trend`** (`config.bybit-spot.example.json`). Spot can't be liquidated, pays no funding, and works with a small account. Move to perpetuals only for a specific reason (for example, you want shorts and have tested them), and keep `max_leverage` at 1.

## 5. Step by step on your phone

```bash
bash install_termux.sh --ccxt
source ~/.tt_env

# 1. Test on Bybit's own price history
python -m tt fetch --exchange bybit --symbol BTC/USDT --timeframe 4h --days 1460 --out data/bybit_btc_4h.csv
python -m tt walkforward --data data/bybit_btc_4h.csv --strategy trend
#    perps: include real funding, not just the 0.01% estimate
python -m tt fetch --exchange bybit --symbol BTC/USDT:USDT --timeframe 4h --days 1460 --out data/bybit_btcp_4h.csv
python -m tt fetch-funding --symbol BTC/USDT:USDT --days 1460 --out data/bybit_btcp_funding.csv
python -m tt backtest --data data/bybit_btcp_4h.csv --strategy trend --fee 0.00055 --funding-csv data/bybit_btcp_funding.csv

# 2. Paper trade on live Bybit prices (no key needed) for at least a month
cp config.bybit-spot.example.json config.json
python -m tt check --config config.json
tmux new -s tt 'scripts/run.sh paper --config config.json'

# 3. Bybit demo trading: real prices, real order handling, fake money
#    Switch to Demo Trading on bybit.com, create an API key *there*, request demo funds,
#    set "mode": "demo" in config.json and export that key, then:
python -m tt check --config config.json
tmux new -s tt 'scripts/run.sh live --config config.json'

# 4. Real money: a small amount, in a sub-account
#    set "mode": "live", use the real sub-account key, then:
python -m tt check --config config.json        # must say "All checks passed."
tmux new -s tt 'scripts/run.sh live --config config.json --i-accept-the-risk'
```

Use **demo trading**, not testnet. Demo uses mainnet prices with fake money. Testnet has its own thin, unrealistic prices.

## 6. Copy trading between your own Bybit accounts

`copy.bybit.example.json` wires a strategy bot (the leader, which writes `state/leader.json`) to a paper follower and a Bybit sub-account in demo mode.

- Each follower has its own key, `multiplier`, `max_leverage` and drawdown kill switch.
- It starts in `dry_run`. Run `python -m tt copy --config copy.json --once` and read the printed orders before switching `dry_run` off.
- Bybit also runs its own copy-trading platform for following other traders. If you use it, check a leader's **maximum drawdown** and whether they use stops. A smooth equity curve with no losing months often means martingale or no stop-loss.

## 7. Kenya-specific practicalities

- **Times:** 4h candles close at **03:00, 07:00, 11:00, 15:00, 19:00 and 23:00 Nairobi time** (EAT, UTC+3). The daily-loss limit resets at **03:00 EAT** (midnight UTC).
- **Keep the phone alive:** set Termux's battery use to *Unrestricted*, and run the bot in `tmux` via `scripts/run.sh` (it holds a wake lock).
- **Data loss and power cuts:** stops are checked by polling, so while the phone is offline your positions aren't protected. That's one more reason to start on spot at 1x, and to use a VPS for real money.
- **Currency:** everything runs in USDT. Your KES result also moves with the USD/KES rate and P2P spreads.

## Sources

- Oraro & Company, [Virtual Asset Service Providers Regulations, 2026](https://www.oraro.co.ke/virtual-asset-service-providers-regulations-2026/)
- Spencer West, [Kenya's VASP Regulations 2026: framework now operational](https://www.spencer-west.com/news/kenyas-virtual-asset-service-providers-regulations-2026-regulatory-framework-now-operational/)
- Global Law Experts, [VASP licensing in Kenya: 2026 update](https://globallawexperts.com/vasp-licensing-kenya/)
- EY, [Kenya enacts Finance Act, 2026](https://www.ey.com/en_gl/technical/tax-alerts/kenya-enacts-finance-act-2026)
- Deloitte Kenya, [Excise duty meets crypto](https://www.deloitte.com/ke/en/services/tax/analysis/excise-duty-meets-crypto-the-legislative-gaps-kenya-must-close.html)
- Bybit API docs: [demo trading](https://bybit-exchange.github.io/docs/v5/demo), [API key info](https://bybit-exchange.github.io/docs/v5/user/apikey-info), [instruments info](https://bybit-exchange.github.io/docs/v5/market/instrument), [kline](https://bybit-exchange.github.io/docs/v5/market/kline), [funding history](https://bybit-exchange.github.io/docs/v5/market/history-fund-rate)
- Bitsgap, [Bybit trading fees explained](https://bitsgap.com/blog/bybit-trading-fees-explained-what-it-costs)
- Waltio, [Bybit API key setup](https://help.waltio.com/en/articles/5960015-bybit-api) (key expiry without IP binding)

*Not financial, legal or tax advice.*
