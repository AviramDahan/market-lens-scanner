import hashlib
import json
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.data import fetch_last_price
from app.models import ScanResult

PROJECT_ROOT = Path(__file__).parent.parent


def _database_url() -> str | None:
    return os.getenv("DATABASE_URL") or os.getenv("SUPABASE_DATABASE_URL")


def using_external_storage() -> bool:
    return bool(_database_url())


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
    if _database_url():
        return

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
    user_id: str | None = None,
) -> dict[str, Any] | None:
    if result.setup_type == "No Trade":
        return None

    if _database_url():
        if source == "manual":
            if not user_id:
                raise ValueError("user_id is required for manual setup saves")
            return _pg_save_user_setup(
                result,
                analysis_period=analysis_period,
                chart_url=chart_url,
                user_id=user_id,
            )
        return _pg_save_global_setup(
            result,
            analysis_period=analysis_period,
            chart_url=chart_url,
            found_by=user_id,
            found_by_name=user_label,
        )

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
    user_id: str | None = None,
) -> list[dict[str, Any]]:
    if _database_url():
        if source == "manual":
            if not user_id:
                return []
            return _pg_list_user_setups(limit=limit, status=status, user_id=user_id)
        return _pg_list_global_setups(limit=limit, status=status)

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


def refresh_setup(setup_id: int | str, user_id: str | None = None) -> dict[str, Any]:
    if _database_url():
        return _pg_refresh_setup(str(setup_id), user_id=user_id)

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


def _pg_connect() -> psycopg.Connection:
    database_url = _database_url()
    if not database_url:
        raise RuntimeError("DATABASE_URL is not configured")
    return psycopg.connect(database_url, row_factory=dict_row)


def _pg_save_global_setup(
    result: ScanResult,
    *,
    analysis_period: str,
    chart_url: str | None,
    found_by: str | None,
    found_by_name: str | None,
) -> dict[str, Any]:
    result_data = result.model_dump()
    status = evaluate_status(result.current_price, result_data)
    fingerprint = _fingerprint(result_data, analysis_period, "auto", None)
    hit_at = _now() if status != "OPEN" else None

    with _pg_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO public.global_setups (
                    fingerprint, ticker, setup_type, analysis_period, status, score,
                    current_price, found_price, stop_loss, target_1, target_2,
                    risk_reward, chart_url, result_json, found_by, found_by_name,
                    status_checked_at, hit_at
                )
                VALUES (
                    %(fingerprint)s, %(ticker)s, %(setup_type)s, %(analysis_period)s,
                    %(status)s, %(score)s, %(current_price)s, %(found_price)s,
                    %(stop_loss)s, %(target_1)s, %(target_2)s, %(risk_reward)s,
                    %(chart_url)s, %(result_json)s, %(found_by)s, %(found_by_name)s,
                    now(), %(hit_at)s
                )
                ON CONFLICT (fingerprint) DO UPDATE SET
                    last_seen_at = now(),
                    scan_count = public.global_setups.scan_count + 1,
                    current_price = excluded.current_price,
                    chart_url = coalesce(excluded.chart_url, public.global_setups.chart_url),
                    status = excluded.status,
                    status_checked_at = now(),
                    hit_at = coalesce(public.global_setups.hit_at, excluded.hit_at)
                RETURNING *
                """,
                {
                    "fingerprint": fingerprint,
                    "ticker": result.ticker,
                    "setup_type": result.setup_type,
                    "analysis_period": analysis_period,
                    "status": status,
                    "score": result.score,
                    "current_price": result.current_price,
                    "found_price": result.current_price,
                    "stop_loss": result.stop_loss,
                    "target_1": result.target_1,
                    "target_2": result.target_2,
                    "risk_reward": result.risk_reward,
                    "chart_url": chart_url,
                    "result_json": Jsonb(result_data),
                    "found_by": found_by,
                    "found_by_name": _clean_text(found_by_name),
                    "hit_at": hit_at,
                },
            )
            row = cur.fetchone()
    return _pg_global_row_to_dict(row)


def _pg_save_user_setup(
    result: ScanResult,
    *,
    analysis_period: str,
    chart_url: str | None,
    user_id: str,
) -> dict[str, Any]:
    global_setup = _pg_save_global_setup(
        result,
        analysis_period=analysis_period,
        chart_url=chart_url,
        found_by=user_id,
        found_by_name=None,
    )
    result_data = result.model_dump()
    status = evaluate_status(result.current_price, result_data)
    hit_at = _now() if status != "OPEN" else None

    with _pg_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO public.user_saved_setups (
                    user_id, global_setup_id, ticker, setup_type, analysis_period,
                    status, score, saved_price, current_price, stop_loss, target_1,
                    target_2, risk_reward, chart_url, result_json, status_checked_at, hit_at
                )
                VALUES (
                    %(user_id)s, %(global_setup_id)s, %(ticker)s, %(setup_type)s,
                    %(analysis_period)s, %(status)s, %(score)s, %(saved_price)s,
                    %(current_price)s, %(stop_loss)s, %(target_1)s, %(target_2)s,
                    %(risk_reward)s, %(chart_url)s, %(result_json)s, now(), %(hit_at)s
                )
                ON CONFLICT (user_id, global_setup_id) DO UPDATE SET
                    current_price = excluded.current_price,
                    status = excluded.status,
                    chart_url = coalesce(excluded.chart_url, public.user_saved_setups.chart_url),
                    status_checked_at = now(),
                    hit_at = coalesce(public.user_saved_setups.hit_at, excluded.hit_at)
                RETURNING *
                """,
                {
                    "user_id": user_id,
                    "global_setup_id": global_setup["id"],
                    "ticker": result.ticker,
                    "setup_type": result.setup_type,
                    "analysis_period": analysis_period,
                    "status": status,
                    "score": result.score,
                    "saved_price": result.current_price,
                    "current_price": result.current_price,
                    "stop_loss": result.stop_loss,
                    "target_1": result.target_1,
                    "target_2": result.target_2,
                    "risk_reward": result.risk_reward,
                    "chart_url": chart_url,
                    "result_json": Jsonb(result_data),
                    "hit_at": hit_at,
                },
            )
            row = cur.fetchone()
    return _pg_user_row_to_dict(row)


