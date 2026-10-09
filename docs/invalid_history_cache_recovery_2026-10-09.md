# Invalid history cache recovery

The first post-memory-fix production scan completed and persisted 179 of 180
cards. SNPS was unavailable because the web process cached a one-row weekly
history. Its focused retries reused that inadequate response. A clean provider
probe returned 128 daily, 414 hourly and 261 weekly rows for the same ticker.

Validated scanner history reads now evict the matching cached frame when minimum
history validation fails. The next existing recovery attempt can fetch again.
Concurrent newer responses are retained; valid history remains cached. The
minimum daily/hourly/weekly requirements (50/100/200 rows), cache TTL, strategy,
scoring and all trading gates are unchanged. Short intraday quote histories are
not subjected to scanner-history minimums.

Tests cover a truncated response followed by valid data, persistent insufficient
history, reuse of valid caches, a concurrent successful refresh and short quote
frames. No historical or portfolio records are modified by this change.
