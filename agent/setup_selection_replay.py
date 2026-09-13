from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.setup_selection_replay import (
    build_setup_selection_replay,
    load_decision_records,
    write_setup_selection_replay,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only FIRST_MATCH_LEGACY setup-selection replay")
    parser.add_argument("--decision-dir", type=Path, default=ROOT / "agent_results" / "decisions")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "agent_results" / "capital_replay")
    args = parser.parse_args()

    records, malformed = load_decision_records(args.decision_dir)
    report = build_setup_selection_replay(records, malformed_records=malformed)
    paths = write_setup_selection_replay(report, args.output_dir)
    sample = report["sample"]
    print(
        "FIRST_MATCH replay complete: "
        f"{sample['deduplicated_candidate_signals']} candidate signals, "
        f"{sample['deduplicated_alternative_signals']} alternatives"
    )
    print(f"Eligibility evidence: {sample['eligibility_statuses']}")
    print(f"JSON: {paths['json']}")
    print(f"Markdown: {paths['markdown']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
