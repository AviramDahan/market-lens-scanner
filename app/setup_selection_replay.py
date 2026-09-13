from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any, Iterable


REPLAY_SCHEMA_VERSION = "first_match_replay_v1"
HORIZONS = (1, 3, 5, 10)


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def parse_timestamp(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value or "").strip().replace("Z", "+00:00"))
    if parsed.tzinfo is not None:
        return parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def exchange_session_identity(record: dict[str, Any]) -> tuple[str, str]:
    """Return New York session date and a buy-eligible/off-hours grouping."""
    value = record.get("market_session_timestamp") or record.get("timestamp")
    try:
        timestamp = datetime.fromisoformat(str(value or "").strip().replace("Z", "+00:00"))
        session_date = timestamp.date().isoformat()
    except (TypeError, ValueError):
        session_date = "UNKNOWN"
    phase = str(record.get("market_session_phase") or "UNKNOWN").upper()
    phase_group = "REGULAR" if phase == "REGULAR" else "OFF_HOURS"
    return session_date, phase_group


def load_decision_records(decision_dir: Path) -> tuple[list[dict[str, Any]], int]:
    records: list[dict[str, Any]] = []
    malformed = 0
    for path in sorted(decision_dir.glob("*.jsonl")):
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    item = json.loads(line)
                except (ValueError, json.JSONDecodeError):
                    malformed += 1
                    continue
                if isinstance(item, dict):
                    records.append(item)
    return records, malformed


