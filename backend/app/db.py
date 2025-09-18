from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .config import get_settings


logger = logging.getLogger(__name__)

SETTINGS = get_settings()
DB_PATH = SETTINGS.operations_db_path


@contextmanager
def get_conn() -> Iterable[sqlite3.Connection]:
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.row_factory = sqlite3.Row
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with get_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS operations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                actor TEXT,
                action TEXT NOT NULL,
                node TEXT,
                before_metrics TEXT,
                after_metrics TEXT,
                status TEXT NOT NULL,
                details TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                uuid TEXT NOT NULL,
                filename TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                node TEXT NOT NULL,
                path TEXT NOT NULL,
                created_at TEXT NOT NULL,
                history TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT UNIQUE NOT NULL,
                node TEXT,
                status TEXT NOT NULL,
                payload TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                details TEXT
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_tasks_updated_at ON tasks(updated_at)"
        )


def record_operation(
    *,
    action: str,
    status: str,
    node: Optional[str] = None,
    actor: Optional[str] = "system",
    before_metrics: Optional[Dict[str, Any]] = None,
    after_metrics: Optional[Dict[str, Any]] = None,
    details: Optional[str] = None,
) -> None:
    entry = (
        datetime.utcnow().isoformat(),
        actor,
        action,
        node,
        json.dumps(before_metrics) if before_metrics else None,
        json.dumps(after_metrics) if after_metrics else None,
        status,
        details,
    )
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO operations (timestamp, actor, action, node, before_metrics, after_metrics, status, details)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            entry,
        )
    try:
        from .logging_service import index_operation_log_sync

        doc = {
            "@timestamp": entry[0],
            "actor": actor,
            "action": action,
            "node": node,
            "status": status,
            "details": details,
            "message": details or action,
            "before_metrics": before_metrics,
            "after_metrics": after_metrics,
            "service": {"name": "operations"},
        }
        index_operation_log_sync(doc)
    except Exception as exc:
        logger.debug("Failed to forward operation to Elasticsearch: %s", exc)


def list_operations(limit: int = 100) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM operations ORDER BY datetime(timestamp) DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def create_file_record(
    *,
    uuid: str,
    filename: str,
    size_bytes: int,
    node: str,
    path: str,
    history: Optional[List[Dict[str, Any]]] = None,
) -> int:
    history = history or []
    record = (
        uuid,
        filename,
        size_bytes,
        node,
        path,
        datetime.utcnow().isoformat(),
        json.dumps(history),
    )
    with get_conn() as conn:
        cursor = conn.execute(
            """
            INSERT INTO files (uuid, filename, size_bytes, node, path, created_at, history)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            record,
        )
        return int(cursor.lastrowid)


def update_file_record(file_id: int, *, node: Optional[str] = None, path: Optional[str] = None, history: Optional[List[Dict[str, Any]]] = None) -> None:
    setters = []
    values: List[Any] = []
    if node is not None:
        setters.append("node = ?")
        values.append(node)
    if path is not None:
        setters.append("path = ?")
        values.append(path)
    if history is not None:
        setters.append("history = ?")
        values.append(json.dumps(history))
    if not setters:
        return
    values.append(file_id)
    query = f"UPDATE files SET {', '.join(setters)} WHERE id = ?"
    with get_conn() as conn:
        conn.execute(query, values)


def get_files() -> List[Dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM files ORDER BY datetime(created_at) DESC"
        ).fetchall()
    return [dict(row) for row in rows]


def get_file(file_id: int) -> Optional[Dict[str, Any]]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM files WHERE id = ?",
            (file_id,),
        ).fetchone()
    return dict(row) if row else None


def delete_file_record(file_id: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM files WHERE id = ?", (file_id,))


def get_files_by_node(node: str) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM files WHERE node = ?",
            (node,),
        ).fetchall()
    return [dict(row) for row in rows]


def upsert_task_record(*, task_id: str, node: Optional[str], status: str, payload: Optional[Dict[str, Any]] = None, details: Optional[str] = None) -> None:
    now = datetime.utcnow().isoformat()
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO tasks (task_id, node, status, payload, created_at, updated_at, details)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(task_id) DO UPDATE SET
                node=excluded.node,
                status=excluded.status,
                payload=excluded.payload,
                updated_at=excluded.updated_at,
                details=excluded.details
            """,
            (
                task_id,
                node,
                status,
                json.dumps(payload) if payload else None,
                now,
                now,
                details,
            ),
        )


def delete_task(task_id: str) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM tasks WHERE task_id = ?", (task_id,))


def list_tasks(limit: int = 100) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM tasks ORDER BY datetime(updated_at) DESC LIMIT ?",
            (limit,),
        ).fetchall()
    tasks = []
    for row in rows:
        record = dict(row)
        payload = record.get("payload")
        if isinstance(payload, str):
            try:
                record["payload"] = json.loads(payload)
            except json.JSONDecodeError:
                record["payload"] = None
        tasks.append(record)
    return tasks
