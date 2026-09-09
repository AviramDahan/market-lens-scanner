# Monitor Failure Semantics

The monitor previously exited successfully even when every open position
returned ERROR or NO_DATA. With no events and no-op persistence disabled this
could look indistinguishable from a healthy run with no TP/SL touches.

The monitor now prints a structured, sanitized health status:

- MONITOR_OK: no failed evaluations, including an empty portfolio.
- MONITOR_DEGRADED: some evaluations failed; preserve valid events and emit a
  workflow warning rather than abort persistence of those events.
- MONITOR_FAILED: all evaluations failed; exit code 1 before workbook save,
  Update Log, or notifications. This applies even with no-op persistence enabled.

The machine-readable status lists ticker/status, not raw provider error text.
NO_NEW_BARS is still treated as a successful no-new-event result. Quote/session
freshness is a separate unresolved monitoring-quality question; this change does
not certify current prices merely because a provider returned rows.

Tests cover all failure classes, empty and healthy portfolios, mixed outcomes,
sanitized details, and the main entry point's nonzero exit without workbook save.
No TP/SL policy, target, sizing, or threshold is changed.
