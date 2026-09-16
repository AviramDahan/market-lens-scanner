# Qualified selection and costed paper execution v1

User-approved activation replaces FIRST_MATCH_LEGACY for new entries. This supersedes earlier observation-only activation notes; the five existing detectors, BEAR block, neutral pilot rules, confirmation freshness, and capital/exposure limits remain in force. There is no new shadow waiting period or paid dependency.

Each matching candidate is evaluated independently through the existing full entry gates against the same portfolio snapshot. Only BUY_SIMULATED candidates can win. Rank by professional adjusted score, TP1 net R/R, weighted net R/R, then setup name. The ledger records every candidate's outcome and the selection reason. Existing positions keep their original exit plan and cannot switch setups.

New entries carry strategy_version=qualified_selection_v1 and execution_model_version=costed_paper_v1. The paper ledger uses all-in entry and exit prices including the existing estimated half spread, slippage and configured per-share fees. Cost assumptions are frozen at entry. Targets are modeled as market-on-touch fills; stops use the lower of trigger and observed quote/bar open before costs. Costs are estimates, not broker fills. Missing or invalid required prices/costs fail visibly. Position risk includes expected stop execution costs, including after the stop moves to entry.

Legacy lots and historical rows retain their original accounting; no retrospective cost reconstruction. Dashboard cohorts separate legacy closed P/L from upgraded closed net P/L. Modeled costs include open and closed lots and must not be subtracted a second time from net P/L. Gap loss is reflected in proceeds, not counted again as fees.

TP1 partial quantity, move-to-entry, TP2/full remaining closure, conservative same-bar ordering and event deduplication remain unchanged. No production trade is manufactured for validation. Better selection and realistic accounting do not establish profitability.