def _pg_list_global_setups(limit: int, status: str | None = None) -> list[dict[str, Any]]:
    limit = max(1, min(limit, 200))
    where = "WHERE status = %(status)s" if status else ""
    params = {"limit": limit, "status": status}
    with _pg_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT *
                FROM public.global_setups
                {where}
                ORDER BY created_at DESC
                LIMIT %(limit)s
                """,
                params,
            )
            rows = cur.fetchall()
    return [_pg_global_row_to_dict(row) for row in rows]


def _pg_list_user_setups(limit: int, user_id: str, status: str | None = None) -> list[dict[str, Any]]:
    limit = max(1, min(limit, 200))
    where_status = "AND status = %(status)s" if status else ""
    with _pg_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT *
                FROM public.user_saved_setups
                WHERE user_id = %(user_id)s
                {where_status}
                ORDER BY created_at DESC
                LIMIT %(limit)s
                """,
                {"user_id": user_id, "status": status, "limit": limit},
            )
            rows = cur.fetchall()
    return [_pg_user_row_to_dict(row) for row in rows]


def _pg_refresh_setup(setup_id: str, user_id: str | None = None) -> dict[str, Any]:
    with _pg_connect() as conn:
        with conn.cursor() as cur:
            if user_id:
                cur.execute(
                    "SELECT * FROM public.user_saved_setups WHERE id = %(id)s AND user_id = %(user_id)s",
                    {"id": setup_id, "user_id": user_id},
                )
                row = cur.fetchone()
                if row is not None:
                    price = fetch_last_price(row["ticker"])
                    status = evaluate_status(price, row["result_json"])
                    hit_at = row["hit_at"] or (_now() if status != "OPEN" else None)
                    cur.execute(
                        """
                        UPDATE public.user_saved_setups
                        SET current_price = %(price)s, status = %(status)s,
                            status_checked_at = now(), hit_at = %(hit_at)s
                        WHERE id = %(id)s AND user_id = %(user_id)s
                        RETURNING *
                        """,
                        {"price": price, "status": status, "hit_at": hit_at, "id": setup_id, "user_id": user_id},
                    )
                    return _pg_user_row_to_dict(cur.fetchone())

            cur.execute("SELECT * FROM public.global_setups WHERE id = %(id)s", {"id": setup_id})
            row = cur.fetchone()
            if row is None:
                raise KeyError(setup_id)
            price = fetch_last_price(row["ticker"])
            status = evaluate_status(price, row["result_json"])
            hit_at = row["hit_at"] or (_now() if status != "OPEN" else None)
            cur.execute(
                """
                UPDATE public.global_setups
                SET current_price = %(price)s, status = %(status)s,
                    status_checked_at = now(), hit_at = %(hit_at)s
                WHERE id = %(id)s
                RETURNING *
                """,
                {"price": price, "status": status, "hit_at": hit_at, "id": setup_id},
            )
            return _pg_global_row_to_dict(cur.fetchone())


def _pg_global_row_to_dict(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "ticker": row["ticker"],
        "setup_type": row["setup_type"],
        "analysis_period": row["analysis_period"],
        "source": "auto",
        "user_label": row.get("found_by_name"),
        "session_id": None,
        "status": row["status"],
        "score": row["score"],
        "current_price": row["current_price"],
        "saved_price": row["found_price"],
        "stop_loss": row["stop_loss"],
        "target_1": row["target_1"],
        "target_2": row["target_2"],
        "risk_reward": row["risk_reward"],
        "chart_url": row["chart_url"],
        "result": row["result_json"],
        "scan_count": row["scan_count"],
        "created_at": _iso(row["created_at"]),
        "last_seen_at": _iso(row["last_seen_at"]),
        "status_checked_at": _iso(row["status_checked_at"]),
        "hit_at": _iso(row["hit_at"]),
    }


def _pg_user_row_to_dict(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "ticker": row["ticker"],
        "setup_type": row["setup_type"],
        "analysis_period": row["analysis_period"],
        "source": "manual",
        "user_label": None,
        "session_id": None,
        "status": row["status"],
        "score": row["score"],
        "current_price": row["current_price"],
        "saved_price": row["saved_price"],
        "stop_loss": row["stop_loss"],
        "target_1": row["target_1"],
        "target_2": row["target_2"],
        "risk_reward": row["risk_reward"],
        "chart_url": row["chart_url"],
        "result": row["result_json"],
        "scan_count": 1,
        "created_at": _iso(row["created_at"]),
        "last_seen_at": _iso(row["created_at"]),
        "status_checked_at": _iso(row["status_checked_at"]),
        "hit_at": _iso(row["hit_at"]),
    }


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


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _clean_text(value: str | None) -> str | None:
    if not value:
        return None
    return value.strip()[:80] or None
