# Market Lens Agent - Full Explanation Of Stock Selection, Technical Analysis, Entry Rules, And Portfolio Management

This document explains how Market Lens selects stocks for scanning, how each ticker is analyzed, how technical setups are detected, and what rules allow or block a simulated trade entry.

Market Lens Agent is paper trading only. It does not connect to a broker, does not place real orders, and does not use real money.

## High-Level Architecture

The system is built as a layered decision pipeline:

1. Smart Universe - selects quality stocks for scanning.
2. Basic Filters - removes low-quality, illiquid, unsuitable instruments.
3. Sector Health - evaluates the current strength of each sector.
4. Technical Scanner - calculates chart indicators and market structure.
5. Setup Detection - identifies actionable technical patterns.
6. Professional Context - adds market, trend, volume, liquidity, and event-risk context.
7. Agent Risk Layer - applies entry gates, exposure checks, net R/R, correlation, and risk rules.
8. Portfolio State - tracks cash, open positions, realized/unrealized P/L, and risk.
9. Position Monitor - monitors TP/SL events after entry.
10. Dashboard / Excel / JSONL - stores every decision for review and audit.

Manual user scans and Agent scans use the same core scanner, setup detection, and strategy decision layer. The difference is that the Agent also manages portfolio state, Excel tracking, GitHub Actions automation, Telegram alerts, and position monitoring.

## Stock Sources

The Agent does not rely on a small fixed list by default. It builds a broad dynamic Smart Universe.

Universe sources:

- S&P 500
- Nasdaq-100
- Russell 1000
- Russell 3000
- the app's curated sector dropdown lists
- broad Nasdaq screener fallback if Russell holdings are unavailable

Default source mode:

```text
MARKET_LENS_SMART_SOURCE=broad
```

The app attempts to pull external source lists from the web. If external sources are unavailable, it falls back to the curated in-app lists.

## Initial Stock Filters

Before a ticker is scored or scanned, it must pass basic quality filters.

Main filters:

- common US equities only
- excludes ETFs
- excludes warrants
- excludes rights
- excludes units
- excludes preferred shares
- excludes notes and bonds
- excludes acquisition corp / blank-check vehicles
- minimum price: `$10`
- 20-day average dollar volume: at least `$100M`
- minimum ATR%: `1.2%`
- maximum ATR%: `8.0%`
- broad-market minimum market cap: `$1B`
- broad-market minimum volume: `250,000` shares
- weak sectors are excluded from new scan candidates

Why these filters exist:

- avoid illiquid stocks
- avoid penny-stock behavior
- avoid non-common-equity instruments
- keep stocks with enough swing movement
- avoid buying technical patterns inside weak sectors by default

## Scan Size

Current cloud defaults:

```text
MARKET_LENS_AGENT_UNIVERSE_TARGET=100
MARKET_LENS_AGENT_UNIVERSE_POOL=100
MARKET_LENS_AGENT_UNIVERSE_MAX_POOL=300
MARKET_LENS_AGENT_MAX_PER_SECTOR=15
MARKET_LENS_AGENT_SCAN_BATCH_SIZE=20
```

Meaning:

- the target is up to 100 tickers per Agent scan
- candidates are selected from a wider ranked pool
- sector concentration is capped
- scanning runs in batches of 20 to reduce load on Render, GitHub Actions, and yfinance

Additional behavior:

- `WATCH` and `WATCH_READY` names are carried forward for up to 14 days.
- Watch names should not consume the normal quota for new candidates.
- `SKIP` names have an 8-hour cooldown to reduce repeated same-day scans of weak candidates.
- Open positions always remain in the scan/monitoring flow, even if Smart Universe would not select them again.

## Sector Health

Before final stock selection, the system evaluates sector health.

Representative ETFs:

- Technology - XLK
- Semiconductors - SMH
- Financials - XLF
- Healthcare - XLV
- Industrials - XLI
- Energy - XLE
- Consumer - XLY
- Communication Services - XLC
- Utilities - XLU
- Real Estate - XLRE
- Materials - XLB

Sector Health uses:

- ETF price versus EMA20
- ETF price versus EMA50
- EMA20 versus EMA50
- EMA50 slope
- 1-month return
- 3-month return
- relative strength versus SPY
- sector momentum

Sector score:

```text
trend_score * 45%
relative_strength_score * 35%
momentum_score * 20%
```

Classification:

- `Strong`: score >= 68
- `Neutral`: score >= 42
- `Weak`: score < 42

Practical effect:

- `Strong`: can receive more scan slots and may allow buys if all other rules pass
- `Neutral`: receives fewer names and requires cleaner setup quality
- `Weak`: no automatic buy; usually excluded from new scan candidates

## Smart Universe Stock Score

After initial filtering, each ticker receives a Smart Score.

Inputs:

- relative strength versus SPY and QQQ
- 1-month return
- 3-month return
- 6-month return
- trend score
- average dollar volume
- ATR%
- sector health

Relative Strength:

```text
relative_strength = (RS versus SPY * 65%) + (RS versus QQQ * 35%)
```

Trend Score:

- price above EMA20: +25
- price above EMA50: +25
- price above EMA200: +20
- EMA20 above EMA50: +20
- EMA50 rising versus 10 days ago: +10
- failed conditions receive negative penalties

