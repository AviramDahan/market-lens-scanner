# Regime indicator history contract

Regime benchmarks previously requested the selected analysis period (often six
months) and silently used span=min(200, rows) while calling the result EMA200.
They now request two years independently of the chart period and require at
least 200 finite positive closes. Spans remain 20/50/200; insufficient provider
history is reported through the existing missing-data warning path.

Missing IWM previously earned +1 risk point because missing was not bearish.
It now contributes zero. No scoring weights, risk thresholds, exposure caps,
Smart Universe, or setup-selection policy changed. Correct inputs can change
the computed regime and therefore effective exposure; that is expected and
must be observed in subsequent decisions, not interpreted as a performance win.

Limitations: EMA initialization still depends on the available history; 200
observations are a minimum, not proof of convergence. Existing missing-market
fallback is NEUTRAL and is not equivalent to a validated healthy market. This
change does not redesign the regime model or intraday update cadence.

Tests cover short history, exact span, invalid values and missing-data risk
points. Full suite after this change: 359 passed.
