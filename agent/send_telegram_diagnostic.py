from __future__ import annotations

import os
import re
import subprocess
import tempfile
from pathlib import Path

from app.telegram_notifications import (
    build_telegram_dedupe_key,
    send_telegram_chart_photo,
    send_telegram_message,
)


GIT_OBJECT_RE = re.compile(r"^[0-9a-fA-F]{40}:[A-Za-z0-9_.\-/]+$")


def main() -> None:
    message = os.getenv("MARKET_LENS_TELEGRAM_DIAGNOSTIC_MESSAGE", "").strip()
    if not message:
        raise SystemExit("Diagnostic message is required.")
    if len(message) > 3500:
        raise SystemExit("Diagnostic message is too long.")

    dedupe_key = os.getenv("MARKET_LENS_TELEGRAM_DIAGNOSTIC_DEDUPE_KEY", "").strip()
    outcome = send_telegram_message(message, dedupe_key=dedupe_key)
    print(f"Telegram diagnostic message status: {outcome.status}")
    if not outcome.sent and outcome.status != "duplicate":
        raise SystemExit("Telegram diagnostic message failed.")

    chart_object = os.getenv("MARKET_LENS_TELEGRAM_DIAGNOSTIC_CHART_OBJECT", "").strip()
    if chart_object and outcome.sent:
        send_chart_from_git(
            chart_object,
            ticker=os.getenv("MARKET_LENS_TELEGRAM_DIAGNOSTIC_TICKER", "").strip(),
            dedupe_key=build_telegram_dedupe_key(dedupe_key, "chart"),
        )


def send_chart_from_git(chart_object: str, *, ticker: str, dedupe_key: str) -> None:
    if not GIT_OBJECT_RE.fullmatch(chart_object):
        raise SystemExit("Diagnostic chart object is invalid.")
    revision = chart_object.split(":", 1)[0]
    if subprocess.run(
        ["git", "cat-file", "-e", f"{revision}^{{commit}}"],
        check=False,
        capture_output=True,
    ).returncode != 0:
        fetched = subprocess.run(
            ["git", "fetch", "--no-tags", "--depth=1", "origin", revision],
            check=False,
            capture_output=True,
        )
        if fetched.returncode != 0:
            raise SystemExit("Diagnostic chart revision could not be fetched.")
    completed = subprocess.run(
        ["git", "show", chart_object],
        check=False,
        capture_output=True,
    )
    if completed.returncode != 0 or not completed.stdout:
        raise SystemExit("Diagnostic chart object could not be read.")

    with tempfile.TemporaryDirectory(prefix="market-lens-telegram-") as temporary_dir:
        chart_path = Path(temporary_dir) / "chart.png"
        chart_path.write_bytes(completed.stdout)
        outcome = send_telegram_chart_photo(
            chart_path,
            ticker=ticker,
            dedupe_key=dedupe_key,
        )
    print(f"Telegram diagnostic chart status: {outcome.status}")
    if not outcome.sent and outcome.status != "duplicate":
        raise SystemExit("Telegram diagnostic chart failed.")


if __name__ == "__main__":
    main()
