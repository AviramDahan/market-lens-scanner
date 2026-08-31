from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.capital_replay import build_capital_replay_report, write_capital_replay_report


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only Market Lens capital-management replay")
    parser.add_argument(
        "--workbook",
        type=Path,
        default=ROOT / "agent_tracker" / "market_lens_agent_portfolio_budget_100k.xlsx",
    )
    parser.add_argument("--decision-dir", type=Path, default=ROOT / "agent_results" / "decisions")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "agent_results" / "capital_replay")
    parser.add_argument("--starting-capital", type=float, default=100_000.0)
    parser.add_argument("--idle-cash-yield", type=float, default=0.05)
    parser.add_argument("--skip-candidate-analysis", action="store_true")
    args = parser.parse_args()

    report = build_capital_replay_report(
        workbook_path=args.workbook,
        decision_dir=args.decision_dir,
        starting_capital=args.starting_capital,
        idle_cash_annual_yield=args.idle_cash_yield,
        include_candidate_analysis=not args.skip_candidate_analysis,
    )
    paths = write_capital_replay_report(report, args.output_dir)
    print(f"Capital replay complete: {report['sample']['closed_trades']} closed trades")
    for scenario in report["scenarios"]:
        print(
            f"{scenario['scenario']}: PnL ${scenario['trading_pnl']:,.2f}, "
            f"return {scenario['trading_return_pct']:.2f}%, "
            f"avg exposure {scenario['average_time_weighted_exposure_pct']:.2f}%"
        )
    print(f"JSON: {paths['json']}")
    print(f"Markdown: {paths['markdown']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
