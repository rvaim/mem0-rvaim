"""SQLite state store owned exclusively by the daemon process.

Tables:
    workspaces          workspace registry (id, display name, cwd)
    sessions            session -> workspace mapping + registration info
    transcript_cursors  per-session transcript read offset
    processed_events    event hashes already captured (idempotency)
    retry_tasks         background failure retry queue
    plugin_state        key/value runtime state
    migrations          schema version history

Only one daemon may open this database; the bootstrap layer enforces that
with a file lock.  All writes are serialized through a single connection
with ``check_same_thread=False`` guarded by a threading lock.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS workspaces (
    workspace_id   TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    cwd            TEXT,
    created_at     TEXT NOT NULL,
    last_seen_at   TEXT
);
CREATE TABLE IF NOT EXISTS sessions (
    session_id     TEXT PRIMARY KEY,
    workspace_id   TEXT NOT NULL,
    cwd            TEXT NOT NULL,
    host           TEXT DEFAULT 'unknown',
    pid            INTEGER,
    registered_at  TEXT NOT NULL,
    last_seen_at   TEXT
);
CREATE TABLE IF NOT EXISTS transcript_cursors (
    session_id      TEXT PRIMARY KEY,
    transcript_path TEXT NOT NULL,
    offset_bytes    INTEGER DEFAULT 0,
    processed_lines INTEGER DEFAULT 0,
    updated_at      TEXT
);
CREATE TABLE IF NOT EXISTS processed_events (
    session_id   TEXT NOT NULL,
    event_hash   TEXT NOT NULL,
    processed_at TEXT NOT NULL,
    PRIMARY KEY (session_id, event_hash)
);
CREATE TABLE IF NOT EXISTS retry_tasks (
    task_id         TEXT PRIMARY KEY,
    kind            TEXT NOT NULL,
    payload         TEXT NOT NULL,
    attempts        INTEGER DEFAULT 0,
    max_attempts    INTEGER DEFAULT 5,
    next_attempt_at REAL NOT NULL,
    last_error      TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT
);
CREATE TABLE IF NOT EXISTS plugin_state (
    key        TEXT PRIMARY KEY,
    value      TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS migrations (
    version   INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);
"""


