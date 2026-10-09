# Scanner memory recovery and measurement correction

The October 9 12:30 UTC scanner read all 180 result cards. The kernel killed its
Python process at 12:38:58 UTC for exceeding the worker's 2500 MiB cgroup limit.
The failed stage was not promoted. This was not an authentication failure, scan
timeout, or insufficient ticker coverage. The historical stage remains retained.

The workbook contains a roughly 562 MB uncompressed Setup Watchlist XML sheet.
Previously, daily/weekly reports loaded complete nested decision evidence while
this workbook was still resident. The scanner now saves and releases the workbook
before assembling reports. JSONL is streamed and summary readers retain only the
fields their existing metrics consume. Full decision evidence and historical
workbook records remain stored; normal full-evidence readers remain compatible.

The accompanying WEAK-sector correction is measurement-only. It resolves the
preliminary sector short-circuit preventing an isolated evaluation of all other
gates. See `docs/weak_sector_override_v1.md` for revision and history semantics.

Regression coverage compares projected summaries with full-evidence summaries,
verifies streaming compressed JSONL, exercises real preliminary/risk gates on
copied inputs, preserves active decisions, retains legacy observations and checks
measurement failure fallback. The trading strategy, thresholds, universe,
portfolio limits and exit execution are unchanged.

Release validation must also run a full scanner in an isolated stage under the
unchanged production memory limit, with Telegram delivery disabled. Verify saved
Excel integrity, decision count, runtime status, report files and peak memory.
A passing unit suite alone does not establish recovery from the production OOM.