Stock Score:

```text
RS Score * 32%
Trend Score * 30%
Momentum Score * 18%
Volume Score * 12%
Volatility Score * 8%
```

Final Smart Score:

```text
stock_score * 78% + sector_health_score * 22%
```

This means a stock is not selected only because its own chart is strong. The sector must also be acceptable.

## Sector Diversification

After ranking, the system builds a diversified basket.

Quota behavior:

- `Strong` sector: can receive a higher quota
- `Neutral` sector: receives a tighter quota
- `Weak` sector: receives zero new candidates

The system also uses a daily rotation key based on date + ticker. This helps prevent every run from scanning exactly the same stocks while preserving quality ranking.

## What Is Calculated For Each Ticker

For every ticker, the technical scanner loads and calculates:

- daily candles
- hourly candles
- weekly candles
- current regular-session close
- ATR
- VWAP
- EMA20
- Weekly MA200
- Volume Profile
- POC - Point of Control
- VAL - Value Area Low
- VAH - Value Area High
- HVN - High Volume Nodes
- Fibonacci swing
- Fib 61.8%
- Fib 78.6%
- Swing Low confluence
- Market Structure
- Volume-Price Scenario
- Relative Strength versus SPY
- earnings date if a setup exists
- extended-hours quote for display context

Extended-hours data:

- pre-market / after-hours price is shown to the user
- it is informational context
- it does not open `BUY_SIMULATED` by itself
- entry still requires regular-session confirmation when `MARKET_LENS_ALLOW_BUY_OUTSIDE_REGULAR_HOURS=false`

## Technical Scan Methods

The system does not depend on one indicator. It combines multiple technical methods and then applies risk and portfolio rules.

No single technique opens a trade. Fibonacci, VWAP, Volume Profile, Breakout, or Liquidity Sweep are analysis layers. A `BUY_SIMULATED` also requires Market Regime, Sector Regime, net R/R, entry confirmation, risk controls, exposure limits, and correlation checks.

### ATR - Average True Range

ATR is the central volatility measure.

Calculation:

- default period: 14 daily candles
- True Range per day is the max of:
  - High - Low
  - abs(High - Previous Close)
  - abs(Low - Previous Close)
- the series is smoothed with EWM using alpha = 1 / 14

Uses:

- ATR% stock filter
- distance checks versus Volume Profile levels
- distance checks versus VWAP
- Fibonacci zone width
- stop placement
- target normalization
- target feasibility
- volatility quality scoring

Smart Universe ATR% filters:

```text
MIN_ATR_PCT = 1.2%
MAX_ATR_PCT = 8.0%
```

Interpretation:

- below 1.2%: may be too slow for swing trading
- above 8.0%: may be too noisy or unstable

### Volume Profile

Volume Profile is calculated from hourly candles.

Calculation:

- splits the price range into 50 buckets
- weights buckets by volume
- POC is the bucket with the highest volume
- Value Area is built from the highest-volume buckets until 70% of total volume is included
- VAL is the lower boundary of the Value Area
- VAH is the upper boundary of the Value Area
- HVNs are secondary high-volume buckets with volume at least 60% of POC volume, capped at 3 levels
- HVNs within 0.2 ATR of POC are removed so the same level is not counted twice

Levels:

- POC - Point of Control
- VAL - Value Area Low
- VAH - Value Area High
- HVN - High Volume Node

Uses:

- Fibonacci confluence
- Swing Low support
- Liquidity Trap targets/context
- Target 2 or structure target
- Setup score
- detecting whether price is inside Value Area

Proximity threshold:

```text
price versus POC / VAL / HVN <= 0.5 ATR
```

Scoring:

- POC confluence: +25
- VAL confluence: +12
- HVN confluence: +8
- price inside Value Area: -20

Rationale:

- POC/VAL/HVN represent areas where significant trading activity occurred.
- A setup is stronger when Fibonacci, Swing Low, or VWAP overlaps with a volume level.
- Price inside Value Area receives a penalty because it may be stuck in the middle of a noisy auction zone.

### VWAP - Session VWAP

The system calculates true session VWAP from hourly candles.

Calculation:

- Typical Price = (High + Low + Close) / 3
- VWAP is calculated only for the latest trading day
- VWAP resets at the start of each trading day
- if volume is unavailable, the latest close is used as fallback

Uses:

- VWAP Reclaim Setup
- confluence for Fib 61.8 setup
- first target in Fib setup if VWAP is above entry
- first target in Swing Volume setup if VWAP is above entry
- VWAP proximity score

Proximity threshold:

```text
abs(price - VWAP) <= 0.3 ATR
```

Scoring:

- VWAP proximity: +8
- volume-confirmed VWAP reclaim: +12

Rationale:

- VWAP is treated as an intraday fair-value reference.
- Reclaiming VWAP may show buyers regaining control.
- A touch is not enough; entry confirmation requires a completed reclaim/close above VWAP.

### Anchored VWAP

The system also uses Anchored VWAP as context for VWAP Reclaim setups.

Calculation:

- looks back up to 63 daily candles
- finds the lowest swing low in that window
- calculates VWAP from that anchor to the present

