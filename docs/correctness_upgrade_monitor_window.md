# Chronological monitor windows

The monitor previously returned after the first event for each ticker. A TP1
followed by a later TP2 or breakeven stop in the same fetched window required
another invocation, delaying ledger state and notification.

The orchestration now consumes the same fetched frame after applying TP1,
strictly after the TP1 bar timestamp, with reduced quantity and the existing
breakeven stop. A maximum of three evaluations permits one partial, one terminal
exit, or a final hold refresh. There is no additional provider fetch per ticker.
Each event keeps its current trade identity, ledger row, and pre-event
notification snapshot. Health counts distinct positions rather than events.

Unchanged assumptions: stop-first if both stop and target touch in one bar;
TP2 before TP1 if both targets first touch in the same bar; no inferred order
within the TP1 bar for the newly moved stop; fills at configured levels, not
gap-adjusted execution. These are paper-model limitations, not verified market
fills. Historical transactions are not rewritten.

Regression coverage uses real in-memory workbooks: TP1 then stop, TP1 then TP2,
TP1 then hold and repeated polling without duplicate credit, one shared fetch,
correct remaining quantity, breakeven stop, cash, event identity and health count.
