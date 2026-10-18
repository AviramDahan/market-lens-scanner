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

Three further legacy Fib positions have pre-entry trigger evidence errors.
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
R/R thresholds, scoring, capital allocation, Smart Universe, historical data
and shadow strategy algorithms are not changed. Clear the variable on both
processes to reverse the pause; no portfolio rewrite is needed.

## Release boundary

This document and tests prepare the pause. The code has no active pause by
default. A production release and explicit environment activation are required;
a successful local test must not be described as a live strategy shutdown.