Use:

- not a standalone trigger
- adds swing context to VWAP Reclaim
- VWAP Reclaim is cleaner if price is above or near Anchored VWAP

Condition:

```text
current_price >= anchored_vwap - 0.3 ATR
```

If price is clearly below Anchored VWAP, the VWAP reclaim is lower quality.

### EMA20

EMA20 is calculated from daily candles.

Uses:

- short-term trend filter
- setup score
- determining whether price is above short-term momentum

Scoring:

- price above EMA20: +8
- price below EMA20: -15

Rationale:

- Long entries above EMA20 have short-term momentum support.
- Long entries below EMA20 are penalized.

### Weekly MA200

The system calculates MA200 from weekly candles.

Uses:

- long-term trend context
- setup score

Scoring:

- price above Weekly MA200: +10
- price below Weekly MA200: -25

Rationale:

- Weekly MA200 is treated as major long-term trend context.
- A long swing setup below Weekly MA200 is materially riskier.

### Market Structure - HH/HL Versus LH/LL

Market Structure is detected using daily pivots.

Calculation:

- detects pivot highs and pivot lows using a 5-candle window
- compares the two latest pivot highs and two latest pivot lows

Classification:

- Uptrend: Higher High + Higher Low
- Downtrend: Lower High + Lower Low
- Ranging: anything else

Scoring:

- Uptrend: +12
- Downtrend: -20
- Ranging: no score adjustment

Rationale:

- Long setups inside uptrends are cleaner.
- Long setups inside downtrends require more caution.

### Pivot High / Pivot Low

Pivots are used across the system.

Pivot high:

- the candle high is higher than the 5 candles before and the 5 candles after it

Pivot low:

- the candle low is lower than the 5 candles before and the 5 candles after it

Uses:

- Fibonacci impulse
- breakout resistance
- swing low support
- market structure

### Fibonacci Impulse + Fib 61.8

The system detects impulse moves and calculates Fibonacci levels.

Steps:

1. Find pivot lows and pivot highs.
2. Build an impulse move from pivot low to the next pivot high.
3. An impulse is valid only if move size is at least 2 ATR.
4. Rank impulses by:
   - 60% recency
   - 40% move size
5. Keep up to two top impulses.
6. Calculate:
   - Fib 38.2
   - Fib 50.0
   - Fib 61.8
   - Fib 78.6

Formula:

```text
fib_382 = swing_high - 0.382 * range
fib_500 = swing_high - 0.500 * range
fib_618 = swing_high - 0.618 * range
fib_786 = swing_high - 0.786 * range
```

Buy zone around Fib 61.8:

```text
zone_low  = fib_618 - 0.25 ATR
zone_high = fib_618 + 0.25 ATR
```

Uses:

- Fib 61.8 Confluence Buy Zone
- stop placement below Fib 78.6
- targets using Fib 50 / swing high
- confluence with POC / VAL / VWAP
- Fib 61.8 proximity score

Scoring:

- Fib 61.8 proximity: up to +20
- Fib zone swept and reclaimed: +20

Rationale:

- Fib 61.8 is treated as a pullback zone inside a prior impulse.
- The system does not buy only because price is at Fib; it requires confluence and entry confirmation.

### ICT / OTE-Style Fib Sweep

Inside Fib setup logic, the system checks whether the Fib zone was swept and reclaimed.

Condition:

- an hourly or daily candle moves below `zone_low`
- then closes back above the level

Scoring:

```text
fib_zone_swept = +20
```

Rationale:

- A sweep below the zone may show liquidity being cleared.
- Reclaiming the zone improves setup quality.

### Swing Low + Volume Support

The system searches for a swing low that overlaps with a Volume Profile level.

Steps:

1. Find valid impulses.
2. Take up to 3 recent swing lows from valid impulse structures.
3. Check whether the swing low is near:
   - POC
   - VAL
   - HVN
4. Required proximity:

```text
abs(swing_low - volume_level) <= 0.25 ATR
```

5. Check whether current price is near swing low:

```text
abs(current_price - swing_low) <= 0.3 ATR
```

6. Check for sweep:
   - hourly low below swing low and close back above
   - or daily low below swing low and close back above within the last 10 days

If price is below swing low by more than 0.1 ATR with no reclaim:

```text
accepted_below = true
```

This receives a penalty.

Scoring:

- Swing low proximity: +10
- Sweep and reclaim: +30
- Daily sweep bonus: +5
- Accepted below: -20

### Liquidity Sweep / Sweep And Reclaim

The system detects wick-below / close-back-above behavior.

Sweep types:

- sweep below VAL
- sweep below swing low
- sweep below Fib zone

Hourly condition:

```text
low < support AND close > support
```

Daily condition:

```text
daily low < support AND daily close > support
```

Scoring:

- Sweep and reclaim: +30
- Daily sweep bonus: +5

Rationale:

- This can represent stop hunting or liquidity clearing.
- A reclaim suggests the breakdown failed.

### Liquidity Trap

Liquidity Trap uses Sweep And Reclaim around VAL/POC.

Two modes:

1. Higher-quality mode:
   - price swept below VAL
   - price reclaimed VAL
   - POC provides structural support