def candidate_rr(record: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    """Recompute candidate geometry using the execution costs observed for the same ticker/run."""
    price = to_float(record.get("price"))
    low = to_float(candidate.get("buy_zone_low"), price)
    high = to_float(candidate.get("buy_zone_high"), low)
    theoretical_entry = low or price
    executable_entry = price if low <= price <= high or price > theoretical_entry else theoretical_entry
    stop = to_float(candidate.get("stop_loss"))
    target_1 = to_float(candidate.get("target_1"))
    target_2 = to_float(candidate.get("target_2"))
    gross_risk = executable_entry - stop
    gross_reward_1 = target_1 - executable_entry
    gross_reward_2 = target_2 - executable_entry
    gross_rr_1 = max(0.0, gross_reward_1) / gross_risk if gross_risk > 0 else 0.0
    gross_rr_2 = max(0.0, gross_reward_2) / gross_risk if gross_risk > 0 else 0.0

    primary_weight = to_float(record.get("primary_rr_weight"), 0.80)
    stretch_weight = to_float(record.get("stretch_rr_weight"), 0.20)
    if primary_weight + stretch_weight <= 0:
        primary_weight, stretch_weight = 0.80, 0.20
    total_weight = primary_weight + stretch_weight
    primary_weight /= total_weight
    stretch_weight /= total_weight

    spread = max(0.0, to_float(record.get("estimated_spread")))
    recorded_total_slippage = max(0.0, to_float(record.get("estimated_slippage")))
    entry_slippage = recorded_total_slippage / 2.0
    stop_slippage = recorded_total_slippage / 2.0
    target_slippage = entry_slippage * 0.50
    fees = max(0.0, to_float(record.get("estimated_fees")))
    half_spread = spread / 2.0
    net_entry = executable_entry + half_spread + entry_slippage + fees
    net_stop = stop - half_spread - stop_slippage
    net_target_1 = target_1 - half_spread - target_slippage - fees
    net_target_2 = target_2 - half_spread - target_slippage - fees
    net_risk = net_entry - net_stop
    net_reward_1 = max(0.0, net_target_1 - net_entry)
    net_reward_2 = max(0.0, net_target_2 - net_entry)
    net_rr_1 = net_reward_1 / net_risk if net_risk > 0 else 0.0
    net_rr_2 = net_reward_2 / net_risk if net_risk > 0 else 0.0
    return {
        "theoretical_entry": round(theoretical_entry, 4),
        "executable_entry": round(executable_entry, 4),
        "stop_loss": round(stop, 4),
        "target_1": round(target_1, 4),
        "target_2": round(target_2, 4),
        "gross_rr_1": round(gross_rr_1, 4),
        "gross_rr_2": round(gross_rr_2, 4),
        "gross_weighted_rr": round(gross_rr_1 * primary_weight + gross_rr_2 * stretch_weight, 4),
        "net_rr_1": round(net_rr_1, 4),
        "net_rr_2": round(net_rr_2, 4),
        "net_weighted_rr": round(net_rr_1 * primary_weight + net_rr_2 * stretch_weight, 4),
        "execution_cost_source": "same_ticker_same_run_observed_costs",
        "estimated_spread": round(spread, 4),
        "estimated_total_entry_stop_slippage": round(recorded_total_slippage, 4),
        "estimated_fees_per_share": round(fees, 4),
    }


def check(name: str, status: str, reason: str) -> dict[str, str]:
    return {"name": name, "status": status, "reason": reason}


def assess_candidate(
    record: dict[str, Any], candidate: dict[str, Any], *, active: bool
) -> dict[str, Any]:
    rr = candidate_rr(record, candidate)
    minimum_primary = to_float(record.get("minimum_primary_net_rr_required"), 0.80)
    minimum_weighted = to_float(record.get("minimum_net_rr_required"), 2.0)
    checks = [
        check(
            "valid_price_geometry",
            "PASS" if rr["stop_loss"] < rr["executable_entry"] < rr["target_1"] <= rr["target_2"] else "FAIL",
            "Candidate stop, entry and targets are ordered." if rr["stop_loss"] < rr["executable_entry"] < rr["target_1"] <= rr["target_2"] else "Candidate price levels are not valid long-trade geometry.",
        ),
        check(
            "market_regime",
            "FAIL" if str(record.get("market_regime") or "").upper() == "BEAR" else "PASS",
            f"Recorded market regime: {record.get('market_regime') or 'UNKNOWN'}.",
        ),
        check(
            "sector_regime",
            "FAIL" if str(record.get("sector_regime") or "").upper() == "WEAK" else "PASS",
            f"Recorded sector regime: {record.get('sector_regime') or 'UNKNOWN'}.",
        ),
        check(
            "regular_session",
            "PASS" if record.get("market_session_can_open_new_buy") is True else "FAIL",
            str(record.get("market_session_reason") or "Session eligibility was not recorded."),
        ),
        check(
            "earnings_blackout",
            "FAIL" if record.get("earnings_blackout") else "PASS",
            "Earnings blackout was active." if record.get("earnings_blackout") else "No earnings blackout was recorded.",
        ),
        check(
            "correlation",
            "FAIL" if record.get("correlation_warning") else "PASS",
            "Recorded ticker/portfolio correlation warning was active." if record.get("correlation_warning") else "Recorded ticker/portfolio correlation was acceptable.",
        ),
        check(
            "primary_net_rr",
            "PASS" if rr["net_rr_1"] >= minimum_primary else "FAIL",
            f"Candidate TP1 net R/R {rr['net_rr_1']:.2f}; required {minimum_primary:.2f}.",
        ),
        check(
            "weighted_net_rr",
            "PASS" if rr["net_weighted_rr"] >= minimum_weighted else "FAIL",
            f"Candidate weighted net R/R {rr['net_weighted_rr']:.2f}; required {minimum_weighted:.2f}.",
        ),
    ]
    if active:
        checks.extend(
            [
                check(
                    "professional_setup_score",
                    "PASS" if to_float(record.get("setup_score")) >= to_float(record.get("minimum_setup_score_required"), 0.45) else "FAIL",
                    f"Recorded active professional score {to_float(record.get('setup_score')):.2f}.",
                ),
                check(
                    "entry_confirmation",
                    "PASS" if record.get("entry_confirmation_passed") else "FAIL",
                    str(record.get("confirmation_reason") or "Active confirmation result unavailable."),
                ),
                check(
                    "target_feasibility",
                    "PASS" if str(record.get("target_feasibility_status") or "").upper() == "OK" else "FAIL",
                    f"Recorded active target status: {record.get('target_feasibility_status') or 'UNKNOWN'}.",
                ),
            ]
        )
    else:
        checks.extend(
            [
                check("professional_setup_score", "UNASSESSABLE", "Alternative-specific professional score was not persisted."),
                check("entry_confirmation", "UNASSESSABLE", "Completed-candle confirmation was calculated only for the active setup."),
                check("target_feasibility", "UNASSESSABLE", "ATR/structure feasibility was calculated only for the active setup."),
            ]
        )
    if record.get("cooldown_active"):
        cooldown_status = "PASS" if active and record.get("cooldown_exception_used") else "FAIL" if active else "UNASSESSABLE"
        cooldown_reason = str(record.get("cooldown_reason") or "Ticker cooldown was active.")
        if not active:
            cooldown_reason += " Alternative exception eligibility depends on its missing score and confirmation."
    else:
        cooldown_status, cooldown_reason = "PASS", "No ticker cooldown was active."
    checks.append(check("stop_cooldown", cooldown_status, cooldown_reason))
    checks.extend(
        [
            check(
                "sector_factor_capital_sizing" if not active else "recorded_capital_sizing",
                "UNASSESSABLE" if not active else ("FAIL" if record.get("capital_blockers") else "PASS"),
                "Alternative position size and post-trade exposures were not persisted." if not active else ("Recorded capital blockers: " + "; ".join(record.get("capital_blockers") or []) if record.get("capital_blockers") else "No active capital blocker was recorded."),
            )
        ]
    )
    statuses = Counter(item["status"] for item in checks)
    eligibility = "FAIL" if statuses["FAIL"] else "UNASSESSABLE" if statuses["UNASSESSABLE"] else "PASS"
    return {
        "setup_type": candidate.get("setup_type"),
        "is_active_legacy_candidate": active,
        "legacy_score": to_float(candidate.get("legacy_score")),
        "shadow_setup_normalized_score": to_float(candidate.get("shadow_setup_normalized_score")),
        "candidate_rr": rr,
        "gate_evidence": checks,
        "counterfactual_entry_eligibility": eligibility,
        "evidence_summary": dict(statuses),
    }


def deduplicated_signals(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep first ticker/setup observation per exchange date and session group."""
    signals: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    ordered = []
    for record in records:
        try:
            timestamp = parse_timestamp(record.get("timestamp"))
        except (TypeError, ValueError):
            continue
        ticker = str(record.get("ticker") or "").upper().strip()
        session_date, phase_group = exchange_session_identity(record)
        for candidate in record.get("setup_candidates") or []:
            if not ticker or not isinstance(candidate, dict) or not candidate.get("setup_type"):
                continue
            key = (session_date, phase_group, ticker, str(candidate["setup_type"]))
            ordered.append((timestamp, key, record, candidate, session_date, phase_group))
    for timestamp, key, record, candidate, session_date, phase_group in sorted(ordered, key=lambda item: item[0]):
        signals.setdefault(
            key,
            {
                "timestamp": timestamp,
                "session_date": session_date,
                "session_group": phase_group,
                "record": record,
                "candidate": candidate,
            },
        )
    return list(signals.values())


def observed_price_outcomes(
    signal: dict[str, Any], daily_prices: dict[str, dict[str, float]]
) -> dict[str, float | None]:
    record = signal["record"]
    timestamp = signal["timestamp"]
    ticker = str(record.get("ticker") or "").upper()
    entry = candidate_rr(record, signal["candidate"])["executable_entry"]
    later = sorted(
        (day, price)
        for day, price in daily_prices.get(ticker, {}).items()
        if day > signal["session_date"]
    )
    output: dict[str, float | None] = {}
    for horizon in HORIZONS:
        value = (later[horizon - 1][1] / entry - 1.0) * 100 if entry > 0 and len(later) >= horizon else None
        output[f"return_after_{horizon}_scan_days_pct"] = round(value, 4) if value is not None else None
    return output


def build_setup_selection_replay(records: list[dict[str, Any]], *, malformed_records: int = 0) -> dict[str, Any]:
    original_actions = [(item.get("final_action"), item.get("reason")) for item in records]
    prices: dict[str, dict[str, tuple[datetime, float]]] = defaultdict(dict)
    for record in records:
        try:
            timestamp = parse_timestamp(record.get("timestamp"))
        except (TypeError, ValueError):
            continue
        ticker = str(record.get("ticker") or "").upper().strip()
        price = to_float(record.get("price"))
        day, _phase_group = exchange_session_identity(record)
        prior = prices[ticker].get(day)
        if ticker and price > 0 and (prior is None or timestamp > prior[0]):
            prices[ticker][day] = (timestamp, price)
    daily_prices = {ticker: {day: value[1] for day, value in values.items()} for ticker, values in prices.items()}

    signals = deduplicated_signals(records)
    replayed: list[dict[str, Any]] = []
    status_counts: Counter[str] = Counter()
    type_counts: Counter[str] = Counter()
    active_gate_gaps: Counter[str] = Counter()
    alternative_gate_gaps: Counter[str] = Counter()
    alternative_count = 0
    for signal in signals:
        record = signal["record"]
        candidate = signal["candidate"]
        active = str(candidate.get("setup_type") or "") == str(record.get("setup_type") or "")
        assessment = assess_candidate(record, candidate, active=active)
        gap_counter = active_gate_gaps if active else alternative_gate_gaps
        for item in assessment["gate_evidence"]:
            if item["status"] != "PASS":
                gap_counter[f"{item['status']}:{item['name']}"] += 1
        status_counts[assessment["counterfactual_entry_eligibility"]] += 1
        type_counts[str(candidate.get("setup_type") or "UNKNOWN")] += 1
        alternative_count += not active
        replayed.append(
            {
                "timestamp": signal["timestamp"].isoformat(),
                "exchange_session_date": signal["session_date"],
                "session_group": signal["session_group"],
                "ticker": str(record.get("ticker") or "").upper(),
                "active_final_action": record.get("final_action"),
                "active_setup_type": record.get("setup_type"),
                "market_regime": record.get("market_regime"),
                "sector_regime": record.get("sector_regime"),
                **assessment,
                "observed_price_outcomes": observed_price_outcomes(signal, daily_prices),
            }
        )
    assert original_actions == [(item.get("final_action"), item.get("reason")) for item in records]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "schema_version": REPLAY_SCHEMA_VERSION,
        "mode": "READ_ONLY_FIRST_MATCH_SHADOW_REPLAY",
        "active_trading_logic_changed": False,
        "active_policy": "FIRST_MATCH_LEGACY",
        "methodology": {
            "deduplication": "First ticker/setup observation per New York exchange date and REGULAR/OFF_HOURS group; later observations in the same group are excluded to avoid hindsight selection.",
            "execution_geometry": "Candidate-specific entry/stop/targets with same-ticker same-run recorded spread/slippage/fees.",
            "eligibility": "PASS is emitted only when every required gate has candidate-specific evidence. Missing evidence is UNASSESSABLE, never inferred.",
            "outcomes": "Later recorded scan prices are directional diagnostics, not OHLC execution or target/stop path reconstruction.",
        },
        "limitations": [
            "Historical alternative candidates lack candidate-specific professional score and completed-candle confirmation.",
            "Historical alternative candidates lack candidate-specific ATR/market-structure target validation and position sizing.",
            "Later scan prices cannot establish whether stop or target touched first within a session.",
            "This report cannot justify replacing FIRST_MATCH_LEGACY or activating a different setup.",
        ],
        "sample": {
            "decision_records": len(records),
            "malformed_records": malformed_records,
            "deduplicated_candidate_signals": len(replayed),
            "deduplicated_alternative_signals": alternative_count,
            "unique_tickers": len({item["ticker"] for item in replayed}),
            "candidate_types": dict(type_counts.most_common()),
            "eligibility_statuses": dict(status_counts),
            "active_gate_gaps": dict(active_gate_gaps.most_common()),
            "alternative_gate_gaps": dict(alternative_gate_gaps.most_common()),
        },
        "signals": replayed,
    }


def summary_markdown(report: dict[str, Any]) -> str:
    sample = report["sample"]
    lines = [
        "# FIRST_MATCH_LEGACY Offline Replay",
        "",
        "This is a read-only shadow report. It did not change active setup selection or any trade action.",
        "",
        "## Sample",
        "",
        f"- Decision records: {sample['decision_records']}",
        f"- Deduplicated candidate signals: {sample['deduplicated_candidate_signals']}",
        f"- Alternative signals: {sample['deduplicated_alternative_signals']}",
        f"- Unique tickers: {sample['unique_tickers']}",
        f"- Eligibility evidence: {json.dumps(sample['eligibility_statuses'], sort_keys=True)}",
        f"- Active gate gaps: {json.dumps(sample['active_gate_gaps'], sort_keys=True)}",
        f"- Alternative gate gaps: {json.dumps(sample['alternative_gate_gaps'], sort_keys=True)}",
        "",
        "## Interpretation",
        "",
        "An alternative with a higher normalized detector score is not automatically a better or eligible trade. "
        "Historical alternatives are marked UNASSESSABLE whenever their own confirmation, professional score, "
        "target feasibility, or sizing evidence was not persisted.",
        "",
        "## Limitations",
        "",
    ]
    lines.extend(f"- {item}" for item in report["limitations"])
    lines.extend(["", "Do not promote an alternative setup policy from this report alone.", ""])
    return "\n".join(lines)


def write_setup_selection_replay(report: dict[str, Any], output_dir: Path) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = output_dir / f"first_match_replay_{stamp}.json"
    md_path = output_dir / f"first_match_replay_{stamp}.md"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    md_path.write_text(summary_markdown(report), encoding="utf-8")
    return {"json": json_path, "markdown": md_path}
