# Agent Results Retention

Date: 2026-09-12

The active Market Lens repository is optimized for production runs and GitHub
Actions checkout speed. Historical generated outputs are preserved separately so
the active checkout does not need to carry every chart, screenshot, and scan
artifact.

## Archive

Historical structured data was archived in:

https://github.com/AviramDahan/market-lens-data-archive/releases/tag/data-snapshot-2026-09-12-lean

The archive keeps:

- all Decision JSONL files
- daily, weekly, run, diagnostic, runtime, and monitor metadata
- the portfolio tracker workbook
- the dashboard snapshot and Telegram notification ledger
- selected trade-relevant charts only

The archive intentionally does not keep every generated chart or screenshot.
Those files are derived debug media and were the main cause of oversized
checkouts.

## Active Repository

The active repository keeps only the files needed for current dashboard
operation, recent QA, live monitoring, and GitHub/Render sync:

- current `dashboard_snapshot.json`
- current tracker workbook
- latest decisions, summaries, runtime, diagnostics, and monitor records
- current dashboard-referenced charts/screenshots
- selected trade-relevant charts

## Policy

Do not remove structured trading data without archiving it first.

Charts and screenshots should remain bounded. They are useful for visual QA, but
they are not the primary research record. Decision JSONL, summaries, tracker
state, and position monitor events are the source of truth for future strategy
analysis.
