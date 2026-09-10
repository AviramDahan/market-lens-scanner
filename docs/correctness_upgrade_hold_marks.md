# Held-position marks and allocation order

Agent HOLD processing updated the position row but left the running exposure
scalar stale for the next candidate. The user path did not refresh held marks
and omitted portfolio_open_risk_before from the final evaluator call.

A shared mark helper now updates price, exposure, unrealized PnL and the existing
mark-to-stop risk definition, returning the exposure delta. Both orchestration
paths use it, and the user path supplies aggregate recorded open risk. It does
not credit cash, change stops, or reinterpret portfolio heat. Invalid marks fail
before position mutation.

The existing Agent ordering (open positions, setups, score, R/R, stable input
tie-break) is now shared with the user path. User response order is preserved;
only internal allocation order changes. No ranking weights or gates changed.

Limits: marks are only refreshed for holdings present in scan results. This
does not establish provider timestamp freshness for every held asset, mark all
holdings simultaneously, or guarantee user/Agent equality with different
portfolio snapshots, universe baskets, or scan times. Those are separate tasks.

Tests: true orchestration passes updated exposure and heat to the next candidate,
existing input portfolios remain unchanged for user simulations, currency and
repeated marks are covered, and reversed display order cannot consume funds
before a supplied exit. Full suite: 372 passed.
