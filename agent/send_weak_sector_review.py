"""One-time, read-only review of the persisted weak-sector measurement."""
from __future__ import annotations

import html
import json
import os
import subprocess
import sys
import tempfile
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.persist_notification_receipts import RECEIPTS, persist_receipts
from app.telegram_notifications import TelegramSendResult, send_telegram_message
from app.weak_sector_override import _observation_applicable, load_observations

EXPERIMENT = "WEAK_SECTOR_OVERRIDE_V1"
DUE = datetime(2026, 10, 13, 10, 0, tzinfo=ZoneInfo("Asia/Jerusalem"))
KEY = "weak-sector-override-v1-review-2026-10-13-1000-asia-jerusalem"
OBSERVATIONS = Path("agent_results/experiments/weak_sector_override_v1_observations.jsonl")
SNAPSHOT = Path("agent_results/dashboard_snapshot.json")
INTRO = (
    "הגיע מועד בדיקת שדרוג WEAK_SECTOR_OVERRIDE_V1.\n"
    "מצורף סיכום הנתונים לצורך החלטה על המשך העבודה."
)


def committed_text(path: Path) -> str | None:
    reference = f"origin/main:{path.as_posix()}"
    exists = subprocess.run(
        ["git", "cat-file", "-e", reference], cwd=ROOT, capture_output=True, check=False
    )
    if exists.returncode != 0:
        return None
    return subprocess.run(
        ["git", "show", reference], cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", check=True,
    ).stdout


def receipt_seen(receipts: str) -> bool:
    for line in receipts.splitlines():
        if line.strip() and json.loads(line).get("key") == KEY:
            return True
    return False


def summarize(observation_text: str) -> tuple[list[dict], dict]:
    if not observation_text.strip():
        raise ValueError("No persisted observations are available")
    with tempfile.TemporaryDirectory(prefix="market-lens-weak-review-") as directory:
        temporary = Path(directory)
        source = temporary / OBSERVATIONS.name
        source.write_text(observation_text, encoding="utf-8")
        output = temporary / "summaries"
        subprocess.run(
            [sys.executable, str(ROOT / "agent/weak_sector_override_summary.py"),
             "--observations", str(source), "--output-dir", str(output)],
            cwd=ROOT, capture_output=True, text=True, check=True, timeout=480,
        )
        rows = load_observations(source)
        summary = json.loads((output / "weak_sector_override_v1_summary.json").read_text(encoding="utf-8"))
    if summary.get("experiment") != EXPERIMENT or summary.get("observation_count") != len(rows):
        raise ValueError("Measurement summary does not match persisted observations")
    return rows, summary


def _outcomes(label: str, result: dict, *, has_signals: bool) -> str:
    if not has_signals:
        return f"{label}: אין מדגם כשיר להערכת תוצאות."
    if not {"closed", "censored_open", "unassessable"}.issubset(result):
        raise ValueError("Outcome summary is incomplete")
    return (
        f"{label}: סגורות {result['closed']}, פתוחות/מצונזרות "
        f"{result['censored_open']}, ללא נתוני תוצאה מספיקים "
        f"{result['unassessable']}."
    )


def format_review(rows: list[dict], summary: dict, *, now: datetime, latest_run: dict | None) -> str:
    applicable = [row for row in rows if _observation_applicable(row)]
    opportunities = {
        str(row.get("signal_id") or (row.get("session_date"), row.get("ticker"),
                                    row.get("setup_type"), row.get("entry_path")))
        for row in applicable
    }
    dates = sorted({str(row.get("session_date")) for row in applicable if row.get("session_date")})
    timestamps = [str(row.get("timestamp")) for row in rows if row.get("timestamp")]
    latest = max(timestamps, default="unknown")
    latest_run = latest_run or {}
    run_id = str(latest_run.get("run_id") or "לא זמין")
    run_at = str(latest_run.get("timestamp") or "זמן לא זמין")
    period = f"{dates[0]} עד {dates[-1]}" if dates else "אין תצפיות ישימות"
    reasons = Counter(summary.get("weak_ineligibility_reasons") or {})
    reason_text = ", ".join(f"{reason} ({count})" for reason, count in reasons.most_common(5)) or "לא נרשמו"
    signals = int(summary["qualifying_signal_count"])
    controls = int(summary["matched_control_count"])
    quality = summary.get("market_data_errors") or {}
    limits = []
    if signals == 0:
        limits.append("אין אות כשיר ולכן אי אפשר להעריך את ביצועי ההרחבה.")
    if quality:
        limits.append(f"נתוני שוק חסרים (מספר טיקרים: {len(quality)}); אין להסיק מהם תוצאה סגורה.")
    if not summary.get("review_ready"):
        limits.append("ספי המדגם לבחינה עדיין לא הושגו.")
    if not limits:
        limits.append("אפשר לבחון את המדגם; עדיין נדרשת החלטה אנושית.")
    lines = [
        INTRO,
        "",
        f"תקופת איסוף: {period}; ימי מסחר עם תצפיות: {len(dates)}.",
        f"תצפית אחרונה: {latest}; נבדק ב־{now.astimezone(ZoneInfo('Asia/Jerusalem')).strftime('%Y-%m-%d %H:%M')} שעון ישראל.",
        f"סריקה אחרונה: {run_id} ({run_at}); סטטוס: {latest_run.get('run_status') or 'לא זמין'}.",
        f"הזדמנויות ייחודיות ישימות: {len(opportunities)}; אותות כשירים: {signals}; קבוצת ביקורת מותאמת: {controls}.",
        _outcomes("אותות", summary.get("signal_results") or {}, has_signals=bool(signals)),
        _outcomes("ביקורת", summary.get("control_results") or {}, has_signals=bool(controls)),
        f"סיבות פסילה מרכזיות: {reason_text}.",
        "מה ניתן להעריך: היקף הזדמנויות, בדיקות זכאות וסיבות פסילה לפי הנתונים שנשמרו.",
        "מה חסר: " + " ".join(limits),
    ]
    return html.escape("\n".join(lines), quote=False)


