from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.qualified_sector_analysis import download_bars
from app.weak_sector_override import (
    build_measurement_summary,
    load_observations,
    write_measurement_summary,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only WEAK_SECTOR_OVERRIDE_V1 measurement summary"
    )
    parser.add_argument(
        "--observations",
        type=Path,
        default=ROOT / "agent_results" / "experiments" / "weak_sector_override_v1_observations.jsonl",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "agent_results" / "summaries",
    )
    parser.add_argument("--outcome-end", type=date.fromisoformat, default=date.today())
    parser.add_argument("--skip-market-data", action="store_true")
    args = parser.parse_args()

    observations = load_observations(args.observations)
    eligible = [
        item
        for item in observations
        if item.get("signal_eligible") is True or item.get("control_eligible") is True
    ]
    bars = {}
    market_data_errors = {}
    if eligible and not args.skip_market_data:
        start = min(date.fromisoformat(item["session_date"]) for item in eligible)
        bars, market_data_errors = download_bars(
            [str(item.get("ticker") or "") for item in eligible], start, args.outcome_end
        )
    summary = build_measurement_summary(observations, bars)
    summary["market_data_errors"] = market_data_errors
    paths = write_measurement_summary(summary, args.output_dir)
    print(
        json.dumps(
            {
                "signals": summary["qualifying_signal_count"],
                "matched_controls": summary["matched_control_count"],
                "review_progress": summary["review_progress"],
                "review_ready": summary["review_ready"],
                "json": str(paths["json"]),
                "markdown": str(paths["markdown"]),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
