# Do sports stink bids catch panicking whales?

**Short answer: no. The rule from the video lost money, and the result isn't close.**

- **Size:** 15,450 bids on 7,852 games (NBA, NFL, NHL, MLB and college football, April 2025 to October 2026).
- **When they filled:** 51% filled. Only 17 of the 7,952 fills came before kick-off, where a panicking whale would be the reason. The rest filled during play, as the game turned against that team.
- **Result:** fills won 30% of the time at an average price of 31¢. That is a return of **−6.3%** on the money staked after Kalshi's fees, with a 95% range of −9.4% to −3.0%. With no fees at all, as for a maker on Polymarket, it is still −5.0%.
- **Every variant lost:** 15 of 15 combinations of discount and window.
- **The hedge rarely existed.** In 9% of NFL and NBA fills, Polymarket hadn't moved, so buying the other team there would have locked a profit. Those were the real "whale" dips, and they made money either way. In the other 91%, Polymarket had moved too: the drop was news. Those lost −9.3%, and no hedge can rescue them.

Study run on 11 October 2026. Reproduce it with `scripts/research_stink_bids.py`.

## The idea being tested

Moon Dev's [10 October 2026 stream](https://x.com/MoonDevOnYT/status/2108988433062355089) argued that order books on Polymarket and Kalshi make something new possible in sports betting:

> "Lakers yes is trading at 65 cents. You place a limit buy at 52 cents. Your bid sits on the book … a whale has 10,000 Lakers shares and needs to exit now … the price walks down through the book and your 52 cent bid gets filled. You just bought Lakers yes at a 20% discount."

The bot he specified (at 41:40–44:24 of the stream):
- **Every game.** Take every NBA game tonight.
- **The bid.** A limit buy 30% below the current price, $2 each, on both teams.
- **The window.** Keep bids up through the 1st and 2nd quarters, then cancel all unfilled ones.
- **Fills.** Hold them to settlement; no selling.

He then added a cross-venue version. Rest stink bids on both venues; when a whale's dump fills one, immediately buy the other side on the other venue.

The stream never showed the bot filling or making money. This study checks it on data.

## How it was tested (fixed before looking at results)

- **Data:** every settled Kalshi game-winner market with a known start time, in two parts.
  - The live API: August to October 2026.
  - Kalshi's archive: one season back, from September 2025.
  - Leagues: NFL, college football, NBA, NHL and MLB.
  - Inputs: 1-minute candles (trade low, bid and ask) from 2 h 10 min before each start to 5 hours after it; real start times from Kalshi's game schedule (`/milestones`); each market's result.
- **The rule, exactly as in the video:**
  - **Reference price:** the mid between best bid and ask, 2 hours before the start.
  - **The bid:** a limit buy 30% below that price, rounded down to the cent, $2 of contracts, on both teams.
  - **Cancel time:** halftime. Kalshi's history has no game clock, so halftime is estimated from the start: NFL +100 min, college football +110, NBA +75, NHL +65 (mid 2nd period), MLB +90 (about the end of the 5th inning).
  - **Settlement:** fills are held to the end.
  - **Skips:** markets without a two-sided quote at the reference time, or with a spread wider than 10 cents.
- **What counts as a fill.** A trade must print *below* the bid. A trade *at* the bid may not reach you, because everyone already waiting at that price is ahead in the queue. The optimistic count ("touch") is reported too.
- **Costs:** Kalshi's maker fee on every fill: 0.0175 × contracts × p × (1 − p), rounded up to the cent, halved for MLB. The "gross" column has no fee, which is what a maker pays on Polymarket.
- **Pass rule:** the rule needed all three of:
  - net profit after fees;
  - a 95% bootstrap interval on return-on-cost entirely above zero (resampling whole games);
  - profit in both the earlier and the later half of the period.
- **Context only:** 5 discounts × 3 windows (pre-game only, until halftime, whole game). Fifteen variants means one could look good by luck, so none of them can pass or fail the idea.
- **The hedge (step 3).** For each fill on NFL and NBA games, the matching Polymarket market's minute price history was read at the fill. The question: would buying the other team there have locked at least 2 cents a share after both venues' fees? A half-cent spread was assumed on top of Polymarket's mid. Fills on Polymarket itself can't be tested: its history has no intraminute lows.

## Results

### The rule as specified: 30% below, until halftime

| | Bids | Fills | Fill rate | Fill's team won | Avg price paid | Return after fees | 95% range |
|---|---:|---:|---:|---:|---:|---:|---:|
| **All** | 15,450 | 7,952 | 51% | 30% | 31¢ | **−6.3%** | −9.4% to −3.0% |
| Earlier half (to 29 Mar 2026) | 7,733 | 3,766 | 49% | 30% | 30¢ | −4.9% | −10.0% to +0.2% |
| Later half | 7,717 | 4,186 | 54% | 31% | 32¢ | −7.5% | −12.0% to −3.1% |
| Fills counted on a touch (optimistic) | 15,450 | 8,392 | 54% | 31% | 31¢ | −2.5% | −6.5% to +1.9% |

**It fails the pre-registered rule:** the loss is significant, and both halves lost.

Over the whole sample, $14,828 went into fills. That lost $744 before fees and $936 after Kalshi's $192 of maker fees.

