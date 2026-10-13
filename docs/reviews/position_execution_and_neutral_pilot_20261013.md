# Position execution and neutral-pilot review

## Scope

Read-only review of the production tracker captured on 13 October 2026.
No cash correction, reopening of trades, historical deletion, new strategy,
entry-gate change or capital increase is authorized by this report.

## Execution repair

`StrategyCandidate.current_price` is the technical daily setup price. On the
00:15 UTC scans of 9 and 10 October it matched the prior New York session's
close. It must not act as a position execution quote.

The position monitor is now the sole TP/SL lifecycle executor. User and Agent
scans retain holdings and obtain marks from validated completed intraday bars.
They cannot fabricate exit proceeds or free allocation from a setup price.
If that mark is unavailable, the prior mark is preserved and the Agent records
`POSITION_MARK_UNAVAILABLE` rather than silently claiming fresh pricing.

The monitor still uses the existing stop-first event ordering, half-position
TP1, stop-to-entry, TP2, frozen execution costs, event cursors, writer lock,
outbox and persistent Telegram receipts. Old scanner outboxes remain retryable.
The host heartbeat guard compares content as well as file time: coalesced
filesystem timestamps must not reject a genuinely updated heartbeat. A file
whose content and timestamp both remain unchanged still fails the guard.
Its production bar window now excludes incomplete/future bars and rejects
regular-session observations older than 300 seconds (configurable through
`MARKET_LENS_POSITION_BAR_MAX_AGE_SECONDS`). This is data validation, not an
entry or trading threshold. Existing monitor schedule and extended-hours policy
are unchanged: extended stops remain disabled; targets require two closes.

## Historical annotations

The archived charts independently show a higher quote than the setup price:

| Trade | Stale setup price | Archived quote | Original stop | Recorded PnL |
| --- | ---: | ---: | ---: | ---: |
| TRGP 20261008_190018 | 283.96 | 288.49 | 286.5682 | -106.40 |
| TGTX 20261009_173018 | 53.34 | 55.05 | 53.6524 | -149.07 |
| PFE 20261009_190021 | 27.82 | 28.27 | 27.9217 | -68.64 |

These exits are flagged separately, not voided or removed. Reported figures
continue to include their -324.11 USD recorded PnL. The dashboard labels those
exit records and warns that historical reported returns need execution review.

Independent completed-bar replay finds no event before the recorded exits.
TRGP subsequently touches its stop on 9 October at 19:47 UTC. TGTX and PFE
remain censored open in the available evidence. These observations are not
alternative portfolio returns and do not justify crediting 324.11 USD to cash.
Yahoo bars re-fetched after the event corroborate the archive, but are not a
broker execution feed. Compressed bar evidence and fingerprints are retained.

## Neutral pilot and Fib findings

The qualified-selection cohort has 10 fully closed trades on eight tickers,
one money win, nine money losses and -1080.08 USD recorded PnL. All ten are
Fib 61.8 Confluence in a NEUTRAL market, STRONG sector and neutral-pilot path.
Six confirmations were more than 60 minutes old measured from candle close.

Excluding the three flagged exits for diagnosis leaves seven completed
observations, one win and six losses, with -755.97 USD PnL. This exclusion is
not a corrected trading track record. Legacy and qualified-selection cohorts
also differ in market periods and execution-cost modeling.

CSCO is a full-trade money win: +64.76 USD partial and -3.00 USD remaining stop
sum to +61.76 USD. A final stop label alone is not a losing trade.

The clean conclusion is not to scale exposure or widen stops automatically.
First establish valid execution. Then compare existing shadow confirmation
freshness and Fib-stop variants against the unchanged active baseline using
independent signals, costs and closed/censored outcomes. Daily-bar MFE/MAE can
include moves before entry or after exit on the same day, so it cannot prove
that a stop was premature. Current evidence is insufficient for a causal
claim that changing a particular score or freshness threshold improves returns.

## Reproduction

Run the audit against a read-only tracker copy (not the live workbook):

```powershell
python -m agent.audit_stale_scanner_exits --tracker PATH_TO_TRACKER_COPY --observations docs/reviews/position_execution_source_observations_20261013.json --bar-dir agent_results/audits/position_execution_20261013 --output PATH_TO_NEW_REPORT --as-of 2026-10-12T21:30:00Z
python -m pytest -q
```

The saved read-only report is
`agent_results/audits/position_execution_review_20261013.json`. It records the
tracker fingerprint, compressed evidence hashes, missing-data limitations,
original exit outcomes and separate later counterfactual observations.