2. Lower-quality mode:
   - price is below VAL but near POC
   - the wipe is still in progress

The Agent treats this setup conservatively:

- no buys in BEAR
- no buys in WEAK sector
- entry confirmation required
- without a reliable reclaim, it is WATCH, not BUY

### Breakout + Retest

The system detects prior resistance, breakout, and retest.

Steps:

1. Find pivot highs from the last 60 candles.
2. For each pivot high, search for a close above that resistance level.
3. Confirm a breakout occurred.
4. Check breakout volume:

```text
breakout_volume >= 1.5 * average_volume_20d
```

5. Check whether current price is inside the retest zone:

```text
retest_low  = resistance - 1.0 ATR
retest_high = resistance + 0.5 ATR
```

6. Ensure structure was not broken after the breakout:

```text
invalidation_level = resistance - 1.5 ATR
no close below this level after breakout
```

Stop:

```text
stop_loss = resistance - 1.5 ATR
```

Target 1:

- nearest structure target when available
- fallback to measured move:

```text
resistance + (resistance - stop_loss)
```

Target 2:

- VAH or swing high

Scoring:

- Breakout volume confirmation: +15
- Uptrend market structure: +12
- EMA/MA200 context contributes

Entry confirmation:

- completed close above trigger
- retest held
- no falling candle into the zone

### VWAP Reclaim

The system detects a reclaim of VWAP after price traded below it.

Conditions:

- at least one recent close was below VWAP
- current price is near VWAP:

```text
abs(current_price - vwap) <= 0.3 ATR
```

- volume is not deteriorating
- Anchored VWAP context is not negative

Target 1:

```text
vwap + 0.5 ATR
```

Target 2:

- VAH, then normalized if needed

Entry confirmation:

- completed close above VWAP proxy
- close >= open
- close >= previous close
- hold/follow-through

### Volume-Price Scenario

The system classifies the last 5 candles versus the prior 5 candles.

It checks:

- did price rise or fall?
- did average volume rise or fall?

Classifications:

- `price_up_vol_up`
- `price_up_vol_down`
- `price_down_vol_down`
- `price_down_vol_up`

Scoring:

- `price_up_vol_up`: +8
- `price_down_vol_down`: +8
- `price_down_vol_up`: -10

Rationale:

- price up + volume up can show demand
- price down + volume down can show a healthy pullback
- price down + volume up can show institutional selling, so it is penalized

### Relative Strength

There are two relative-strength layers.

Scanner layer:

- compares 20-day ticker return to 20-day SPY return
- ratio > 1.0 means the ticker is outperforming SPY

Smart Universe layer:

- compares 3-month return versus SPY and QQQ
- weighting: 65% SPY, 35% QQQ

Setup scoring:

- Relative Strength > 1.3: +10
- Relative Strength < 0.7: -8

Rationale:

- long setups in market leaders are preferred over long setups in laggards

### Professional Market Regime

In addition to Agent Market Regime, each scan result receives Professional Context.

It checks:

- SPY trend
- QQQ trend
- IWM trend

Labels:

- Risk-on
- Mixed
- Risk-off

This layer does not replace Agent Market Regime. It adjusts displayed quality score and adds warnings/strengths.

### Liquidity Quality

Liquidity is checked again inside Professional Context.

Inputs:

- current price
- 20-day average volume
- 20-day average dollar volume

Labels:

- Institutional liquidity
- Tradable
- Thin

Note:

- Smart Universe is stricter and requires `$100M` average dollar volume.
- Professional Context provides explanatory quality scoring for the result card.

### Trend Quality

Professional Context evaluates trend quality:

- price above MA20
- price above MA50
- price above MA200
- MA20 above MA50
- MA50 above MA200
- positive MA20 slope

Quality contribution:

- above MA20: 0.18
- above MA50: 0.20
- above MA200: 0.20
- MA20 above MA50: 0.18
- MA50 above MA200: 0.14
- positive slope: 0.10

Labels:

- Clean uptrend
- Constructive
- Messy

### Volume Confirmation

Professional Context checks whether volume supports the setup.

Inputs:

- latest volume versus 20-day average
- accumulation days
- distribution days
- pullback volume contraction

Accumulation day:

```text
return > 0.5% AND volume > avg20
```

Distribution day:

```text
return < -0.5% AND volume > avg20
```

Labels:

- Confirmed
- Neutral
- Distribution risk

### Event Risk

Event Risk checks:

- earnings date
- whether earnings are near
- largest recent gap in the last 20 days

Labels:

- Earnings risk
- Recent gap risk
- No major event flag

Effect:

- Professional Context reduces quality around near earnings or large recent gaps.
- Agent Risk Layer can fully block buys during Earnings Blackout.

### Extended-Hours Analysis

The system pulls pre-market / after-hours quotes when available.

Stored fields:

- phase
- label
- quote price
- timestamp
- regular close
- change
- change percent
- whether the quote is extended-hours

Extended-hours impact checks:

- quote inside buy zone
- quote below buy zone
- quote above buy zone
- quote touched stop
- quote touched target 1
- quote touched target 2
- informational R/R from extended-hours price

Important:

```text
Extended-hours data is informational only.
```