def run_review(
    *,
    now: datetime,
    observations: str | None,
    latest_run: dict | None,
    receipts: str,
    send: Callable[[str], TelegramSendResult],
    persist: Callable[[], None],
) -> str:
    if now.tzinfo is None:
        raise ValueError("A timezone-aware clock is required")
    if now < DUE:
        return "not_due"
    if receipt_seen(receipts):
        return "already_sent"
    try:
        if observations is None:
            raise ValueError("Persisted measurement file is missing")
        rows, summary = summarize(observations)
        message = format_review(rows, summary, now=now, latest_run=latest_run)
    except Exception as exc:
        # The reminder itself must still arrive when measurement reporting fails.
        message = html.escape(
            INTRO + "\n\nסיכום המדידה נכשל או שהנתונים אינם זמינים "
            f"({exc.__class__.__name__}). יש לבדוק ידנית את הנתונים השמורים; לא ניתן להסיק תוצאה מהריצה הזו.",
            quote=False,
        )
    result = send(message)
    if not result.sent:
        raise RuntimeError(f"Telegram review delivery failed: {result.status}")
    persist()
    return "sent"


def main() -> None:
    now = datetime.now(ZoneInfo("Asia/Jerusalem"))
    if now < DUE:
        print("Weak-sector review is not due yet.")
        return
    subprocess.run(["git", "fetch", "origin", "main"], cwd=ROOT, check=True, capture_output=True)
    receipts = committed_text(RECEIPTS) or ""
    if receipt_seen(receipts):
        print("Weak-sector review was already delivered.")
        return
    observations = committed_text(OBSERVATIONS)
    snapshot_text = committed_text(SNAPSHOT)
    try:
        latest_run = (json.loads(snapshot_text).get("latest_run") or {}) if snapshot_text else None
    except (ValueError, AttributeError):
        latest_run = None
    local_receipts = ROOT / RECEIPTS
    local_receipts.parent.mkdir(parents=True, exist_ok=True)
    local_receipts.write_text(receipts, encoding="utf-8")
    os.environ["MARKET_LENS_TELEGRAM_DEDUP_LOG"] = str(local_receipts)
    os.environ["MARKET_LENS_TELEGRAM_DEDUP_DAYS"] = "0"
    thread = os.getenv("MARKET_LENS_TELEGRAM_REVIEW_THREAD_ID", "").strip()
    if thread and (not thread.isdecimal() or int(thread) <= 0):
        raise ValueError("MARKET_LENS_TELEGRAM_REVIEW_THREAD_ID must be a positive integer")

    def send(message: str) -> TelegramSendResult:
        return send_telegram_message(
            message, dedupe_key=KEY, message_thread_id=int(thread) if thread else None,
        )

    def persist() -> None:
        if not receipt_seen(local_receipts.read_text(encoding="utf-8")):
            raise RuntimeError("Telegram accepted the review, but no local receipt was written")
        persist_receipts(ROOT)
        subprocess.run(["git", "fetch", "origin", "main"], cwd=ROOT, check=True, capture_output=True)
        if not receipt_seen(committed_text(RECEIPTS) or ""):
            raise RuntimeError("Telegram review receipt was not confirmed on origin/main")

    result = run_review(
        now=now, observations=observations, latest_run=latest_run, receipts=receipts, send=send,
        persist=persist,
    )
    print(f"Weak-sector review status: {result}.")


if __name__ == "__main__":
    main()
