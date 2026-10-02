# WEAK_SECTOR_OVERRIDE_V1

Status: measurement-only infrastructure. It does not modify active trading decisions,
position sizing, portfolio state, alerts, thresholds, or the sector gate.

## Existing foundation

The experiment extends the read-only sector analysis in
`app/decision_quality_analysis.py` and the audit in
`docs/qualified_selection_sector_audit_2026-10-02.md`. The existing conservative
5-minute replay remains authoritative for signal outcomes: regular-session bars,
frozen execution costs, stop-first ordering inside an ambiguous bar, half exit at
TP1, stop moved to entry, then TP2.

## Runtime measurement

Each active decision receives a `weak_sector_override_v1` evidence object. The first
regular-session observation for each New York date, ticker, setup and active entry
path is persisted to:

`agent_results/experiments/weak_sector_override_v1_observations.jsonl`

A WEAK-sector signal is eligible only when the sector gate is the sole failure and
every other gate is an explicit `PASS`, including executable quantity, cash,
exposure and portfolio heat. Missing evidence is `UNASSESSABLE`. Standard and
Neutral Pilot thresholds are evaluated independently from the persisted active path.

The observation freezes the strategy/execution versions, entry, stop, targets,
R/R, cost policy, sizing and market/sector context. It never calls the execution
path and never changes `final_action` or the workbook portfolio.

Eligible STRONG-sector observations are retained as controls. Comparison uses all
controls from the same setup type, market regime and setup-score bucket; examples
are not selected after their outcome is known.

## Summary command

```powershell
python agent/weak_sector_override_summary.py
```

For schema validation without downloading market data:

```powershell
python agent/weak_sector_override_summary.py --skip-market-data
```

Outputs:

- `agent_results/summaries/weak_sector_override_v1_summary.json`
- `agent_results/summaries/weak_sector_override_v1_summary.md`

Closed and censored outcomes are reported separately. Signal results are explicitly
not portfolio returns. When no signal qualifies, the report keeps the count at zero
and lists persisted ineligibility reasons.

## Review gate

Review may begin only after at least 50 signals, 30 closed outcomes, 20 trading days
and three sectors. Meeting these minimums permits a review; it does not prove an edge,
activate the override, or authorize an active policy change.

## Deployment

The measurement is deployed through PR #6, followed by the observation-filter fix
in PR #7. Its observations are committed to `main`. The reminder below remains a
separate, unmerged change until explicitly approved for release.

## One-time Telegram review reminder

The separate `market-lens-weak-sector-review.yml` workflow checks the reminder at
07:00 UTC daily. On 2026-10-13 this is 10:00 Asia/Jerusalem. A delayed run sends
at its first execution after the due time. It does not inspect market hours,
signal count, or `review_ready` before sending.

The job fetches the latest committed observation JSONL from `origin/main`, runs
`python agent/weak_sector_override_summary.py` in a temporary directory, and sends
the requested review message with the collection period, eligibility counts,
outcome status, and main rejection reasons. If the summary fails, it sends a
failure notice instead. It uses the same Telegram bot and chat secrets as the
existing Agent notifications. A forum topic can be selected with the optional
`MARKET_LENS_TELEGRAM_REVIEW_THREAD_ID` repository secret; no topic secret is
currently configured in the repository.

A stable delivery key is stored in `agent_results/telegram_notifications.jsonl`
on `main` through the existing receipt persister. The workflow shares the
`market-lens-repo-writes` concurrency group with the scanner and monitor, and
checks the latest committed receipt before sending. A failed Telegram response
does not produce a receipt, so the next scheduled run retries. A successful
Telegram response followed by a process crash before the receipt push has an
unavoidable ambiguous state because Telegram's sendMessage API has no idempotency
key; the workflow fails visibly and the next run may repeat that one message.
The reminder never changes trading decisions or the portfolio.
