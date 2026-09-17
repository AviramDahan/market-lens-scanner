# Community setup notifications

User-authorized informational alerts disregard BEAR and the Agent's account management: available cash, sizing, exposure, portfolio heat, sector/factor concentration, correlation with holdings, existing positions, stop cooldown and daily pilot quota. Actual trading decisions, portfolio persistence and exits are unchanged.

Entry zone, positive stop risk, gross R/R, professional score, normalized quality, sector quality, net weighted/TP1 R/R, target feasibility, earnings rules, regular-session timing and fresh completed-candle confirmation still apply. During BEAR, its disabled-entry thresholds (score 1 / R/R 999) are replaced **only for informational assessment** by configured BULL quality floors (defaults score .45, net R/R 2, TP1 .80). Neutral uses its existing quality rules, including qualifying pilot quality without account quotas. No stale/off-hours setup alert is sent.

All detected candidates are assessed. At most one qualified setup alert per ticker is chosen by score/TP1 R/R/weighted R/R; this selection never changes the trading selection. A regular BUY notification retains precedence when the Agent actually opens a position. Existing holdings can receive a technically valid setup alert while their original exit management remains intact.

The community message includes ticker/company, setup, timestamp, price, entry zone, stop, targets, score, net R/R and sector/market state. BEAR is a market caution, not a notification veto. It contains no account sizes, balances, holdings, Agent decision or personal dashboard link. An alternative candidate's old chart is never attached to mismatched levels.

Uses the existing Telegram destination and QUALIFIED_BLOCKED_ENABLED switch (default enabled), with the existing post-persistence outbox. No new service or paid dependency. One delivery key per ticker/setup/date limits repeats; successful delivery receipts are merged into current main in an isolated checkout, preserving newer portfolio changes. Send-then-persist is not a transactional exactly-once guarantee: a process/network failure after Telegram accepts a message can permit a later duplicate. Failures are surfaced rather than fabricating a successful trade or receipt.

Tests use simulated Telegram only; no test post is sent to the live channel. Deployment enables real future qualifying alerts as requested.
