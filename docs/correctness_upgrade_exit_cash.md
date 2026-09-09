# Exit Cash and Exposure Within a Scan

## Defect

The agent changed open positions on an exit but did not immediately credit its
in-memory cash or release its in-memory exposure. Later candidates in the same
scan could be rejected using pre-exit balances. The user strategy path did not
apply exits to its simulated holdings at all.

## Change

Both paths use `app.strategy.apply_strategy_exit`. It applies the in-memory
position transition and returns cash and exposure deltas. The agent's existing
wrapper retains its metadata refresh. Existing Trade Log persistence remains
the authoritative source for final cash; this change does not append another
cash movement or rewrite old transactions.

Full exits release the position's stored exposure and credit recorded proceeds.
Partial exits reduce shares, mark partial_taken, advance the stop to entry using
the existing policy, and mark the remaining exposure at the current scan price.
The updated balances are available to the next candidate. The user path copies
position dictionaries before simulation, preserving the caller's holdings.

Invalid quantities, repeated partial exits, missing positions, and invalid
accounting inputs are rejected before mutation. A one-share TP1 closes that
share under the existing quantity convention. The new helper does not implement
cross-process transaction deduplication or change execution-price assumptions.

## Tests and limitations

Tests exercise both exit adapters and both orchestration entry points, including
full/partial exit followed by another candidate, odd lots, one share, duplicate
application, invalid quantity, and portfolio-currency amounts. Final gates remain
authoritative; permitting sizing does not force a BUY.

No capital limit, risk threshold, target, or Universe logic changed. Historical
reconciliation, same-bar/gap assumptions, held-position mark freshness, scanner
ordering differences, and durable transactional persistence remain separate work.
