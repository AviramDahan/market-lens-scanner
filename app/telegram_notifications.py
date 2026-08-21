from __future__ import annotations

import html
import json
import mimetypes
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


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
    allow_legacy_env = _env_bool("MARKET_LENS_TELEGRAM_ALLOW_LEGACY_ENV", False)
    bot_token = os.getenv("MARKET_LENS_TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("MARKET_LENS_TELEGRAM_CHAT_ID", "").strip()
    if allow_legacy_env:
        bot_token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN", "").strip() or os.getenv("TELEGRAM_TOKEN_BOT", "").strip()
        chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID", "").strip()
    return TelegramSettings(
        bot_token=bot_token,
        chat_id=chat_id,
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
    dedupe_key: str = "",
) -> TelegramSendResult:
    current = settings or load_telegram_settings()
    if not current.enabled:
        return TelegramSendResult(False, "disabled", "Telegram notifications are disabled.")
    if not current.bot_token or not current.chat_id:
        return TelegramSendResult(False, "not_configured", "Telegram bot token or chat id is missing.")
    if telegram_dedupe_seen(dedupe_key):
        return TelegramSendResult(False, "duplicate", "Telegram notification already sent for this event.")

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
                mark_telegram_dedupe_sent(dedupe_key)
                return TelegramSendResult(True, "sent", f"Telegram API returned {status_code}.")
            return TelegramSendResult(False, "failed", f"Telegram API returned {status_code}.")
    except Exception as exc:
        return TelegramSendResult(False, "failed", f"Telegram send failed: {exc.__class__.__name__}.")


def send_telegram_photo(
    photo: Any,
    *,
    caption: str = "",
    settings: TelegramSettings | None = None,
    opener: Callable[..., Any] = urlopen,
    dedupe_key: str = "",
) -> TelegramSendResult:
    current = settings or load_telegram_settings()
    if not current.enabled:
        return TelegramSendResult(False, "disabled", "Telegram notifications are disabled.")
    if not current.bot_token or not current.chat_id:
        return TelegramSendResult(False, "not_configured", "Telegram bot token or chat id is missing.")
    if telegram_dedupe_seen(dedupe_key):
        return TelegramSendResult(False, "duplicate", "Telegram notification already sent for this event.")

    photo_source = str(photo or "").strip()
    if not photo_source:
        return TelegramSendResult(False, "no_photo", "No chart image is available.")

    headers = {"User-Agent": "market-lens-agent/1.0"}
    if _is_http_url(photo_source):
        payload = {
            "chat_id": current.chat_id,
            "photo": photo_source,
            "caption": caption,
            "parse_mode": "HTML",
        }
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    else:
        path = Path(photo_source)
        if not path.exists() or not path.is_file():
            return TelegramSendResult(False, "not_found", "Chart image file is missing.")
        data, content_type = _multipart_photo_payload(
            fields={
                "chat_id": current.chat_id,
                "caption": caption,
                "parse_mode": "HTML",
            },
            file_path=path,
        )
        headers["Content-Type"] = content_type

    request = Request(
        f"https://api.telegram.org/bot{current.bot_token}/sendPhoto",
        data=data,
        headers=headers,
        method="POST",
    )
    try:
        with opener(request, timeout=current.timeout_seconds) as response:
            status_code = int(getattr(response, "status", 0) or response.getcode())
            if 200 <= status_code < 300:
                mark_telegram_dedupe_sent(dedupe_key)
                return TelegramSendResult(True, "sent", f"Telegram API returned {status_code}.")
            return TelegramSendResult(False, "failed", f"Telegram API returned {status_code}.")
    except Exception as exc:
        return TelegramSendResult(False, "failed", f"Telegram photo send failed: {exc.__class__.__name__}.")


def send_telegram_chart_photo(
    chart_ref: Any,
    *,
    ticker: Any = "",
    dashboard_url: str = "",
    settings: TelegramSettings | None = None,
    opener: Callable[..., Any] = urlopen,
    dedupe_key: str = "",
) -> TelegramSendResult:
    source = chart_photo_source(chart_ref, dashboard_url)
    if not source:
        return TelegramSendResult(False, "no_photo", "No chart image is available.")
    ticker_text = str(ticker or "").upper()
    caption = f"<b>{_escape(ticker_text)} chart</b>" if ticker_text else "<b>Position chart</b>"
    return send_telegram_photo(source, caption=caption, settings=settings, opener=opener, dedupe_key=dedupe_key)


def chart_photo_source(chart_ref: Any, dashboard_url: str = "") -> str:
    ref = str(chart_ref or "").strip()
    if not ref:
        return ""
    if _is_http_url(ref) or Path(ref).exists():
        return ref
    normalized_ref = ref.replace("\\", "/")
    if "agent_results/" in normalized_ref:
        ref = "/agent-results/" + normalized_ref.split("agent_results/", 1)[1]
    if ref.startswith("/"):
        parts = urlsplit(dashboard_url)
        if parts.scheme and parts.netloc:
            return urlunsplit((parts.scheme, parts.netloc, ref, "", ""))
    return ref


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
    ticker = getattr(result, "ticker", "")
    entry = position.get("entry_price") or getattr(result, "current_price", 0)
    target_1 = position.get("target_1")
    target_2 = position.get("target_2")
    lines = [
        f"<b>BUY | {_escape(ticker)}</b>",
        f"Time: {_escape(_format_message_time(timestamp))}",
        f"Setup: {_escape(getattr(result, 'setup_type', ''))}",
        "",
        f"Entry: {_money(entry)} | Qty: {_escape(position.get('quantity', 0))}",
        f"Exposure: {_money(position.get('exposure_ils'))} | Risk: {_money(position.get('risk_ils'))}",
        f"SL: {_price_with_percent(position.get('stop_loss'), entry)}",
        f"TP1: {_price_with_percent(target_1, entry)}",
        f"TP2: {_price_with_percent(target_2, entry)}",
        "",
        f"Score: {_number(getattr(result, 'score', 0), 2)} | Net R/R: {_number(decision_json.get('net_rr'), 2)}",
        f"Regime: {_escape(decision_json.get('market_regime', '-'))} | Sector: {_escape(decision_json.get('sector_regime', '-'))}",
        f"Why: {_escape(_shorten(getattr(decision, 'feedback', '') or decision_json.get('reason', ''), 220))}",
    ]
    if dashboard_url:
        lines.append(f"Dashboard: {_escape(dashboard_url)}")
    return "\n".join(lines)


def format_position_event_message(
    *,
    position: dict[str, Any],
    event: Any,
    run_id: str,
    timestamp: str,
    dashboard_url: str,
) -> str:
    action = str(getattr(event, "action", "") or "")
    action_title = {
        "TAKE_PARTIAL_PROFIT": "TP1 | PARTIAL SOLD",
        "TAKE_PROFIT": "TP2 | POSITION CLOSED",
        "EXIT_STOP": "STOP | POSITION CLOSED",
    }.get(action, action or "Position event")
    entry = _to_float(position.get("entry_price"))
    trigger = _to_float(getattr(event, "trigger_price", 0))
    quantity = int(_to_float(getattr(event, "quantity", 0)))
    pnl = (trigger - entry) * quantity if entry > 0 and trigger > 0 and quantity > 0 else None
    ticker = getattr(event, "ticker", "") or position.get("ticker", "")
    lines = [
        f"<b>{_escape(action_title)} | {_escape(ticker)}</b>",
        f"Time: {_escape(_format_message_time(timestamp))}",
        f"Price: {_money(trigger)} | Qty: {_escape(quantity)}",
        f"P/L: {_signed_money(pnl)} | Cash: {_money(getattr(event, 'cash_in', None))}",
        "",
        f"Entry: {_money(entry)}",
        f"SL: {_price_with_percent(position.get('stop_loss'), entry)}",
        f"TP1: {_price_with_percent(position.get('target_1'), entry)}",
        f"TP2: {_price_with_percent(position.get('target_2'), entry)}",
        f"Bar H/L/C: {_money(getattr(event, 'high', None))} / {_money(getattr(event, 'low', None))} / {_money(getattr(event, 'close', None))}",
    ]
    if action == "TAKE_PARTIAL_PROFIT":
        lines.append("Next: remaining stop moves to entry.")
    note = _shorten(getattr(event, "note", ""), 180)
    if note:
        lines.append(f"Note: {_escape(note)}")
    if dashboard_url:
        lines.append(f"Dashboard: {_escape(dashboard_url)}")
    return "\n".join(lines)


def format_stop_moved_to_entry_message(
    *,
    position: dict[str, Any],
    event: Any,
    run_id: str,
    timestamp: str,
    dashboard_url: str,
) -> str:
    entry = _to_float(position.get("entry_price"))
    old_stop = _to_float(position.get("stop_loss"))
    total_quantity = int(_to_float(position.get("quantity", 0)))
    closed_quantity = int(_to_float(getattr(event, "quantity", 0)))
    remaining_quantity = max(0, total_quantity - closed_quantity)
    ticker = getattr(event, "ticker", "") or position.get("ticker", "")
    lines = [
        f"<b>STOP TO ENTRY | {_escape(ticker)}</b>",
        f"Time: {_escape(_format_message_time(timestamp))}",
        "",
        f"Old SL: {_price_with_percent(old_stop, entry)}",
        f"New SL: {_price_with_percent(entry, entry)}",
        f"Remaining qty: {_escape(remaining_quantity)}",
        "Reason: TP1 hit; remaining paper position is protected at breakeven.",
    ]
    if dashboard_url:
        lines.append(f"Dashboard: {_escape(dashboard_url)}")
    return "\n".join(lines)


def format_position_attention_message(
    *,
    position: dict[str, Any],
    alert: dict[str, Any],
    timestamp: str,
    dashboard_url: str,
) -> str:
    ticker = alert.get("ticker") or position.get("ticker", "")
    entry = position.get("entry_price_usd") or position.get("entry_price")
    label = alert.get("label") or alert.get("event_type") or "TP/SL level"
    alert_title = _attention_title(alert.get("event_type"), label)
    lines = [
        f"<b>{_escape(alert_title)} | {_escape(ticker)}</b>",
        f"Time: {_escape(_format_message_time(timestamp))}",
        "",
        f"Live: {_money(alert.get('live_price'))}",
        f"Level: {_price_with_percent(alert.get('threshold'), entry)}",
        f"Distance: {_number(alert.get('distance_pct'), 2)}%",
        f"Range H/L: {_money(alert.get('live_high'))} / {_money(alert.get('live_low'))}",
        "",
        "Status: no portfolio change yet.",
        "Tracker updates only after actual TP/SL touch.",
    ]
    if dashboard_url:
        lines.append(f"Dashboard: {_escape(dashboard_url)}")
    return "\n".join(lines)


def dashboard_url_from_env(fallback_app_url: str = "") -> str:
    explicit = os.getenv("MARKET_LENS_DASHBOARD_URL", "").strip()
    if explicit:
        return explicit
    public_url = os.getenv("MARKET_LENS_PUBLIC_URL", "").strip()
    return dashboard_url_from_app_url(public_url or fallback_app_url)


def dashboard_url_from_app_url(app_url: str) -> str:
    parts = urlsplit(app_url)
    if not parts.scheme or not parts.netloc:
        return ""
    return urlunsplit((parts.scheme, parts.netloc, "/agent", "", ""))


def build_telegram_dedupe_key(*parts: Any) -> str:
    clean_parts = []
    for part in parts:
        text = str(part if part is not None else "").strip()
        if text:
            clean_parts.append(text.replace("\n", " ")[:120])
    return "|".join(clean_parts)


def _attention_title(event_type: Any, label: Any) -> str:
    event = str(event_type or "").upper()
    if event == "EXIT_STOP":
        return "NEAR STOP"
    if event == "TAKE_PROFIT":
        return "NEAR TP2"
    if event == "TAKE_PARTIAL_PROFIT":
        return "NEAR TP1"
    clean_label = str(label or "").strip().upper()
    return f"NEAR {clean_label}" if clean_label else "NEAR TP/SL"


def telegram_dedupe_seen(dedupe_key: str) -> bool:
    key = str(dedupe_key or "").strip()
    if not key or not _env_bool("MARKET_LENS_TELEGRAM_DEDUP_ENABLED", True):
        return False
    log_path = telegram_dedupe_log_path()
    if not log_path.exists():
        return False
    cutoff = telegram_dedupe_cutoff()
    try:
        for line in log_path.read_text(encoding="utf-8").splitlines():
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if payload.get("key") != key:
                continue
            sent_at = _parse_utc(payload.get("sent_at"))
            if cutoff is None or sent_at is None or sent_at >= cutoff:
                return True
    except OSError:
        return False
    return False


def mark_telegram_dedupe_sent(dedupe_key: str) -> None:
    key = str(dedupe_key or "").strip()
    if not key or not _env_bool("MARKET_LENS_TELEGRAM_DEDUP_ENABLED", True):
        return
    log_path = telegram_dedupe_log_path()
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "sent_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "key": key,
        }
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, separators=(",", ":"), ensure_ascii=False) + "\n")
    except OSError:
        return


