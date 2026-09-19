from __future__ import annotations

import json
import logging
import sqlite3
import threading
from pathlib import Path

LOGGER = logging.getLogger(__name__)


class SQLiteStore:
    """Small local persistence layer for single-process Web deployments."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        with self._lock, self._connection:
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.execute("PRAGMA synchronous=NORMAL")
            self._connection.execute("PRAGMA busy_timeout=5000")
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    accessed_at REAL NOT NULL,
                    data_json TEXT NOT NULL,
                    history_json TEXT NOT NULL
                )
                """
            )
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    updated_at REAL NOT NULL,
                    data_json TEXT NOT NULL
                )
                """
            )

    def load_sessions(self) -> list[tuple[str, float, str, str]]:
        with self._lock:
            return self._connection.execute(
                "SELECT session_id, accessed_at, data_json, history_json FROM sessions"
            ).fetchall()

    def save_session(self, session_id: str, accessed_at: float, data: dict, history: list[dict[str, str]]) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO sessions(session_id, accessed_at, data_json, history_json)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    accessed_at=excluded.accessed_at,
                    data_json=excluded.data_json,
                    history_json=excluded.history_json
                """,
                (session_id, accessed_at, json.dumps(data, ensure_ascii=False), json.dumps(history, ensure_ascii=False)),
            )

    def delete_sessions(self, session_ids: list[str]) -> None:
        if not session_ids:
            return
        with self._lock, self._connection:
            self._connection.executemany("DELETE FROM sessions WHERE session_id = ?", ((item,) for item in session_ids))

    def load_jobs(self) -> list[tuple[str, float, str]]:
        with self._lock:
            return self._connection.execute("SELECT job_id, updated_at, data_json FROM jobs").fetchall()

    def save_job(self, job_id: str, updated_at: float, data: dict) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO jobs(job_id, updated_at, data_json)
                VALUES (?, ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET
                    updated_at=excluded.updated_at,
                    data_json=excluded.data_json
                """,
                (job_id, updated_at, json.dumps(data, ensure_ascii=False)),
            )

    def delete_jobs(self, job_ids: list[str]) -> None:
        if not job_ids:
            return
        with self._lock, self._connection:
            self._connection.executemany("DELETE FROM jobs WHERE job_id = ?", ((item,) for item in job_ids))

    def close(self) -> None:
        with self._lock:
            self._connection.close()
