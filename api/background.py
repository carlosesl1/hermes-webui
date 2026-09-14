"""Durable /background tasks; /btw remains an ephemeral side conversation.

The WebUI server owns the worker threads. Opening this store in a new server
lifetime marks unfinished work interrupted; it does not resume those threads.
Results are separate from the parent's transcript and are never consumed by GET.
"""
from __future__ import annotations

from contextlib import contextmanager
import sqlite3
import threading
import time
from typing import Any

from api.config import STATE_DIR

_lock = threading.Lock()
_ready_path = None
_TERMINAL = {"done", "error", "no_response", "cancelled", "interrupted"}
_BTW_TRACKING: dict[str, dict[str, Any]] = {}


@contextmanager
def _store():
    """Serialize initialization/writes in the single WebUI server process."""
    global _ready_path
    path = STATE_DIR / "background_tasks.sqlite3"
    with _lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                if _ready_path != path:
                    db.execute("""CREATE TABLE IF NOT EXISTS background_tasks (
                        parent_session_id TEXT NOT NULL,
                        task_id TEXT NOT NULL,
                        bg_session_id TEXT NOT NULL,
                        stream_id TEXT NOT NULL,
                        prompt TEXT NOT NULL,
                        status TEXT NOT NULL,
                        started_at REAL NOT NULL,
                        answer TEXT,
                        completed_at REAL,
                        error TEXT,
                        PRIMARY KEY (parent_session_id, task_id)
                    )""")
                    db.execute("""UPDATE background_tasks
                        SET status='interrupted', completed_at=?, error=?
                        WHERE status='running'""", (
                        time.time(),
                        "Server restarted before the result was saved. Task was not resumed.",
                    ))
            _ready_path = path
            with db:
                yield db
        finally:
            db.close()


def track_background(parent_sid: str, bg_sid: str, stream_id: str,
                     task_id: str, prompt: str) -> None:
    with _store() as db:
        db.execute("""INSERT INTO background_tasks
            (parent_session_id, task_id, bg_session_id, stream_id, prompt, status, started_at)
            VALUES (?, ?, ?, ?, ?, 'running', ?)""",
            (parent_sid, task_id, bg_sid, stream_id, prompt, time.time()))


def complete_background(parent_sid: str, task_id: str, answer: str,
                        *, status: str = "done", error: str | None = None) -> bool:
    """Commit a terminal result before child cleanup; first completion wins.

    Persistence errors propagate so callers retain the child's session file.
    False means this exact result is not stored and cleanup is not safe.
    """
    if status not in _TERMINAL:
        raise ValueError("Invalid background terminal status")
    with _store() as db:
        db.execute("""UPDATE background_tasks
            SET status=?, answer=?, completed_at=?, error=?
            WHERE parent_session_id=? AND task_id=? AND status='running'""",
            (status, answer, time.time(), error, parent_sid, task_id))
        return db.execute("""SELECT 1 FROM background_tasks
            WHERE parent_session_id=? AND task_id=? AND status=?
                AND answer IS ? AND error IS ?""",
            (parent_sid, task_id, status, answer, error)).fetchone() is not None


def get_results(parent_sid: str) -> list[dict[str, Any]]:
    """Idempotent terminal snapshot, safe for retries, siblings and multiple tabs."""
    return [task for task in get_background_tasks(parent_sid) if task["status"] != "running"]


def get_background_tasks(parent_sid: str) -> list[dict[str, Any]]:
    """Detached, ordered snapshot including running and completed tasks."""
    with _store() as db:
        return [dict(row) for row in db.execute("""SELECT * FROM background_tasks
            WHERE parent_session_id=? ORDER BY started_at, task_id""", (parent_sid,))]


def track_btw(parent_sid: str, ephemeral_sid: str, stream_id: str,
              question: str) -> None:
    with _lock:
        _BTW_TRACKING[parent_sid] = {
            "ephemeral_session_id": ephemeral_sid,
            "stream_id": stream_id,
            "question": question,
        }


def cleanup_btw(parent_sid: str) -> dict[str, Any] | None:
    """Remove and return btw tracking for a parent session."""
    with _lock:
        return _BTW_TRACKING.pop(parent_sid, None)