class StateStore:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._path = str(db_path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    # ------------------------------------------------------------------
    # generic helpers
    # ------------------------------------------------------------------
    def _execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def _query_one(self, sql: str, params: tuple = ()) -> Optional[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    def _query_all(self, sql: str, params: tuple = ()) -> List[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------
    # workspaces / sessions
    # ------------------------------------------------------------------
    def register_workspace(self, workspace_id: str, name: str, cwd: str) -> None:
        now = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        self._execute(
            "INSERT INTO workspaces(workspace_id, name, cwd, created_at, last_seen_at) "
            "VALUES(?,?,?,?,?) "
            "ON CONFLICT(workspace_id) DO UPDATE SET name=excluded.name, "
            "cwd=excluded.cwd, last_seen_at=excluded.last_seen_at",
            (workspace_id, name, cwd, now, now),
        )

    def register_session(
        self,
        session_id: str,
        workspace_id: str,
        cwd: str,
        host: str = "unknown",
        pid: Optional[int] = None,
        workspace_name: Optional[str] = None,
    ) -> None:
        now = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        name = workspace_name or workspace_id
        self.register_workspace(workspace_id, name, cwd)
        self._execute(
            "INSERT INTO sessions(session_id, workspace_id, cwd, host, pid, registered_at, last_seen_at) "
            "VALUES(?,?,?,?,?,?,?) "
            "ON CONFLICT(session_id) DO UPDATE SET workspace_id=excluded.workspace_id, "
            "cwd=excluded.cwd, host=excluded.host, pid=excluded.pid, last_seen_at=excluded.last_seen_at",
            (session_id, workspace_id, cwd, host, pid, now, now),
        )

    def workspace_for_session(self, session_id: str) -> Optional[str]:
        row = self._query_one("SELECT workspace_id FROM sessions WHERE session_id=?", (session_id,))
        return row["workspace_id"] if row else None

    def session_info(self, session_id: str) -> Optional[Dict[str, Any]]:
        row = self._query_one(
            "SELECT * FROM sessions WHERE session_id=?", (session_id,)
        )
        return dict(row) if row else None

    def list_workspaces(self) -> List[Dict[str, Any]]:
        rows = self._query_all(
            "SELECT w.*, (SELECT COUNT(*) FROM sessions s WHERE s.workspace_id=w.workspace_id) "
            "AS session_count FROM workspaces w ORDER BY w.last_seen_at DESC"
        )
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # transcript cursors
    # ------------------------------------------------------------------
    def get_cursor(self, session_id: str) -> Optional[Dict[str, Any]]:
        row = self._query_one(
            "SELECT * FROM transcript_cursors WHERE session_id=?", (session_id,)
        )
        return dict(row) if row else None

    def set_cursor(self, session_id: str, transcript_path: str,
                   offset_bytes: int, processed_lines: int) -> None:
        now = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        self._execute(
            "INSERT INTO transcript_cursors(session_id, transcript_path, offset_bytes, processed_lines, updated_at) "
            "VALUES(?,?,?,?,?) "
            "ON CONFLICT(session_id) DO UPDATE SET transcript_path=excluded.transcript_path, "
            "offset_bytes=excluded.offset_bytes, processed_lines=excluded.processed_lines, "
            "updated_at=excluded.updated_at",
            (session_id, transcript_path, offset_bytes, processed_lines, now),
        )

    # ------------------------------------------------------------------
    # processed event hashes (idempotency)
    # ------------------------------------------------------------------
    def is_event_processed(self, session_id: str, event_hash: str) -> bool:
        row = self._query_one(
            "SELECT 1 FROM processed_events WHERE session_id=? AND event_hash=?",
            (session_id, event_hash),
        )
        return row is not None

    def mark_event_processed(self, session_id: str, event_hash: str) -> None:
        now = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        self._execute(
            "INSERT OR IGNORE INTO processed_events(session_id, event_hash, processed_at) "
            "VALUES(?,?,?)",
            (session_id, event_hash, now),
        )

    # ------------------------------------------------------------------
    # retry tasks
    # ------------------------------------------------------------------
    def enqueue_retry(self, task_id: str, kind: str, payload: Dict[str, Any]) -> None:
        now = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        self._execute(
            "INSERT OR REPLACE INTO retry_tasks(task_id, kind, payload, attempts, "
            "max_attempts, next_attempt_at, last_error, created_at, updated_at) "
            "VALUES(?,?,?,0,5,?,NULL,?,?)",
            (task_id, kind, json.dumps(payload), time.time(), now, now),
        )

    def claim_due_retries(self, limit: int = 10) -> List[Dict[str, Any]]:
        rows = self._query_all(
            "SELECT * FROM retry_tasks WHERE next_attempt_at <= ? "
            "ORDER BY next_attempt_at ASC LIMIT ?",
            (time.time(), limit),
        )
        return [dict(r) for r in rows]

    def bump_retry(self, task_id: str, error: str, delay_seconds: int = 300) -> None:
        now = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        self._execute(
            "UPDATE retry_tasks SET attempts=attempts+1, next_attempt_at=?, "
            "last_error=?, updated_at=? WHERE task_id=?",
            (time.time() + delay_seconds, error[:500], now, task_id),
        )

    def drop_retry(self, task_id: str) -> None:
        self._execute("DELETE FROM retry_tasks WHERE task_id=?", (task_id,))

    # ------------------------------------------------------------------
    # plugin state
    # ------------------------------------------------------------------
    def get_state(self, key: str, default: Any = None) -> Any:
        row = self._query_one("SELECT value FROM plugin_state WHERE key=?", (key,))
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except (json.JSONDecodeError, TypeError):
            return row["value"]

    def set_state(self, key: str, value: Any) -> None:
        now = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        self._execute(
            "INSERT INTO plugin_state(key, value, updated_at) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (key, json.dumps(value), now),
        )

    # ------------------------------------------------------------------
    # migrations
    # ------------------------------------------------------------------
    def current_version(self) -> int:
        row = self._query_one("SELECT MAX(version) AS v FROM migrations")
        return int(row["v"]) if row and row["v"] is not None else 0

    def apply_migration(self, version: int, statements: List[str]) -> None:
        now = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        with self._lock:
            for sql in statements:
                self._conn.execute(sql)
            self._conn.execute(
                "INSERT OR REPLACE INTO migrations(version, applied_at) VALUES(?,?)",
                (version, now),
            )
            self._conn.commit()
