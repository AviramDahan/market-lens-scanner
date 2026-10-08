"""Best-effort, rate-limited Telegram alerts for the live host runner."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from urllib.request import Request, urlopen

try:
    import fcntl
except ImportError:  # Tests run on Windows; the production host is Linux.
    fcntl = None


REPO = Path("/home/trader/market-lens-runtime")
STATE = Path("/home/trader/market-lens-runtime-state")
RUNTIME_ENV = Path("/home/trader/.config/market-lens/runtime.env")
HOST_ENV = Path("/home/trader/.config/market-lens/host.env")
DEFAULT_ENV = REPO / "deploy/hetzner/runtime.defaults.env"
ALLOWED_COMPONENTS = {"scanner", "monitor", "maintenance"}
ALLOWED_EVENTS = {"RUN_FAILED", "MONITOR_DEGRADED", "TRADE_ALERT_DELIVERY_FAILED", "DISK_LOW",
                  "MAINTENANCE_FAILED", "MAINTENANCE_INEFFECTIVE"}


def env_value(name: str) -> str:
    value = os.getenv(name, "").strip()
    if value:
        return value
    for path in (HOST_ENV, RUNTIME_ENV, DEFAULT_ENV):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            key, separator, raw = line.strip().partition("=")
            if separator and key == name:
                configured = raw.strip().strip("\"'")
                if configured:
                    return configured
    return ""


def send_runtime_alert(
    component: str,
    event: str,
    *,
    now: datetime | None = None,
    state: Path | None = None,
    opener=urlopen,
) -> str:
    if component not in ALLOWED_COMPONENTS or event not in ALLOWED_EVENTS:
        raise ValueError("Unsupported operations alert")
    token = env_value("MARKET_LENS_TELEGRAM_BOT_TOKEN")
    chat_id = env_value("MARKET_LENS_TELEGRAM_OPS_CHAT_ID")
    if not token or not chat_id:
        return "not_configured"

    moment = now or datetime.now(timezone.utc)
    receipt = state or STATE / "ops_alerts" / f"{component}_{event}.json"
    receipt.parent.mkdir(parents=True, exist_ok=True)
    cooldown = max(60, int(env_value("MARKET_LENS_OPS_ALERT_COOLDOWN_SECONDS") or "3600"))
    with receipt.open("a+", encoding="utf-8") as record:
        if fcntl is not None:
            fcntl.flock(record, fcntl.LOCK_EX)
        record.seek(0)
        try:
            previous = json.load(record)
            sent_at = datetime.fromisoformat(previous["sent_at"])
            if 0 <= (moment - sent_at).total_seconds() < cooldown:
                return "duplicate"
        except (ValueError, KeyError, TypeError):
            pass

        detail = (
            "Build-cache maintenance needs review. Check the host service journal and free disk space."
            if component == "maintenance" else
            "Paper portfolio execution needs review. Check the host service journal "
            "and the latest Agent dashboard. No trade details or credentials are included."
        )
        message = (
            f"<b>Market Lens | {escape(component.upper())} {escape(event)}</b>\n"
            f"Time: {moment.astimezone(timezone.utc):%Y-%m-%d %H:%M} UTC\n"
            f"{detail}"
        )
        request = Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=json.dumps({"chat_id": chat_id, "text": message, "parse_mode": "HTML"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with opener(request, timeout=10) as response:
                if not 200 <= response.status < 300:
                    return "failed"
        except Exception:
            return "failed"
        record.seek(0)
        record.truncate()
        json.dump({"sent_at": moment.isoformat()}, record)
        record.flush()
        os.fsync(record.fileno())
        return "sent"
