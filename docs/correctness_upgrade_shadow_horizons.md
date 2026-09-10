# Shadow measurement horizon repair

The prior summary labelled the next available rescan date as 1d, even when
several exchange sessions were missing. Outcomes now require an observation
on the exact NYSE session date for horizons 1/3/5/10. Weekends and holidays are
skipped using the existing calendar. No target-date observation means missing,
not zero and not a substitute later price.

Best/worst shadow rankings previously mixed 5-day observations for one strategy
with 1-day observations for another. Rankings now use a common 1-session horizon.
This remains a descriptive ranking, not strategy activation or statistical
evidence of superiority. Minimum sample was not increased or used for trading.

New summary metadata documents the method version and limitations. Existing
field names remain compatible; corrected values may have lower coverage.

Still unresolved: scanner-driven sampling bias, period-boundary censoring,
nonuniform intraday observation times, costs, and TP/SL path outcomes. These are
not backtests or executable returns. Daily/weekly grouping and legacy timestamp
date semantics remain unchanged in this narrow fix. No trading action or old
record is rewritten. Full suite: 375 passed.
