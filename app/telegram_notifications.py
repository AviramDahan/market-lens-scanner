from __future__ import annotations

import html
import json
import os
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class TelegramSettings:
    bot_token: str
    chat_id: str
    enabled: bool = True
    timeout_seconds: int = 10


@dataclass(frozen=True)
class TelegramSendResult:
    sent: bool
    status: str
    reason: str = ""


def load_telegram_settings() -> TelegramSettings:
    return TelegramSettings(
        bot_token=(
            os.getenv("MARKET_LENS_TELEGRAM_BOT_TOKEN")
            or os.getenv("TELEGRAM_BOT_TOKEN")
            or os.getenv("TELEGRAM_TOKEN_BOT")
            or ""
        ).strip(),
        chat_id=(os.getenv("MARKET_LENS_TELEGRAM_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID") or "").strip(),
        enabled=_env_bool("MARKET_LENS_TELEGRAM_ENABLED", True),
        timeout_seconds=max(1, int(os.getenv("MARKET_LENS_TELEGRAM_TIMEOUT_SECONDS", "10"))),
    )


def telegram_configured(settings: TelegramSettings | None = None) -> bool:
    current = settings or load_telegram_settings()
    return current.enabled and bool(current.bot_token and current.chat_id)


def send_telegram_message(
    text: str,
    *,
    settings: TelegramSettings | None = None,
    opener: Callable[..., Any] = urlopen,
) -> TelegramSendResult:
    current = settings or load_telegram_settings()
    if not current.enabled:
        return TelegramSendResult(False, "disabled", "Telegram notifications are disabled.")
    if not current.bot_token or not current.chat_id:
        return TelegramSendResult(False, "not_configured", "Telegram bot token or chat id is missing.")

    payload = {
        "chat_id": current.chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    request = Request(
        f"https://api.telegram.org/bot{current.bot_token}/sendMessage",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "market-lens-agent/1.0"},
        method="POST",
    )
    try:
        with opener(request, timeout=current.timeout_seconds) as response:
            status_code = int(getattr(response, "status", 0) or response.getcode())
            if 200 <= status_code < 300:
                return TelegramSendResult(True, "sent", f"Telegram API returned {status_code}.")
            return TelegramSendResult(False, "failed", f"Telegram API returned {status_code}.")
    except Exception as exc:
        return TelegramSendResult(False, "failed", f"Telegram send failed: {exc.__class__.__name__}.")


def format_position_opened_message(
    *,
    result: Any,
    decision: Any,
    position: dict[str, Any],
    run_id: str,
    timestamp: str,
    dashboard_url: str,
) -> str:
    decision_json = getattr(decision, "decision_json", {}) or {}
    lines = [
        "<b>Market Lens Paper Agent</b>",
        "<b>BUY_SIMULATED opened</b>",
        "",
        f"Ticker: <b>{_escape(getattr(result, 'ticker', ''))}</b>",
        f"Setup: {_escape(getattr(result, 'setup_type', ''))}",
        f"Time: {_escape(timestamp)}",
        f"Run: {_escape(run_id)}",
        "",
        f"Entry: {_money(position.get('entry_price') or getattr(result, 'current_price', 0))}",
        f"Quantity: {_escape(position.get('quantity', 0))}",
        f"Exposure: {_money(position.get('exposure_ils'))}",
        f"Risk: {_money(position.get('risk_ils'))}",
        f"Stop: {_money(position.get('stop_loss'))}",
        f"Targets: {_money(position.get('target_1'))} / {_money(position.get('target_2'))}",
        "",
        f"Setup score: {_number(getattr(result, 'score', 0), 2)}",
        f"Net R/R: {_number(decision_json.get('net_rr'), 2)}",
        f"Market: {_escape(decision_json.get('market_regime', '-'))}",
        f"Sector: {_escape(decision_json.get('sector', '-'))} ({_escape(decision_json.get('sector_regime', '-'))})",
        "",
        f"Reason: {_escape(_shorten(getattr(decision, 'feedback', '') or decision_json.get('reason', '')))}",
    ]
    if dashboard_url:
        lines.extend(["", f"Dashboard: {_escape(dashboard_url)}"])
    return "\n".join(lines)


def dashboard_url_from_app_url(app_url: str) -> str:
    parts = urlsplit(app_url)
    if not parts.scheme or not parts.netloc:
        return ""
    return urlunsplit((parts.scheme, parts.netloc, "/agent", "", ""))


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _escape(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=False)


def _money(value: Any) -> str:
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return "-"


def _number(value: Any, digits: int = 2) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return "-"


def _shorten(value: Any, limit: int = 700) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."