def telegram_dedupe_log_path() -> Path:
    raw_path = os.getenv("MARKET_LENS_TELEGRAM_DEDUP_LOG", "agent_results/telegram_notifications.jsonl").strip()
    return Path(raw_path or "agent_results/telegram_notifications.jsonl")


def telegram_dedupe_cutoff() -> datetime | None:
    try:
        days = int(os.getenv("MARKET_LENS_TELEGRAM_DEDUP_DAYS", "14"))
    except ValueError:
        days = 14
    if days <= 0:
        return None
    return datetime.now(timezone.utc) - timedelta(days=days)


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


def _price_with_percent(value: Any, entry: Any) -> str:
    price = _money(value)
    percent = _percent_from_entry(value, entry)
    if percent == "-":
        return price
    return f"{price} ({percent})"


def _signed_money(value: Any) -> str:
    amount = _to_float(value)
    if value is None:
        return "-"
    sign = "+" if amount >= 0 else "-"
    return f"{sign}${abs(amount):,.2f}"


def _number(value: Any, digits: int = 2) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return "-"


def _percent_from_entry(value: Any, entry: Any) -> str:
    target = _to_float(value)
    base = _to_float(entry)
    if target <= 0 or base <= 0:
        return "-"
    change = ((target - base) / base) * 100
    sign = "+" if change > 0 else ""
    return f"{sign}{change:.2f}%"