It can warn that a setup changed, but it does not open `BUY_SIMULATED` without regular-session confirmation.

### Target Normalization

After a setup proposes targets, the system normalizes them if needed.

Goals:

- target 1 must be above entry
- target 1 should not be too close
- target 2 must be above target 1
- use market structure when possible

Defaults:

```text
minimum_t1_atr = 1.2 ATR
fallback_t1 = entry + 2.0 ATR
fallback_t2 = entry + 4.0 ATR
```

If Target 1 is below entry:

```text
target_1 = entry + 2 ATR
```

If Target 1 is too close:

```text
target_1 = entry + 1.2 ATR
```

If Target 2 is not above Target 1:

```text
target_2 = max(structure_target, entry + 4 ATR, target_1 + ATR)
```

### Nearest Structure Target

When possible, a target is based on market structure, not only a mechanical formula.

Calculation:

- looks back up to 63 days
- searches for prior highs above entry + 0.75 ATR
- chooses the nearest prior resistance above price

If no suitable resistance exists, the system uses ATR/measured-move fallback.

### Trade Plan

For every setup that is not `No Trade`, the system builds a Trade Plan.

It includes:

- entry trigger
- trigger price
- invalidation
- stop loss
- target 1
- target 2

Examples:

- Breakout: close above resistance / reclaim
- VWAP: hold VWAP and break above trigger
- Fib/support: break above prior-day high after holding buy zone

Trade Plan is documentation and context. It does not replace the Agent Risk Layer.

## Setup Types

### 1. No Trade

If no clean pattern exists, the ticker receives:

```text
setup_type = No Trade
```

`No Trade` does not mean the company is bad. It means the current chart does not meet the setup rules at that moment.

### 2. Fib 61.8 Confluence Buy Zone

This setup searches for a pullback around Fib 61.8 with confluence.

Required:

- price near a valid Fib zone
- confluence with POC / VAL / VWAP
- theoretical entry around the Fib zone
- stop below Fib 78.6 with ATR buffer
- targets from VWAP / Fib / VAH / swing high
- candidate-level R/R

The Agent will not buy only because price is in the zone. It requires candle confirmation:

- close above buy zone, or
- strong bullish reclaim from the zone, and
- no weak falling candle into the zone

### 3. Breakout + Retest

This setup searches for a breakout above resistance followed by a retest.

Required:

- prior pivot high
- close above resistance after that pivot
- price back inside the retest zone
- no deep close below resistance - 1.5 ATR after breakout
- breakout volume is a bonus when breakout volume >= 1.5x 20-day average

Entry:

- executable entry is current price
- stop: resistance - 1.5 ATR
- target 1: resistance/measured move or structure resistance
- target 2: VAH or swing high

### 4. Swing Low + Volume Support Buy Zone

This setup searches for support around a meaningful swing low with Volume Profile confluence.

Required:

- valid swing low
- confluence with POC / VAL / HVN
- price near support
- stop below support
- targets above current price

### 5. Liquidity Trap Buy Zone

This setup searches for a failed breakdown / sweep / reclaim.

Idea:

- price moves below support or VAL
- then closes back above it
- this may represent liquidity clearing and reclaim

This setup is riskier, so the Agent is stricter:

- no buys in BEAR
- no buys in WEAK sector
- confirmation required
- unreliable reclaim becomes WATCH, not BUY

### 6. VWAP Reclaim Setup

This setup searches for a reclaim above VWAP.

Required:

- price moved back above VWAP
- completed candle closed above VWAP
- follow-through is preferred
- touching VWAP is not enough

## Technical Setup Score

Each setup receives a score from 0 to 1.

The raw maximum is 248 points, then normalized to 0-1.

Examples of weights:

- Sweep and reclaim: 30
- POC confluence: 25
- Fib 61.8 proximity: up to 20
- Fib zone swept and reclaimed: 20
- R/R quality: up to 20
- VAL reclaim after sweep: 15
- Volume-confirmed VWAP reclaim: 12
- VAL confluence: 12
- Swing low proximity: 10
- Price above weekly MA200: 10
- Strong relative strength: 10
- HVN confluence: 8
- VWAP proximity: 8
- Price above EMA20: 8
- Breakout volume confirmation: 15

Penalties:

- price below EMA20: -15
- price below weekly MA200: -25
- downtrend market structure: -20
- price down + volume up scenario: -10
- weak relative strength: -8
- price inside Value Area: -20
- acceptance below swept level: -20

## Scanner-Level R/R

The scanner does not use only the farthest target.

There are two targets:

- Target 1 - primary scale-out / risk-reduction target
- Target 2 - stretch target

Scanner-level decision R/R:

```text
decision_rr = rr1 * 65% + rr2 * 35%
```

For a technical candidate to remain visible:

- rr1 must be positive
- max(rr1, rr2) must be >= min_rr

The scanner's job is to surface candidates. The Agent Risk Layer later decides whether the setup is `BUY_SIMULATED`, `WATCH`, or `SKIP`.

## Agent Market Regime

Before entry, the Agent calculates broad market regime:

- `BULL`
- `NEUTRAL`
- `BEAR`

Inputs:

- SPY
- QQQ
- IWM
- VIX
- US10Y
- DXY

Point logic:

