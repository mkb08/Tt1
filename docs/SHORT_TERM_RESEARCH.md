# Is there a short-term strategy that makes money after fees?

**Short answer: none of the four short-term strategies tested did, on data they hadn't seen.** That's out of four families and 16 settings, on BTC, ETH and SOL, after Bybit's fees.

- Three of the four lost money.
- The fourth, funding carry, stayed positive but earned about 0.15% a year.
- The only strategy that held up over the same untouched two years was the slower **4-hour trend breakout** already in the toolkit.

Study run on 11 October 2026. Reproduce it with `scripts/research_short_term.py`.

## How the test was set up (fixed before looking at results)

- **Data:** Binance hourly candles for BTC, ETH and SOL, Oct 2020 to Oct 2026. Binance's own archive supplied the perpetual-futures prices and **every historical funding payment** (6,500+ per coin).
- **Two periods:**
  - *Development* (Oct 2020 to Oct 2024) was used to pick settings, via three sequential walk-forward periods.
  - *Holdout* (Oct 2024 to Oct 2026) was locked away and used **once**, with the settings chosen on development data. Its pass rule and script were written before the development results came in.
- **Same settings for all three coins:** no per-coin tuning.
- **Costs:** Bybit's 0.1% spot fee and 0.055% perpetual fee, 0.02% slippage on every fill, and real funding payments.
- **Pass rule:** in the holdout, net profit on at least 2 of 3 coins, a positive average, and an average Sharpe ratio above 0.5.
- **Settings tested:** 16, on 3 coins each, so 48 development runs. Testing more ideas makes it more likely one looks good by luck, so this count is reported on purpose.

The four families, each with published evidence or long practitioner use behind it:

| Strategy | Rule | Typical hold | Settings tried |
|---|---|---|---|
| Volatility breakout | Buy the first hourly close above today's open + k × yesterday's range; stop at today's open; sell at the next day's open. Optional trend filter. | hours | k ∈ {0.4, 0.6, 0.8} × filter on/off |
| Trend breakout, 1 h | The toolkit's `trend` on hourly candles | hours to days | 48 / 96 / 168-hour breakout |
| EMA cross, 1 h | The toolkit's `ema` on hourly candles | hours to days | 24/96, 48/192, 96/384 |
| Funding carry | Buy spot and short the perpetual (market-neutral) while the 3-day average funding rate is above a threshold; collect funding | days | threshold 0.01% or 0.02% per 8 h × 1- or 3-day average |

## Results on the untouched holdout (Oct 2024 to Oct 2026)

| Strategy (setting chosen on development data) | BTC | ETH | SOL | Verdict |
|---|---:|---:|---:|---|
| Volatility breakout (k 0.8, trend filter) | −9.9% | +3.3% | −7.4% | **Fail** |
| Trend breakout, 1 h (96 h) | −10.6% | +5.4% | −18.9% | **Fail** |
| EMA cross, 1 h (24/96) | −22.5% | −36.6% | −24.7% | **Fail** |
| Funding carry (3-day avg > 0.01%/8h) | +0.3% | +0.5% | +0.1% | Passes the rule, but worthless in practice: about 0.15% a year |
| *For context:* 4 h trend breakout (toolkit default, not tuned here) | +11.6% | +16.8% | +1.5% | Positive on all three |
| *For context:* buy & hold | +38% (fell 54%) | +5% (fell 69%) | −21% (fell 79%) | |

The settings that looked best on development data then failed on the holdout. That's the usual sign of curve-fitting: development gains came from luck and from coin-specific runs (for example, SOL's big moves in the development years), not from a lasting edge.

## Why short-term trading loses here: fees versus the move per trade

The holdout run records, per trade, how much the market moved in the bot's favour and how much costs took:

| Strategy | Move caught per trade (BTC / ETH / SOL) | Cost per trade |
|---|---|---|
| EMA cross, 1 h | +0.04% / −0.15% / −0.17% | 0.20% |
| Trend breakout, 1 h | +0.07% / +0.15% / −0.19% | 0.20% |
| Volatility breakout | +0.04% / **+0.51%** / +0.19% | 0.20% |
| Scalper, 5 min (earlier study) | about +0.01% | 0.20% |

Bybit spot costs about 0.2% per round trip, whatever the timeframe. The shorter the hold, the smaller the move per trade, so fees take a bigger share. Only volatility breakout on ETH caught enough movement to clear costs, and one coin out of three isn't an edge.

## Funding carry: a real edge that only pays in hot markets

Carry stayed positive on every coin in both periods with tiny drawdowns. That's because it doesn't bet on direction: it collects the fee that leveraged buyers pay short sellers. The catch is that it only pays well when funding is high:

| Year | Funding paid to shorts (annualised, BTC / ETH / SOL) | Carry result on the whole account |
|---|---|---|
| 2021 | +31% / +38% / +29% | +5.0% / +12.6% / +6.6% |
| 2022 | +4% / +1% / −36% | ~0% (it stayed out) |
| 2023 | +8% / +8% / +1% | ~0% |
| 2024 | +12% / +13% / +14% | +2.4% / +2.4% / +3.8% |
| 2025 | +5% / +5% / +0.4% | ~0% |
| 2026 (to Sept) | +3% / +2% / −1% | ~0% |

The last 30 days in the archive (to 30 Sept 2026) paid about 5.5%, 4.9% and 3.2% a year, below the level where carry is worth its fees.

To make money, carry also needs:
- both a spot and a perpetual position;
- enough money to meet Bybit's perpetual minimums (about 83 USDT for BTC);
- an exchange you trust with both legs.

## What this means

1. **Don't trade short-term on Bybit spot with market orders.** A real edge has to beat 0.2% per round trip, and none of the tested ideas did, from 1-minute scalping up to 1-hour trend following.
2. **The shortest timeframe that worked was the 4-hour trend breakout:** positive on all three coins in the holdout, with much smaller drops than buy and hold. It holds trades for days, not minutes.
3. **Funding carry is worth watching, not running, at today's funding levels.** When funding climbs back above about 0.01% per 8 hours (roughly 11% a year), it becomes a low-risk way to earn.
4. **Lower costs are what would change the picture:** VIP fee tiers or limit orders. Even then, the scalping study found the edge (+0.008% per trade) smaller than limit-order fees (0.04% per round trip).

## Reproduce

```bash
python -m tt fetch --symbol BTC/USDT --timeframe 1h --days 2192 --out data/btc_1h.csv   # also eth, sol; and 4h
python scripts/fetch_binance_archive.py /tmp/binance_archive data                       # funding + perp prices
python scripts/research_short_term.py dev        # picks settings on Oct 2020 to Oct 2024
python scripts/research_short_term.py holdout    # tests them once on Oct 2024 to Oct 2026
```

Results are saved in `research/short_term_dev.json` and `research/short_term_holdout.json`.

*Past results don't predict future ones. Not financial advice.*
