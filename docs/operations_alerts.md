# Market Lens operations alerts

`Market Lens | System Alerts` is a private Telegram group for operational failures.
It is separate from the existing trade-notification group. The same bot is a
member of both groups; the operations group ID is held in the repository secret
`MARKET_LENS_TELEGRAM_OPS_CHAT_ID`. The bot token remains in
`MARKET_LENS_TELEGRAM_BOT_TOKEN`.

The `Market Lens Operations Health Check` GitHub workflow runs three lightweight
read-only checks per weekday. It tests the Hetzner `/health`, `/agent`, and
`/agent/data` endpoints; a nonempty scan with at least 95% coverage; scan
freshness during regular New York trading hours; and monitor freshness when
open positions exist during that session. A failed check sends a short alert
to the operations group and fails the workflow. A healthy check sends nothing.
The workflow never dispatches scans, changes the paper portfolio, or sends to
the trade-notification group.

Use `workflow_dispatch` with `test_telegram=true` for a single diagnostic
message to the operations group. This mode does not run a scan or monitor and
does not change portfolio data.

This is periodic health monitoring, not a raw application-log stream. A failure
between checks can take until the next check to be reported. GitHub Actions
failures before the Python check starts are visible in Actions but cannot be
reported by this script. Repeated failed checks may generate repeated alerts;
the messages are intentionally isolated from trading notifications.

The Hetzner single-writer runner also sends a direct operations alert after a
scanner or monitor run fails, when the monitor reports `MONITOR_DEGRADED`, or
when a persisted BUY/TP/SL notification cannot be delivered. It uses the bot
token from the host runtime environment and the operations group ID from
`runtime.defaults.env` (or a host override). Each event type is limited to one
message per hour; only a successful Telegram response records a receipt.
Alert text excludes exception details and credentials. The trade group remains
the destination for successful BUY/TP/SL events. The host alerts are best effort:
loss of network/Telegram/host power can prevent delivery; the periodic workflow
provides a separate backstop. The live host runner must have the bot token in
its runtime environment for direct alerts to work.
