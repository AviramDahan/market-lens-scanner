"""Rebuild the 2026-W38 report from verified Git-recovered inputs.

Run with --recovery pointing to the separately retained recovery ZIP contents.
This script changes only the weekly JSON and Markdown summary files.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.agent_dashboard import compute_full_trade_performance, compute_realized_pnl, read_trades
from app.performance_summary import build_period_summary, collect_records, write_json, write_markdown

BEFORE = "f2a3e73c520d17ec1a9f949f77a9ebbdecd0975d"
AFTER = "ea81994beacde7c8c9c110840a61a51498647b5e"
WORKBOOK = "agent_tracker/market_lens_agent_portfolio_budget_100k.xlsx"
SNAPSHOT = "agent_results/dashboard_snapshot.json"
REPORT = ROOT / "agent_results/summaries/weekly_summary_2026-W38.json"
ARCHIVE_URL = "https://github.com/AviramDahan/market-lens-data-archive/releases/tag/measurement-history-recovery-2026-09-22"


def git_blob(ref: str, path: str) -> bytes:
    return subprocess.check_output(["git", "show", f"{ref}:{path}"], cwd=ROOT)


def historical_trades(ref: str) -> list[dict]:
    book = load_workbook(io.BytesIO(git_blob(ref, WORKBOOK)), read_only=True, data_only=True)
    try:
        return read_trades(book)
    finally:
        book.close()


def historical_snapshot(ref: str) -> dict:
    return json.loads(git_blob(ref, SNAPSHOT))


def validate_recovery(recovery: Path) -> None:
    manifest = json.loads((recovery / "recovery_manifest.json").read_text(encoding="utf-8"))
    index = json.loads((recovery / "decisions/archive/index.json").read_text(encoding="utf-8"))
    selected = [item for item in manifest["files"] if item["path"].startswith("agent_results/decisions/market_lens_agent_202609")
                and "20260914" <= Path(item["path"]).name[18:26] <= "20260920"]
    assert len(selected) == 155, len(selected)
    for item in selected:
        archive = recovery / Path(item["archive"].replace("\\", "/"))
        assert archive.is_file(), archive
        assert index[Path(item["path"]).name] == archive.name
        raw = gzip.decompress(archive.read_bytes())
        assert len(raw) == item["bytes"]
        assert hashlib.sha256(raw).hexdigest() == item["sha256"]
        for line in raw.splitlines():
            if line.strip():
                json.loads(line)


def rebuild(recovery: Path) -> dict:
    validate_recovery(recovery)
    opening = historical_snapshot(BEFORE)
    closing = historical_snapshot(AFTER)
    start = opening["summary"]
    end = closing["summary"]
    prior_rows = historical_trades(BEFORE)
    rows = historical_trades(AFTER)
    assert len(prior_rows) == 87 and len(rows) == 93
    assert closing["status"] == "ok"
    assert round(end["equity_ils"] - start["equity_ils"], 2) == -298.49

    events = compute_realized_pnl(rows)["trades"]
    completed = compute_full_trade_performance(rows)["closed"]
    portfolio = {
        "currency": end["currency"],
        "starting_capital": end["starting_capital_ils"],
        "open_positions_start": start["open_positions"],
        "open_positions_end": end["open_positions"],
        "cash": end["cash_ils"],
        "exposure": end["exposure_ils"],
        "open_risk": end["open_risk_ils"],
        "realized_pnl": end["realized_pnl_ils"],
        "unrealized_pnl": end["unrealized_pnl_ils"],
        "total_portfolio_value": end["equity_ils"],
        "cumulative_return_pct": round((end["equity_ils"] / end["starting_capital_ils"] - 1) * 100, 4),
    }
    decision_dir = recovery / "decisions"
    records, files = collect_records(decision_dir, period="weekly", target_date=date(2026, 9, 20))
    assert len(files) == 155 and len(records) == 20974, (len(files), len(records))
    report = build_period_summary(
        period="weekly", target_date=date(2026, 9, 20), decision_dir=decision_dir,
        portfolio=portfolio, current_decision_path=decision_dir / "missing.jsonl",
        run_id=closing["latest_run"]["run_id"], trade_events=events,
        completed_trades=completed,
    )
    assert report["total_scans"] == 155 and report["total_tickers_scanned"] == 20974
    assert report["BUY_SIMULATED_count"] == 2
    assert report["positions_closed_today"] == 3
    assert report["TP1_hits"] == 1 and report["SL_hits"] == 3
    assert report["period_realized_pnl"] == -14.56
    assert report["open_positions_start"] == start["open_positions"]
    assert report["open_positions_end"] == end["open_positions"]
    assert sum(report["decision_cohorts"].values()) == 20974
    report["history_coverage_status"] = "GIT_RECOVERABLE_WEEK_WITH_VERIFIED_TRADE_LEDGER"
    report["history_coverage_note"] = (
        "Decision-derived metrics recomputed from all 155 Git-recoverable W38 runs. "
        "Trade and portfolio metrics use the recorded end-of-week workbook and snapshots. "
        "Files never committed to Git and unrecorded future outcomes cannot be inferred."
    )
    report["historical_rebuild"] = {
        "source_release": ARCHIVE_URL,
        "opening_source_commit": BEFORE,
        "closing_source_commit": AFTER,
        "original_partial_summary_commit": "358be7c8",
        "decision_files_verified": len(files),
        "decision_records_verified": len(records),
        "ledger_rows_start": len(prior_rows),
        "ledger_rows_end": len(rows),
        "opening_equity": start["equity_ils"],
        "closing_equity": end["equity_ils"],
        "recorded_equity_change": round(end["equity_ils"] - start["equity_ils"], 2),
        "realized_pnl_change": round(end["realized_pnl_ils"] - start["realized_pnl_ils"], 2),
        "unrealized_pnl_change": round(end["unrealized_pnl_ils"] - start["unrealized_pnl_ils"], 2),
        "equity_change_basis": "recorded_snapshots_outside_regular_session",
        "max_drawdown_basis": "fully_closed_trade_sequence_dollars_not_marked_portfolio_drawdown",
    }
    report["runtime_metrics"]["decision_files"] = [f"agent_results/decisions/{path.name.split('.jsonl')[0]}.jsonl" for path in files]
    report["errors_retries_timeouts"] = []
    report["runtime_error_coverage_status"] = "UNKNOWN_HISTORICAL_RUNTIME_TELEMETRY"
    report["runtime_error_coverage_note"] = "Runtime and retry telemetry not independently reconstructed for this historical week."
    assert report["decision_cohorts"] == {
        "EXISTING_POSITION": 399,
        "FIRST_MATCH_LEGACY": 9881,
        "QUALIFIED_PROFESSIONAL_SCORE_V1": 10694,
    }
    assert report["SKIP_count"] == 17594 and report["WATCH_count"] == 2677
    assert report["capital_measurement_cohorts"] == {"DYNAMIC_CAPITAL_FIELDS_PRESENT": 20974}
    assert round(report["historical_rebuild"]["realized_pnl_change"] +
                 report["historical_rebuild"]["unrealized_pnl_change"], 2) == report["historical_rebuild"]["recorded_equity_change"]
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--recovery", type=Path, required=True)
    args = parser.parse_args()
    report = rebuild(args.recovery.resolve())
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    write_json(REPORT, report)
    markdown = REPORT.with_suffix(".md")
    write_markdown(markdown, "Weekly Performance Summary", report)
    with markdown.open("a", encoding="utf-8") as handle:
        handle.write(
            "\n\n## Historical rebuild\n\n"
            f"- Decision coverage: {report['total_scans']} verified runs, "
            f"{report['total_tickers_scanned']} records from the Git recovery archive.\n"
            f"- SKIP: {report['SKIP_count']}; WATCH: {report['WATCH_count']}; "
            f"No Trade setups: {report['NO_TRADE_count']}.\n"
            f"- Opened: {report['positions_opened_today']}; fully closed: "
            f"{report['positions_closed_today']}; TP1 partial exits: {report['TP1_hits']}; "
            f"stop exits: {report['SL_hits']}.\n"
            f"- Weekly realized PnL: ${report['period_realized_pnl']:.2f}; "
            f"recorded equity change: ${report['historical_rebuild']['recorded_equity_change']:.2f}.\n"
            f"- Closed-trade money win rate: {report['win_rate']:.2f}% "
            f"({report['total_closed_trades']} fully closed trades).\n"
            f"- Opening workbook: `{BEFORE}`; closing workbook: `{AFTER}`.\n"
            f"- Recovered decision data: {ARCHIVE_URL}\n\n"
            "Runtime/retry details and unrecorded future outcomes are unavailable. "
            "The equity change compares recorded snapshots outside regular session.\n"
        )
    print(json.dumps({key: report[key] for key in (
        "total_scans", "total_tickers_scanned", "WATCH_READY_count", "SKIP_count",
        "period_realized_pnl", "total_closed_trades", "win_rate")}, indent=2))


if __name__ == "__main__":
    main()
