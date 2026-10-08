# Hetzner runtime reliability (2026-10-08)

This release changes paper-trading infrastructure and scan candidate supply, not entry gates, risk limits, or exits.

- A committed but unpushed portfolio update is retried on the next scheduled run. The host only pushes when `origin/main` is an ancestor of local `HEAD`; divergent histories still stop for manual reconciliation. A pending Telegram outbox is delivered only after the backup is current and the worker image matches deployed code. A receipt-only interrupted state is committed and backed up without resending.
- The Smart Universe source rotation widens for large requests (maximum 120 symbols per healthy sector before quality checks). Liquidity, volatility, market-cap and sector eligibility checks remain unchanged. The requested 175 fresh names is a target, not a reason to admit ineligible names. Watch/open-position carry-forward stays outside that target.
- Upstream `MMC` is normalized to `MRSH`, the current Marsh symbol. Scanner-only candidates with two recent confirmed failures for fewer than 200 weekly bars are deferred for seven days and then retried; open-position carry-forward is never suppressed. The 200-week requirement remains unchanged.
- The host sends a rate-limited operations alert when available disk falls below 12 GiB (`MARKET_LENS_MIN_FREE_GB` overrides this). Nothing is deleted or pruned automatically. The repository stores frequent XLSX snapshots, so Git history will continue growing. A separate durable archival design is required before disk or GitHub limits become binding; do not discard past results or prune the shared AI Trader Docker cache.

Verification: run `python -m pytest -q`, inspect `/health`, `/agent`, `/agent/data`, then confirm a new scanner record and monitor heartbeat on the host. A `PARTIAL_OK` record must list its missing tickers; it must not be reported as a complete scan. A successful no-event monitor proves evaluation only, not a live TP/SL touch.