- SPY bullish: +2
- SPY bearish: -2
- QQQ bullish: +2
- QQQ bearish: -2
- IWM not bearish: +1
- IWM bearish: -1
- VIX calm or below 20: +1
- VIX stressed or above 25: -2
- US10Y bullish: -0.5
- US10Y bearish: +0.25
- DXY bullish: -0.25
- DXY bearish: +0.25

Classification:

- risk_points >= 4 -> BULL
- risk_points <= -2 -> BEAR
- otherwise -> NEUTRAL

Effects:

```text
BULL:
  max total exposure = $40,000
  minimum net R/R = 2.0
  minimum setup score = 0.45

NEUTRAL:
  max total exposure = $20,000
  minimum net R/R = 2.5
  minimum setup score = 0.55

NEUTRAL + STRONG sector:
  minimum net R/R can be relaxed to 2.2

BEAR:
  no new BUY_SIMULATED
```

## BUY_SIMULATED Entry Rules

The Agent opens a new `BUY_SIMULATED` only if all required gates pass.

Required:

- setup type is not `No Trade`
- Market Regime is not `BEAR`
- scan is during regular session unless off-hours buys are explicitly enabled
- setup score >= 0.45 in `BULL`
- setup score >= 0.55 in `NEUTRAL`
- sector is not `WEAK`
- normalized quality is acceptable
- buy zone exists
- stop loss exists
- target 1 and target 2 exist
- price is inside the buy zone
- executable entry is valid
- net_rr_1 >= 0.80
- target 2 cannot justify a trade alone
- weighted net R/R passes the active market threshold
- target 1 is not too close versus ATR
- entry confirmation passed
- no earnings blackout is active
- no stop-loss cooldown blocks re-entry
- sector exposure is not exceeded
- factor/theme exposure is not exceeded
- no high correlation warning versus open positions
- position size fits cash, exposure, and risk limits

If one condition fails:

- the action may become `WATCH`
- if it is close or staged off-hours, it may become `WATCH_READY`
- if the issue is severe, it becomes `SKIP`

## Entry Confirmation

The Agent does not buy only because price is inside the buy zone.

It uses completed candles, not live candles.

Defaults:

```text
MARKET_LENS_REQUIRE_ENTRY_CONFIRMATION=true
MARKET_LENS_ENTRY_CONFIRMATION_INTRADAY_ENABLED=true
MARKET_LENS_ENTRY_CONFIRMATION_INTRADAY_INTERVAL=30m
MARKET_LENS_ENTRY_CONFIRMATION_INTRADAY_PERIOD=5d
```

Breakout + Retest:

- close above trigger
- retest held
- no falling candle into the zone

VWAP Reclaim:

- close above VWAP proxy
- close >= open
- close >= previous close
- hold above buy zone

Fib/support:

- candle touched the zone
- close above buy zone, or strong bullish reclaim
- strong bullish reclaim requires close above zone midpoint, close above open, close above previous close, and close in the upper part of the candle
- weak/falling candles block BUY

If there is not enough data:

```text
BUY is blocked
action becomes WATCH
```

## Gross R/R Versus Net R/R

The system stores both theoretical and execution-adjusted R/R.

Gross R/R:

- based on entry, stop, target 1, target 2
- does not include spread or slippage

Net R/R:

- uses executable entry
- adds spread
- adds slippage
- adds fees if configured
- reduces target assumptions by slippage
- increases effective risk

Agent weighting:

```text
weighted_net_rr = net_rr_1 * 80% + net_rr_2 * 20%
```

Default thresholds:

```text
MARKET_LENS_MIN_PRIMARY_NET_RR=0.80
MARKET_LENS_PREFERRED_PRIMARY_NET_RR=1.00
MARKET_LENS_PRIMARY_RR_WEIGHT=0.80
MARKET_LENS_STRETCH_RR_WEIGHT=0.20
```

Slippage model:

```text
avg dollar volume >= $500M:
  fallback spread = 0.03%
  slippage = 0.07%

avg dollar volume >= $100M:
  fallback spread = 0.08%
  slippage = 0.15%

avg dollar volume >= $25M:
  fallback spread = 0.15%
  slippage = 0.30%

below $25M:
  fallback spread = 0.25%
  slippage = 0.60%
```

Breakout entries receive extra slippage:

- +0.10% for liquid stocks
- +0.20% for lower-liquidity stocks

If ATR% is above 6%, slippage is multiplied by 1.15.

Slippage is capped at 0.75%.

## Target Validation

The system checks whether targets are realistic versus ATR and market structure.

Stored:

- target_1_atr_distance
- target_2_atr_distance
- target_feasibility_status
- market_structure_status
- prior_high_20
- prior_high_63
- previous_resistance

Statuses:

- `OK`
- `LOW_REWARD_DISTANCE`
- `AGGRESSIVE`
- `EXTENDED`
- `INVALID`
- `UNKNOWN`

Default:

```text
MARKET_LENS_MIN_TARGET1_ATR_DISTANCE=0.75
```

Rules:

- Target 1 must be above price
- Target 2 must be above price
- Target 1 too close to ATR becomes WATCH_READY/WATCH
- Target 1 above 7 ATR or Target 2 above 14 ATR is EXTENDED
- targets too far above recent structure are EXTENDED

