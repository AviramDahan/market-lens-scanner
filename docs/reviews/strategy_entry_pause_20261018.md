# Historical strategy entry-pause review

## Decision and limits

The user requested removing historically unprofitable strategies. Use final
cash PnL and profit factor, not win rate alone. A money win combines every
partial and final exit for the original position. Open trades are not wins
or losses, and zero trades means no evidence.

The frozen production workbook was captured on 18 October 2026, with SHA256
`306c9294ce724a65d15d9f884dde47afff9ddbaea175420cfb9ac7cdf4b8d2fb`.
It contains 51 fully closed paper positions and no remaining open positions.
This is a recorded-ledger analysis, not an independently validated backtest.

| Setup | Closed | Money win rate | Recorded PnL USD | Profit factor | Decision |
| --- | ---: | ---: | ---: | ---: | --- |
| Fib 61.8 Confluence Buy Zone | 48 | 29.17% | -58.92 | 0.9866 | Pause new entries reversibly |
| Liquidity Trap Buy Zone | 2 | 50.00% | +211.86 | 1.6144 | Retain; insufficient sample for superiority |
| VWAP Reclaim Setup | 1 | 100.00% | +379.96 | Not estimable without losses | Retain; insufficient sample |
| Breakout + Retest | 0 | Not estimable | No closed trades | Not estimable | Retain; no loss evidence |
| Swing Low + Volume Support Buy Zone | 0 | Not estimable | No closed trades | Not estimable | Retain; no loss evidence |

The qualified-selection cohort has 12 closed Fib trades, one money win,
11 losses and -1305.65 USD. Three contain historical stale-source exit errors
totaling -324.11 USD. Excluding those only as a sensitivity leaves nine
trades and -981.54 USD; that is not a corrected portfolio return.

Two further legacy Fib positions (GILD and CHTR) have pre-entry trigger evidence errors.
No original financial row is removed or restated. Legacy cost accounting and
qualified-selection fills differ, as do their periods and market regimes.
The pause is a conservative manual decision, not proof that Fib can never
be profitable. Do not declare the other setups profitable from one or two
closed positions or expect a replacement volume of buys.

## Scope of implementation

`MARKET_LENS_DISABLED_ENTRY_SETUPS` is an optional comma-separated list of
exact setup names (case-insensitive). Unset or empty preserves existing policy.
Activation value for this review:

```text
MARKET_LENS_DISABLED_ENTRY_SETUPS=Fib 61.8 Confluence Buy Zone
```

Apply it consistently to web and scanner-worker processes. The shared
candidate selector evaluates and retains the setup evidence, then prevents
paused setups from new BUY_SIMULATED, WATCH_READY staging and qualified-entry
Telegram alerts. Other fully qualified candidates remain selectable. Decisions
retain their pre-pause action and reason plus an explicit policy blocker.

Existing holdings, their monitor, TP1/TP2/SL policy, entry confirmation,
R/R thresholds, capital allocation, Smart Universe and historical data are
not changed. The subsequent explicit request below supersedes the original
entry-pause-only scope, including cross-strategy Fib scoring inputs.

## Final requested policy: retire trading Shadow, exclude Fibonacci

The user requested ending the eight live trading Shadow strategies and using
the strongest non-Fib candidate through the existing active entry pipeline.
Production web and worker share these Compose defaults:

```text
MARKET_LENS_SHADOW_STRATEGIES_ENABLED=false
MARKET_LENS_FIBONACCI_ENTRIES_ENABLED=false
MARKET_LENS_BREAKOUT_CONTINUATION_ENABLED=true
```

The eight original evaluators remain available explicitly to offline research
so historical reports remain reproducible; live decisions record an empty
`shadow_strategies` list and `shadow_collection_status=RETIRED`. Historical
JSONL, workbooks, charts and reports are not deleted or rewritten. Technical
monitor parity/safety observation and the separate weak-sector measurement
are not these eight trading strategies and are not changed.

The scanner stops computing Fib levels and omits the Fib detector. All four
non-Fib detectors receive `fib_info=None`, removing Fib confluence bonuses
and Fib-derived targets, including the Breakout TP2 dependency. Existing
non-Fib structure/volume target rules remain unchanged; an insufficient plan
is rejected by the existing gates rather than given an invented target.
The shared selector also blocks cached/new candidate names containing `fib`,
including Fibonacci variants. Existing holdings and their frozen exit plans
remain exempt from the new-entry retirement.

`BREAKOUT_CONTINUATION` is an additive `active_v1` entry profile on the
existing `Breakout + Retest` detector, not a second duplicate detector or a
renaming of a Fib strategy. It requires a known non-BEAR market and non-WEAK
sector, regular session, completed fresh same-session confirmation, primary
net R/R at least the existing configured minimum (never below 0.8), and no
earnings blackout. The full existing setup-score, weighted net R/R, target,
cooldown, sizing, capital, correlation and exposure checks still apply.
The decision JSON records its checks under `active_entry_profile`; a passed
profile cannot turn an otherwise failing WATCH/SKIP into a buy.

### Evidence and limitations

Frozen Shadow research inputs contain 102,602 records from 706 files between
17 September and 16 October 2026. Daily deduplicated Breakout observations:
57 signals, 53 available 1-session observations, 60.38% positive price changes,
mean +0.7275%; 35 available 5-session observations, 54.29% positive and mean
-0.3024%. These use later recorded scan prices, not verified TP/SL paths or
cash outcomes. **60.38% is not a proven trading money win rate.**

All 57 Breakout signals use the existing Breakout + Retest setup. In contrast,
143 of 144 Trend Pullback observations and all five Relative Strength Leader
observations originate from Fib setups; neither is promoted as an independent
non-Fib strategy. VWAP has only 19 observations, with 50% positive among 18
available 1-session observations; its existing detector is retained, not
declared a new proven winner. Removing Fib inputs also changes the proposed
Breakout plans relative to the historical proxy, so its old percentage cannot
be presented as an expected live win rate.

### QA and rollback

Local full regression: `python -m pytest -q` -> 830 passed. New coverage includes
default-off Shadow/nonmutation and offline reproduction, Fib-free scanner and
detector inputs, cached variant blocking, shared web/worker configuration,
all existing lifecycle actions, additive Breakout failures and preservation
of existing risk gates. Python defaults match Compose; off-hours timing-only
failures retain WATCH_READY, not entry permission. A WEAK-sector failure is
reported through the existing sector blocker, preserving the separate read-only
counterfactual measurement. Existing isolated monitor accounting/idempotency
tests remain in the full suite. No synthetic production trade or Telegram
test is required or sent.

Code-only rollback can explicitly set the three flags to `true`, `true`,
`false` respectively and recreate both process types, or restore the previous
images/configuration. Never restore an old workbook over newer portfolio
events. Local tests or a merged PR alone are not proof of production release;
verify image revision, both process policies, endpoints and a bounded new
scanner run separately.
