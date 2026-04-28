"""
Persistent job + watch-list state.

PC may be powered off / restarted at any moment. We persist enough state to
resume in-progress jobs on next start and to expose a watch-list to the cloud
cron watcher.

Tables:
  jobs(id, kind, params_json, status, chat_id, created_at, updated_at, result_json, error)
  watches(id, query_json, owner_chat_id, last_alert_at, active)

DB file: state/state.db (gitignored).
"""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

from .utils.log import get_logger

log = get_logger(__name__)

DB_PATH = Path("state/state.db")


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB_PATH, isolation_level=None)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys=ON")
    return c


def init_db() -> None:
    with _conn() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id           TEXT PRIMARY KEY,
                kind         TEXT NOT NULL,
                params_json  TEXT NOT NULL,
                status       TEXT NOT NULL,
                chat_id      INTEGER,
                created_at   INTEGER NOT NULL,
                updated_at   INTEGER NOT NULL,
                result_json  TEXT,
                error        TEXT
            );

            CREATE TABLE IF NOT EXISTS watches (
                id              TEXT PRIMARY KEY,
                query_json      TEXT NOT NULL,
                owner_chat_id   INTEGER NOT NULL,
                last_alert_at   INTEGER,
                active          INTEGER NOT NULL DEFAULT 1
            );
            """
        )


def save_job(job, chat_id: Optional[int]) -> None:
    now = int(time.time())
    with _conn() as c:
        c.execute(
            """
            INSERT INTO jobs(id, kind, params_json, status, chat_id, created_at, updated_at, result_json, error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                status=excluded.status,
                updated_at=excluded.updated_at,
                result_json=excluded.result_json,
                error=excluded.error
            """,
            (
                job.id,
                job.kind,
                json.dumps(job.params, ensure_ascii=False),
                job.status,
                chat_id,
                int(job.created_at.timestamp()),
                now,
                json.dumps(job.result, ensure_ascii=False, default=str) if job.result else None,
                job.error or None,
            ),
        )


def load_resumable_jobs() -> list[dict[str, Any]]:
    """Return jobs that were running/queued when we last shut down."""
    with _conn() as c:
        cur = c.execute(
            "SELECT id, kind, params_json, chat_id, created_at FROM jobs "
            "WHERE status IN ('queued','running') ORDER BY created_at"
        )
        rows = cur.fetchall()
    out = []
    for row in rows:
        out.append(
            {
                "id": row[0],
                "kind": row[1],
                "params": json.loads(row[2]),
                "chat_id": row[3],
                "created_at": row[4],
            }
        )
    return out


def mark_job_interrupted(job_id: str) -> None:
    """이전 실행에서 비정상 종료된 작업을 'interrupted' 상태로 표시."""
    with _conn() as c:
        c.execute(
            "UPDATE jobs SET status='interrupted', updated_at=? WHERE id=? "
            "AND status IN ('queued','running')",
            (int(time.time()), job_id),
        )


WATCH_LIST_FILE = Path("watch_list.json")


def _sync_watch_list_file() -> None:
    """Write all active watches to watch_list.json so the cloud cron can read it."""
    items = []
    for w in list_watches(active_only=True):
        q = w["query"]
        items.append(
            {
                "id": w["id"],
                "owner_chat_id": w["owner_chat_id"],
                "origin": q["origin"],
                "destination": q["destination"],
                "date": q["date"],
                "time": q["time"],
                "window_minutes": q.get("window_minutes", 120),
            }
        )
    WATCH_LIST_FILE.write_text(json.dumps(items, ensure_ascii=False, indent=2))


def add_watch(watch_id: str, query: dict, owner_chat_id: int) -> None:
    with _conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO watches(id, query_json, owner_chat_id, active) VALUES (?, ?, ?, 1)",
            (watch_id, json.dumps(query, ensure_ascii=False), owner_chat_id),
        )
    _sync_watch_list_file()


def remove_watch(watch_id: str) -> bool:
    with _conn() as c:
        cur = c.execute("UPDATE watches SET active=0 WHERE id=?", (watch_id,))
        ok = cur.rowcount > 0
    _sync_watch_list_file()
    return ok


def list_watches(active_only: bool = True) -> list[dict[str, Any]]:
    sql = "SELECT id, query_json, owner_chat_id, last_alert_at, active FROM watches"
    if active_only:
        sql += " WHERE active=1"
    with _conn() as c:
        cur = c.execute(sql)
        rows = cur.fetchall()
    return [
        {
            "id": r[0],
            "query": json.loads(r[1]),
            "owner_chat_id": r[2],
            "last_alert_at": r[3],
            "active": bool(r[4]),
        }
        for r in rows
    ]


def mark_watch_alerted(watch_id: str) -> None:
    with _conn() as c:
        c.execute("UPDATE watches SET last_alert_at=? WHERE id=?", (int(time.time()), watch_id))