### Where the fills came from

| | Fills | Team won | Avg price | Return after fees |
|---|---:|---:|---:|---:|
| Before kick-off | 17 | 41% | 30¢ | −7.4% (too few to judge) |
| During play | 7,935 | 30% | 31¢ | −6.3% |
| Price bounced back within 10 min* | 1,588 | 46% | 31¢ | **+49.3%** |
| Price kept falling* | 6,364 | 26% | 32¢ | **−20.2%** |

\*Sorted using what happened *after* the fill, so a bot couldn't do this live. It shows where the money goes.

- **The whale case is real, but rare.** About 1 fill in 5 was a real liquidity shock: the price snapped back, and those fills made money.
- **News is the common case.** The other 4 in 5 were news: a score, an injury, momentum. The price kept falling after the fill, and the team won less often than the price paid implied.
- **Whales almost never cause the fill.** A whale dumping before the game almost never pushes a price 30% down. Liquid sports books absorb it.
- **The result is adverse selection.** A resting bid gets filled mostly when it shouldn't be.

### By league

| League | Bids | Fills | Team won | Avg price | Return after fees | 95% range |
|---|---:|---:|---:|---:|---:|---:|
| NBA (2025-26 season + preseason) | 2,780 | 1,326 | 27% | 28¢ | −6.6% | −16.1% to +2.8% |
| NFL (2025 season + 2026 to date) | 786 | 428 | 30% | 30¢ | +1.8% | −13.3% to +17.5% |
| NHL | 3,148 | 1,362 | 33% | 33¢ | −2.6% | −9.4% to +5.0% |
| MLB | 5,680 | 3,356 | 32% | 34¢ | −6.3% | −9.9% to −2.8% |
| College football | 3,056 | 1,480 | 27% | 27¢ | −11.7% | −21.0% to −1.5% |

The NFL is the only league above zero, and its range runs from −13% to +18%. With five leagues and fifteen variants, one positive league is what luck alone would produce.

### Other discounts and windows (context only)

All 15 lost money after fees:

| Discount | Pre-game only | Until halftime | Whole game |
|---|---:|---:|---:|
| 10% | −15.9% (187 fills) | −8.1% | −8.7% |
| 20% | −43.2% (53 fills) | −7.4% | −8.3% |
| 30% | −7.4% (17 fills) | **−6.3%** | −7.4% |
| 40% | −5.5% (6 fills) | −7.0% | −8.1% |
| 50% | −1.3% (4 fills) | −6.8% | −6.7% |

### The hedge (step 3)

For each Kalshi fill in NFL and NBA games, the other team's price on Polymarket at that minute was checked. Of 1,754 fills, 1,637 had a matching Polymarket game.

| | Fills | Team won | Held to the end | If hedged |
|---|---:|---:|---:|---:|
| Hedge available (lock ≥ 2¢ a share after fees) | 152 (9%) | 41% | **+28.9%** (+$82) | locked +$58, median 3.8¢ a share |
| No hedge: Polymarket had moved too | 1,485 (91%) | 26% | **−9.3%** (−$260) | – |
| All matched fills | 1,637 | | −5.8% (−$178) | hedge when possible: −$202 |

- **What the check reveals.** At the moment a bid fills, the other venue's price tells a one-venue dump (a whale) from news.
- **Whale dips are good buys, but rare.** They come up about 1 time in 11, and are worth buying with or without the hedge.
- **News is the rest.** Most fills are news, and those losses are already made by the time the bid fills.
- **Why hedging makes the total slightly worse:** it gives up part of the good fills' upside and doesn't touch the bad ones.

The one lead this points to is a different bot, not tested here. It would rest no blind bids and only buy when one venue is well below the other, i.e. the other venue confirms there's no news. It would pay taker fees (about 1.5¢ near 50¢) and buy at the ask rather than at the dip's low, so it needs its own test before any money. `tt games --live` and the whale scanner show those gaps as they happen.

## What this means

1. **Don't run the stink-bid bot for profit as specified.** Of each dollar it puts in, it loses about 6 cents after fees. It also concentrates fills in games that are turning against you.
2. **"Whale discounts" exist, but they're short, rare and hard to tell from news.** A one-venue dump that the other venue ignores is the profitable case. Even then, the gap has to cover two venues' fees: about 2–3.5¢ per $1 on today's NFL prices before any profit.
3. **The tools are still useful for watching.** The whale scanner shows big trades next to the other venue's price, and `tt games` shows the live cross-venue lock after fees. Use paper mode; if a real edge shows up, it will show up there first.
4. **Re-run it as data grows:** `python scripts/research_stink_bids.py fetch && python scripts/research_stink_bids.py report && python scripts/research_stink_bids.py hedge`.

## Limits of this study

- **Halftime is estimated from the start time.** The video's own rule uses the live clock, which the bot does too. The pre-game-only and whole-game windows bracket it, and all three lose.
- **One-minute candles.** A trade below the bid inside the minute counts as a fill, and the bid's position in the queue is ignored beyond requiring a trade *through* it.
- **Kalshi only for the fill test.** Polymarket's public history has no intraminute lows. Its maker fee is zero, which the "no fees" figure (−5.0%) covers.
- **Not covered:** 2025-26 college basketball, and seasons before mid-2025.
