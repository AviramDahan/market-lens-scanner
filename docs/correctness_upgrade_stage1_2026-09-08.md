# Correctness Upgrade: Stage 1

## Status and Scope

Implemented and verified locally. Not committed, pushed, or deployed to Render.
No production scan was dispatched and no live tracker was modified.
This package addresses AUDIT-CLOCK-01/02/03 and AUDIT-ENTRY-01 only.

Stage 0 is the historical baseline; its nine expected failures are not the
current result. Six of those cases now pass without expected-failure markers.

## Entry Clock

`app/trading_clock.py` supplies one cached NYSE session calendar to:

- `app.agent_risk.market_session_status`: entry authorization.
- `app.setups._regular_market_is_open`: regular-session volume timing checks.
- `agent.market_lens_ui_agent.agent_market_session`: existing discovery phase selection.

The calendar comes from pinned `pandas-market-calendars==5.4.0`; schedule
calculation is local, not a per-candidate network request. It handles exchange
holidays, early closes and US daylight-saving transitions. Regular session is
the half-open interval `[market_open, market_close)`. A missing calendar or
timezone returns UNKNOWN and blocks entries, rather than claiming WATCH_READY.
The wrapper retains the old phase labels where possible. HOLIDAY is new in
decision session metadata; discovery maps holidays to its existing weekend label.

Reference for the library schedule contract:
https://pandas-market-calendars.readthedocs.io/en/latest/usage.html

## Completed Bars

Intraday timestamps are timezone-aware provider bar START times. A bar is
usable only once its start plus interval has elapsed, clipped to the exchange
close for a final short bar. Extended-hours bars cannot confirm regular entries.
Daily dates are session labels; the daily bar completes at the exchange close.

The last row is no longer removed blindly. It is retained when actually closed,
and removed while still live, regardless of the legacy `drop_last` argument.
Two completed bars suffice; a third live placeholder is no longer required.
Unordered/duplicate indices are rejected. Missing timezone or invalid session
bars cannot supply confirmation. Calendar failures return unavailable rather
than allowing entry or crashing the candidate evaluation.

## Active Confirmation Freshness

The existing technical confirmation calculation remains unchanged. Timing now
affects the final permission, before cooldown exceptions and pilot eligibility:

- Prior-session confirmation: WATCH, no new buy.
- Future or unfinished candle: WATCH, no new buy.
- Missing/ambiguous timestamps or unknown calendar: no new buy.
- Same-session stale intraday confirmation: WATCH.
- Valid completed same-session confirmation: other existing gates still decide.

Intraday freshness uses the existing configured lookback, not a new strategy
threshold: closed-candle age must be less than interval times lookback. With
30-minute bars and the existing default three-candle window, that is less than
90 minutes after candle close. This prevents a missing-data feed from making
an old morning confirmation appear fresh all afternoon.

Off-hours scans still retain completed historical confirmations as informational
references for staging. They do not become valid current-session confirmations.
The existing confirmation-disable and off-hours configuration fields have not
been removed; disabling confirmation intentionally bypasses this requirement.
Keep production confirmation enabled and off-hours buys disabled. The calendar
never authorizes a holiday entry even with the legacy off-hours override.

Existing HOLD / EXIT_STOP / TAKE_PARTIAL_PROFIT / TAKE_PROFIT actions are not
downgraded by entry-only timing gates. The position monitor was not modified.

## Decision JSON Compatibility

Existing action names and keys remain. New additive fields:

- `technical_confirmation_passed`: original technical pattern result.
- `confirmation_timing_valid`: fresh completed same-session confirmation.
- `confirmation_candle_close_timestamp`: resolved candle close.
- `confirmation_closed_age_minutes`: observation minus candle close.

`confirmation_age_minutes` remains age from candle START for compatibility.
Negative ages are not clamped to zero. `confirmation_freshness_shadow_only`
is now false. During entry evaluation, invalid timing sets
`entry_confirmation_passed=false`, `confirmation_status=TIMING_INVALID`, and
records the reason under `entry_gate_blockers`, not hidden as a capital block.
Existing historical decisions are not rewritten.

## Changed Files

- New `app/trading_clock.py`.
- `app/agent_risk.py`: clock wrapper, completed-bar selection, active freshness gate, metadata.
- `app/setups.py`: shared regular-session clock only; no signal thresholds changed.
- `agent/market_lens_ui_agent.py`: shared phase classification only.
- `pyproject.toml`: pinned calendar dependency.
- `tests/test_audit_regressions.py`: remove markers for the five repaired clock cases.
- `tests/test_agent_entry_gates.py`: active freshness contract, valid deterministic
  trading-date fixtures, BUY/exit integration and HTTP scan route tests.
- New `tests/test_trading_clock.py`: clock/bar/freshness contracts.

## Verification

`python -m pytest -q --tb=short`: **255 passed, 3 xfailed in 14.15 seconds**.
`git diff --check`: passed, with only local Git LF/CRLF conversion notices.

Coverage includes holidays, early close, DST, exact open/close, missing timezone,
calendar failure, future/live candles, first opening bar, two completed bars,
stale previous-session and same-session data, daily fallback and off-hours
references. Positive controls still BUY in BULL/NEUTRAL with valid timing.

Both `/scan` and `/ui/scan` were exercised through FastAPI TestClient, using
controlled candles and the real confirmation and strategy decision path.
Other market inputs were patched, charts disabled, and persistence omitted.
The tests verified HTTP 200, BUY versus WATCH, and JSON serialization of new
fields. This is local integration validation, not a live-provider or browser
production end-to-end test.

## Release Gate and Next Stage

Not yet production-ready by evidence: production deployment and live smoke
validation remain outstanding. Do not overwrite current result commits with
the older local tracker. Release only source/test/dependency/docs changes on
top of the latest production source after checking for intervening source edits.
Install the pinned dependency on both Render and the Actions environment.
After deployment, check current same-session confirmation, off-hours staging,
serialized metadata, scanner operation and unchanged monitor behavior.

The remaining expected failures belong to subsequent packages:

- AUDIT-EXIT-01: scanner must respect persisted position targets.
- AUDIT-METRIC-01: MFE must exclude observations after exit.
- AUDIT-SIGNAL-01: negative-benchmark relative strength ordering.

No changes were made to position size, capital limits, stops/targets, score
weights, Universe ranking/rotation rules, scheduler workflows, monitor execution,
Telegram, Excel or dashboard layout. Holiday phase classification necessarily
affects the existing choice between regular and off-hours discovery behavior.

Source rollback must never restore the baseline Excel over a newer portfolio.