## Earnings Blackout

The Agent checks earnings dates.

Rules:

- no new `BUY_SIMULATED` 5 trading days before earnings
- no new `BUY_SIMULATED` 1 trading day after earnings
- if earnings data is missing, default behavior is not to block but to add a warning

Config:

```text
MARKET_LENS_BLOCK_UNKNOWN_EARNINGS=false
```

## Stop-Loss Cooldown

If a ticker was stopped out, the Agent applies a cooldown before a new entry.

Default:

```text
MARKET_LENS_STOP_COOLDOWN_DAYS=3
```

Re-entry during cooldown is allowed only if:

- setup_score >= 0.60
- net_rr_1 >= 1.20
- entry_confirmation_passed=true
- new setup type is different from the failed setup type

Otherwise:

```text
final_action = WATCH
```

## Correlation Check

Before buying, the Agent checks rolling correlation against open positions.

Calculation:

- daily returns
- up to 90 recent trading days
- at least 40 overlapping observations required
- each candidate is checked against each open position

Default threshold:

```text
MARKET_LENS_CORRELATION_BLOCK_THRESHOLD=0.85
```

If correlation is high:

```text
WATCH: High correlation with existing position
```

The goal is to avoid a portfolio that looks diversified by tickers but is actually concentrated in the same factor.

## Factor / Theme Tags

Each ticker receives factor/theme tags where possible.

Examples:

- Mega Cap Tech
- AI / Semiconductors
- High Beta Growth
- Defensive
- Financials
- Energy
- Consumer Cyclical
- Low Volatility
- Small Cap / Risk-On
- Rates-sensitive Growth

The system checks factor/theme exposure in addition to sector exposure.

Defaults:

```text
BULL factor exposure cap = 50% of max market exposure
NEUTRAL factor exposure cap = 35% of max market exposure
```

## Portfolio Risk Rules

The Agent manages a simulated:

```text
$100,000
```

Default rules:

- max allocation per ticker: 10% of portfolio, up to `$10,000`
- max risk per trade: 1% of portfolio, up to `$1,000`
- BULL max total exposure: `$40,000`
- NEUTRAL max total exposure: `$20,000`
- BEAR: no new trades
- BULL sector exposure cap: 40% of allowed exposure
- NEUTRAL sector exposure cap: 30% of allowed exposure
- BULL factor exposure cap: 50% of allowed exposure
- NEUTRAL factor exposure cap: 35% of allowed exposure

Position size is the smaller valid amount among:

- available cash
- max ticker allocation
- max risk based on entry-stop distance
- remaining market-regime exposure
- sector exposure cap
- factor exposure cap

If a high-quality setup is too large:

- the Agent first tries to reduce share count
- if reduced size still cannot fit the rules, the action becomes `WATCH`

## Off-Hours Scans

The system can scan outside regular market hours.

Purpose:

- identify candidates
- stage `WATCH_READY`
- prepare for regular-session confirmation

Default:

```text
MARKET_LENS_ALLOW_BUY_OUTSIDE_REGULAR_HOURS=false
```

Meaning:

- no `BUY_SIMULATED` outside regular session
- pre-market / after-hours data is informational only
- a strong off-hours setup must wait for regular-session confirmation

Session logic:

- PRE_MARKET: 04:00-09:29 New York
- REGULAR: 09:30-16:00 New York
- AFTER_HOURS: 16:01-20:00 New York
- WEEKEND/CLOSED: no new entries

## Action Types

### BUY_SIMULATED

Opens a new paper position.

Allowed only when all technical, risk, portfolio, and confirmation rules pass.

### WATCH_READY

Setup is close to entry or staged outside regular hours.

Examples:

- valid setup outside regular session
- net R/R close to threshold
- target 1 close but structure remains interesting

### WATCH

Technical setup exists, but one or more entry gates are missing.

Examples:

- price not inside buy zone
- confirmation failed
- net R/R insufficient
- sector not strong enough
- exposure/correlation issue

### SKIP

No valid setup or a major block exists.

Examples:

- No Trade
- earnings blackout
- invalid targets
- bear market block
- critical missing data

### HOLD

Existing paper position remains open.

### TAKE_PARTIAL_PROFIT

Target 1 touched.

The system closes half the simulated quantity and moves stop to entry.

### TAKE_PROFIT

Target 2 touched.

The system closes the remaining position.

### EXIT_STOP

Stop loss touched.

The system closes the position and starts cooldown.

## Position Management After Entry

The Position Monitor manages open positions.

It does not open new trades.

It:

- reads open positions from Excel
- downloads 1-minute intraday candles
- checks each candle's high/low
- detects TP1, TP2, or SL touches
- updates Excel
- updates dashboard data
- writes event logs

Conservative event priority:

1. Stop Loss
2. Target 2
3. Target 1

If target and stop are touched in the same one-minute candle:

```text
stop-first policy is applied
```

Reason: the exact intraminute sequence is unknown.

## TP1 / TP2 / SL Rules

### Target 1

When candle high touches Target 1:

