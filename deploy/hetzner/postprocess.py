from __future__ import annotations

import json
from pathlib import Path

from app.agent_dashboard import build_agent_dashboard, write_diagnostic_snapshot
from app.workbook_retention import enforce_tracker_size


def main() -> None:
    root = Path("/app")
    tracker = root / "agent_tracker/market_lens_agent_portfolio_budget_100k.xlsx"
    enforce_tracker_size(tracker)
    snapshot = build_agent_dashboard(root)
    if snapshot.get("status") != "ok":
        raise RuntimeError("Dashboard snapshot is not healthy; refusing persistence")
    path = root / "agent_results/dashboard_snapshot.json"
    path.write_text(json.dumps(snapshot, default=str, separators=(",", ":")), encoding="utf-8")
    write_diagnostic_snapshot(root, snapshot)
    print("Dashboard snapshot and tracker size guard completed")


if __name__ == "__main__":
    main()