def _format_message_time(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(_message_timezone()).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        compact = text.replace("T", " ")
        if len(compact) >= 16:
            return compact[:16]
        return compact


def _parse_utc(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _message_timezone() -> ZoneInfo:
    timezone_name = os.getenv("MARKET_LENS_TELEGRAM_TIMEZONE", "Asia/Jerusalem").strip() or "Asia/Jerusalem"
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("Asia/Jerusalem")


def _is_http_url(value: str) -> bool:
    parts = urlsplit(value)
    return parts.scheme in {"http", "https"} and bool(parts.netloc)


def _multipart_photo_payload(*, fields: dict[str, Any], file_path: Path) -> tuple[bytes, str]:
    boundary = f"market-lens-{uuid4().hex}"
    content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    body = bytearray()

    for name, value in fields.items():
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"))
        body.extend(str(value).encode("utf-8"))
        body.extend(b"\r\n")

    body.extend(f"--{boundary}\r\n".encode("utf-8"))
    body.extend(
        (
            f'Content-Disposition: form-data; name="photo"; filename="{file_path.name}"\r\n'
            f"Content-Type: {content_type}\r\n\r\n"
        ).encode("utf-8")
    )
    body.extend(file_path.read_bytes())
    body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode("utf-8"))
    return bytes(body), f"multipart/form-data; boundary={boundary}"


def _shorten(value: Any, limit: int = 700) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value if value is not None else default)
    except (TypeError, ValueError):
        return default