- action: `TAKE_PARTIAL_PROFIT`
- closes half the current simulated quantity
- remaining position stays open
- stop loss moves to original entry price
- realized P/L is recorded
- notes say: `Partial profit taken; stop moved to breakeven.`

### Target 2

When candle high touches Target 2:

- action: `TAKE_PROFIT`
- closes the remaining position
- realized P/L is recorded

### Stop Loss

When candle low touches stop:

- action: `EXIT_STOP`
- closes the remaining position
- realized P/L is recorded
- cooldown starts

## Decision JSON

Every scanned ticker receives a Decision JSON object.

Important fields:

- timestamp
- ticker
- company_name
- price
- market_regime
- market_regime_score
- sector
- sector_etf
- sector_regime
- sector_score
- market_cap
- market_cap_bucket
- setup_type
- setup_score
- buy_zone_low
- buy_zone_high
- stop_loss
- target_1
- target_2
- gross_rr_1
- gross_rr_2
- gross_rr_decision
- net_rr_1
- net_rr_2
- net_rr
- estimated_spread
- estimated_slippage
- executable_entry
- theoretical_entry
- earnings_date
- earnings_blackout
- sector_exposure_before
- sector_exposure_after
- factor_tags
- factor_exposure_before
- factor_exposure_after
- highest_correlation_ticker
- highest_correlation_value
- position_size
- cash_available
- portfolio_exposure_before
- portfolio_exposure_after
- final_action
- reason
- warnings

The goal is to make every decision auditable later.

## Data Outputs

The Agent writes:

```text
agent_tracker/market_lens_agent_portfolio_budget_100k.xlsx
agent_results/decisions/*.jsonl
agent_results/summaries/*.md
agent_results/screenshots/*.png
agent_results/charts/
agent_results/position_monitor/*.md
```

The dashboard at:

```text
/agent
```

shows:

- portfolio state
- open positions
- latest actions
- trade log
- latest run summary
- saved charts
- entry checklist
- P/L
- score calibration

## Chart Retention Policy

To avoid repository bloat, charts are intentionally limited.

Charts are saved for:

- `BUY_SIMULATED`
- `HOLD` / open positions
- `WATCH_READY`
- up to 5 closest rejected candidates

Charts are not saved for:

- every `No Trade`
- every early rejected stock
- every normal rejected candidate
- very low setup score

Defaults:

```text
MARKET_LENS_SAVE_REJECTED_CHARTS=false
MARKET_LENS_REJECTED_CHART_LIMIT=5
MARKET_LENS_REJECTED_CHART_MIN_SCORE=0.40
```

## Scan And Monitor Schedule

Scanner endpoint:

```text
/agent/trigger-scan
```

Regular-session confirmation scans:

- 09:45 New York
- 10:30 New York
- 11:30 New York
- 13:30 New York
- 14:30 New York
- 15:30 New York
- 16:15 New York

Off-hours staging scans:

- weekdays: 06:30, 08:30, 09:10, 16:20, 18:30, 20:15, 22:30 New York
- Saturday: 11:00 New York
- Sunday: 18:30, 22:00 New York

Live position monitor endpoint:

```text
/agent/monitor-live
```

Recommended monitor schedule:

- Monday-Friday
- every 1 minute
- 09:35-16:05 New York

The live monitor dispatches GitHub Actions only when an open position touches TP/SL.

## How To Read Actions

Example `BUY_SIMULATED`:

```text
BUY_SIMULATED:
Market is acceptable,
sector is not weak,
setup is valid,
entry confirmation passed,
net R/R passed,
no earnings blackout,
correlation acceptable,
position size fits risk limits.
```

Example `WATCH`:

```text
WATCH:
Technical setup exists, but entry confirmation failed,
or net R/R is below threshold,
or price is not executable inside the buy zone.
```

Example `WATCH_READY`:

```text
WATCH_READY:
Setup is close to entry or staged outside regular hours.
Needs regular-session confirmation before BUY_SIMULATED.
```

Example `SKIP`:

```text
SKIP:
No Trade, earnings blackout, invalid targets,
bear market block, or major risk/data issue.
```

## What The System Does Not Do

The system does not:

- provide financial advice
- guarantee profitability
- connect to a broker
- place real trades
- use real money
- buy only because price is inside the buy zone
- allow Target 2 alone to justify a weak trade
- open new buys outside regular session by default

## Future Improvement Areas

Possible future improvements:

- formal backtesting by setup type
- walk-forward validation
- performance by setup_score bucket
- checking whether higher setup_score actually predicts better win rate
- improving Anchored VWAP by swing high/low event anchors
- calibrating slippage with real bid/ask data when reliable
- sector breadth analysis beyond ETF trend
- expanding to 200/300 tickers using batches if 100 is not enough

## Summary

Market Lens Agent is not just a scanner. It is a layered decision system:

```text
Universe
-> Filters
-> Sector Health
-> Stock Score
-> Technical Setup
-> Professional Context
-> Market Regime
-> Entry Confirmation
-> Net R/R
-> Earnings / Exposure / Correlation
-> Position Sizing
-> Final Action
-> Portfolio Monitor
```

Core principle:

```text
Not every good stock is a trade.
Not every setup is an entry.
An entry happens only when the technical setup, market, sector, risk, execution, and portfolio context all align.
```

