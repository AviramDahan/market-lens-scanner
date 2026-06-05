import hashlib
import json
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.data import fetch_last_price
from app.models import ScanResult

PROJECT_ROOT = Path(__file__).parent.parent


def _db_path() -> Path:
    explicit = os.getenv("MARKET_LENS_DB_PATH")
    if explicit:
        path = Path(explicit)
    else:
        data_dir = Path(os.getenv("MARKET_LENS_DATA_DIR", PROJECT_ROOT / "data"))
        path = data_dir / "setups.sqlite"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_storage() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS saved_setups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fingerprint TEXT NOT NULL UNIQUE,
                ticker TEXT NOT NULL,
                setup_type TEXT NOT NULL,
                analysis_period TEXT NOT NULL,
                source TEXT NOT NULL,
                user_label TEXT,
                session_id TEXT,
                status TEXT NOT NULL,
                score REAL NOT NULL,
                current_price REAL NOT NULL,
                saved_price REAL NOT NULL,
                stop_loss REAL NOT NULL,
                target_1 REAL NOT NULL,
                target_2 REAL NOT NULL,
                risk_reward REAL NOT NULL,
                chart_url TEXT,
                result_json TEXT NOT NULL,
                scan_count INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                status_checked_at TEXT,
                hit_at TEXT
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_saved_setups_created ON saved_setups(created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_saved_setups_status ON saved_setups(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_saved_setups_ticker ON saved_setups(ticker)")


def save_setup(
    result: ScanResult,
    *,
    analysis_period: str,
    chart_url: str | None = None,
    source: str = "auto",
    user_label: str | None = None,
    session_id: str | None = None,
) -> dict[str, Any] | None:
    if result.setup_type == "No Trade":
        return None

    init_storage()
    now = _now()
    result_data = result.model_dump()
    status = evaluate_status(result.current_price, result_data)
    fingerprint = _fingerprint(result_data, analysis_period, source, session_id)
    result_json = json.dumps(result_data, sort_keys=True)
    hit_at = now if status != "OPEN" else None

    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO saved_setups (
                fingerprint, ticker, setup_type, analysis_period, source, user_label, session_id,
                status, score, current_price, saved_price, stop_loss, target_1, target_2,
                risk_reward, chart_url, result_json, created_at, last_seen_at,
                status_checked_at, hit_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(fingerprint) DO UPDATE SET
                last_seen_at=excluded.last_seen_at,
                scan_count=saved_setups.scan_count + 1,
                current_price=excluded.current_price,
                chart_url=COALESCE(excluded.chart_url, saved_setups.chart_url),
                source=CASE
                    WHEN excluded.source = 'manual' THEN 'manual'
                    ELSE saved_setups.source
                END,
                user_label=COALESCE(excluded.user_label, saved_setups.user_label),
                session_id=COALESCE(excluded.session_id, saved_setups.session_id),
                status=excluded.status,
                status_checked_at=excluded.status_checked_at,
                hit_at=COALESCE(saved_setups.hit_at, excluded.hit_at)
            """,
            (
                fingerprint,
                result.ticker,
                result.setup_type,
                analysis_period,
                source,
                _clean_text(user_label),
                _clean_text(session_id),
                status,
                result.score,
                result.current_price,
                result.current_price,
                result.stop_loss,
                result.target_1,
                result.target_2,
                result.risk_reward,
                chart_url,
                result_json,
                now,
                now,
                now,
                hit_at,
            ),
        )
        row = conn.execute(
            "SELECT * FROM saved_setups WHERE fingerprint = ?",
            (fingerprint,),
        ).fetchone()
    return _row_to_dict(row)


def list_setups(
    limit: int = 80,
    status: str | None = None,
    source: str | None = None,
    session_id: str | None = None,
) -> list[dict[str, Any]]:
    init_storage()
    limit = max(1, min(limit, 200))
    where = []
    params: list[Any] = []
    if status:
        where.append("status = ?")
        params.append(status)
    if source:
        where.append("source = ?")
        params.append(source)
    if session_id:
        where.append("session_id = ?")
        params.append(_clean_text(session_id))
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    params.append(limit)

    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM saved_setups
            {where_sql}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    return [_row_to_dict(row) for row in rows]


def refresh_setup(setup_id: int) -> dict[str, Any]:
    init_storage()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM saved_setups WHERE id = ?", (setup_id,)).fetchone()
        if row is None:
            raise KeyError(str(setup_id))

        result_data = json.loads(row["result_json"])
        price = fetch_last_price(row["ticker"])
        status = evaluate_status(price, result_data)
        now = _now()
        hit_at = row["hit_at"] or (now if status != "OPEN" else None)
        conn.execute(
            """
            UPDATE saved_setups
            SET current_price = ?, status = ?, status_checked_at = ?, hit_at = ?
            WHERE id = ?
            """,
            (price, status, now, hit_at, setup_id),
        )
        updated = conn.execute("SELECT * FROM saved_setups WHERE id = ?", (setup_id,)).fetchone()
    return _row_to_dict(updated)


def evaluate_status(price: float, result_data: dict[str, Any]) -> str:
    stop = float(result_data.get("stop_loss") or 0)
    target_1 = float(result_data.get("target_1") or 0)
    target_2 = float(result_data.get("target_2") or 0)

    if stop > 0 and price <= stop:
        return "STOPPED"
    if target_2 > 0 and price >= target_2:
        return "TARGET2"
    if target_1 > 0 and price >= target_1:
        return "TARGET1"
    return "OPEN"


def _fingerprint(
    result_data: dict[str, Any],
    analysis_period: str,
    source: str = "auto",
    session_id: str | None = None,
) -> str:
    payload = {
        "source": source,
        "session_id": _clean_text(session_id) if source == "manual" else "global",
        "ticker": result_data.get("ticker"),
        "setup_type": result_data.get("setup_type"),
        "analysis_period": analysis_period,
        "buy_zone": result_data.get("buy_zone"),
        "stop_loss": round(float(result_data.get("stop_loss") or 0), 2),
        "target_1": round(float(result_data.get("target_1") or 0), 2),
        "target_2": round(float(result_data.get("target_2") or 0), 2),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    data["result"] = json.loads(data.pop("result_json"))
    return data


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _clean_text(value: str | None) -> str | None:
    if not value:
        return None
    return value.strip()[:80] or None
