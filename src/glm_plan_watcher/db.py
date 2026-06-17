"""SQLite persistence for the local daemon."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from glm_plan_watcher.models import WatchEvent

SCHEMA_VERSION = 2

_TARGET_COLUMN_ADDITIONS = {
    "active_window_start": "TEXT NOT NULL DEFAULT ''",
    "active_window_end": "TEXT NOT NULL DEFAULT ''",
    "active_timezone": "TEXT NOT NULL DEFAULT ''",
    "active_interval_seconds": "REAL NOT NULL DEFAULT 3",
    "active_jitter_seconds": "REAL NOT NULL DEFAULT 1",
    "idle_interval_seconds": "REAL NOT NULL DEFAULT 600",
    "on_hit_handoff": "INTEGER NOT NULL DEFAULT 1",
}


class Repository:
    """Small sqlite3 repository for daemon state.

    This intentionally avoids an ORM: the daemon needs simple local persistence, predictable SQL,
    and easy packaging as a Tauri sidecar dependency.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._shared: sqlite3.Connection | None = None
        if str(self.path) == ":memory:":
            # in-memory DB 是“按连接”的：每次新建连接都会拿到一个空库。必须用一条常驻连接，
            # 否则 init_schema 建的表在后续 connect() 里就消失了。check_same_thread=False
            # 以便 FastAPI TestClient 的线程池能复用同一连接（仅用于测试/内存场景）。
            self._shared = sqlite3.connect(":memory:", check_same_thread=False)
            self._shared.row_factory = sqlite3.Row
            self._shared.execute("PRAGMA foreign_keys = ON")
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.init_schema()

    @contextmanager
    def connect(self) -> Any:
        if self._shared is not None:
            # 常驻内存连接：不在每次调用后关闭，否则库就没了。
            yield self._shared
            self._shared.commit()
            return
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    display_name TEXT NOT NULL,
                    user_data_dir TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL DEFAULT 'stopped',
                    last_login_at TEXT
                );

                CREATE TABLE IF NOT EXISTS targets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                    billing_cycle TEXT NOT NULL,
                    tier TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    interval REAL NOT NULL DEFAULT 90,
                    jitter REAL NOT NULL DEFAULT 30,
                    dry_run INTEGER NOT NULL DEFAULT 0,
                    auto_click_entry INTEGER NOT NULL DEFAULT 1,
                    active_window_start TEXT NOT NULL DEFAULT '',
                    active_window_end TEXT NOT NULL DEFAULT '',
                    active_timezone TEXT NOT NULL DEFAULT '',
                    active_interval_seconds REAL NOT NULL DEFAULT 3,
                    active_jitter_seconds REAL NOT NULL DEFAULT 1,
                    idle_interval_seconds REAL NOT NULL DEFAULT 600,
                    on_hit_handoff INTEGER NOT NULL DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                    target TEXT NOT NULL,
                    type TEXT NOT NULL,
                    button_state TEXT NOT NULL,
                    button_text TEXT NOT NULL DEFAULT '',
                    action TEXT NOT NULL DEFAULT 'none',
                    available INTEGER NOT NULL DEFAULT 0,
                    message TEXT NOT NULL DEFAULT '',
                    ts TEXT NOT NULL,
                    raw_json TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_events_account_ts ON events(account_id, ts DESC);

                CREATE TABLE IF NOT EXISTS artifacts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
                    screenshot_path TEXT NOT NULL DEFAULT '',
                    html_path TEXT NOT NULL DEFAULT '',
                    trace_path TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS workers (
                    account_id INTEGER PRIMARY KEY REFERENCES accounts(id) ON DELETE CASCADE,
                    pid INTEGER,
                    status TEXT NOT NULL,
                    started_at TEXT,
                    last_heartbeat_at TEXT
                );
                """
            )
            self._ensure_target_columns(conn)
            conn.execute(
                "INSERT OR REPLACE INTO schema_meta(key, value) VALUES('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )

    def _ensure_target_columns(self, conn: sqlite3.Connection) -> None:
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(targets)").fetchall()}
        for column, ddl in _TARGET_COLUMN_ADDITIONS.items():
            if column not in existing:
                conn.execute(f"ALTER TABLE targets ADD COLUMN {column} {ddl}")

    def create_account(
        self,
        display_name: str,
        user_data_dir: str,
        status: str = "stopped",
        last_login_at: str | None = None,
    ) -> dict[str, Any]:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO accounts(display_name, user_data_dir, status, last_login_at)
                VALUES (?, ?, ?, ?)
                """,
                (display_name, user_data_dir, status, last_login_at),
            )
            return self.get_account(cursor.lastrowid, conn=conn)

    def list_accounts(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM accounts ORDER BY id").fetchall()
            return [_row_dict(row) for row in rows]

    def get_account(self, account_id: int, conn: sqlite3.Connection | None = None) -> dict[str, Any]:
        owns_conn = conn is None
        if conn is None:
            if self._shared is not None:
                # :memory: 模式必须复用常驻连接，否则裸开会得到一个空库（no such table）。
                conn = self._shared
                owns_conn = False
            else:
                conn = sqlite3.connect(self.path)
                conn.row_factory = sqlite3.Row
        try:
            row = conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
            if row is None:
                raise KeyError(f"account not found: {account_id}")
            return _row_dict(row)
        finally:
            if owns_conn:
                conn.close()

    def update_account(self, account_id: int, **fields: Any) -> dict[str, Any]:
        allowed = {"display_name", "user_data_dir", "status", "last_login_at"}
        updates = {key: value for key, value in fields.items() if key in allowed}
        if updates:
            with self.connect() as conn:
                assignments = ", ".join(f"{key} = ?" for key in updates)
                conn.execute(
                    f"UPDATE accounts SET {assignments} WHERE id = ?",
                    (*updates.values(), account_id),
                )
                return self.get_account(account_id, conn=conn)
        return self.get_account(account_id)

    def delete_account(self, account_id: int) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM accounts WHERE id = ?", (account_id,))

    def create_target(
        self,
        account_id: int,
        billing_cycle: str,
        tier: str,
        enabled: bool = True,
        interval: float = 90.0,
        jitter: float = 30.0,
        dry_run: bool = False,
        auto_click_entry: bool = True,
        active_window_start: str = "",
        active_window_end: str = "",
        active_timezone: str = "",
        active_interval_seconds: float = 3.0,
        active_jitter_seconds: float = 1.0,
        idle_interval_seconds: float = 600.0,
        on_hit_handoff: bool = True,
    ) -> dict[str, Any]:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO targets(
                    account_id, billing_cycle, tier, enabled, interval, jitter, dry_run,
                    auto_click_entry, active_window_start, active_window_end, active_timezone,
                    active_interval_seconds, active_jitter_seconds, idle_interval_seconds,
                    on_hit_handoff
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    account_id,
                    billing_cycle,
                    tier,
                    int(enabled),
                    interval,
                    jitter,
                    int(dry_run),
                    int(auto_click_entry),
                    active_window_start,
                    active_window_end,
                    active_timezone,
                    active_interval_seconds,
                    active_jitter_seconds,
                    idle_interval_seconds,
                    int(on_hit_handoff),
                ),
            )
            return self.get_target(cursor.lastrowid, conn=conn)

    def list_targets(self, account_id: int | None = None, enabled_only: bool = False) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if account_id is not None:
            clauses.append("account_id = ?")
            params.append(account_id)
        if enabled_only:
            clauses.append("enabled = 1")
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.connect() as conn:
            rows = conn.execute(f"SELECT * FROM targets{where} ORDER BY id", params).fetchall()
            return [_normalize_bool_fields(row) for row in rows]

    def get_target(self, target_id: int, conn: sqlite3.Connection | None = None) -> dict[str, Any]:
        owns_conn = conn is None
        if conn is None:
            if self._shared is not None:
                # :memory: 模式必须复用常驻连接，否则裸开会得到一个空库（no such table）。
                conn = self._shared
                owns_conn = False
            else:
                conn = sqlite3.connect(self.path)
                conn.row_factory = sqlite3.Row
        try:
            row = conn.execute("SELECT * FROM targets WHERE id = ?", (target_id,)).fetchone()
            if row is None:
                raise KeyError(f"target not found: {target_id}")
            return _normalize_bool_fields(row)
        finally:
            if owns_conn:
                conn.close()

    def update_target(self, target_id: int, **fields: Any) -> dict[str, Any]:
        allowed = {
            "billing_cycle",
            "tier",
            "enabled",
            "interval",
            "jitter",
            "dry_run",
            "auto_click_entry",
            "active_window_start",
            "active_window_end",
            "active_timezone",
            "active_interval_seconds",
            "active_jitter_seconds",
            "idle_interval_seconds",
            "on_hit_handoff",
        }
        updates = {key: _sqlite_bool(value) for key, value in fields.items() if key in allowed}
        if updates:
            with self.connect() as conn:
                assignments = ", ".join(f"{key} = ?" for key in updates)
                conn.execute(
                    f"UPDATE targets SET {assignments} WHERE id = ?",
                    (*updates.values(), target_id),
                )
                return self.get_target(target_id, conn=conn)
        return self.get_target(target_id)

    def delete_target(self, target_id: int) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM targets WHERE id = ?", (target_id,))

    def insert_event(self, account_id: int, event: WatchEvent, raw_json: str | None = None) -> int:
        raw_json = raw_json or event.to_json_line()
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO events(
                    account_id, target, type, button_state, button_text, action, available,
                    message, ts, raw_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    account_id,
                    event.target,
                    event.type,
                    event.button_state,
                    event.button_text,
                    event.action,
                    int(event.available),
                    event.message,
                    event.ts.isoformat(),
                    raw_json,
                ),
            )
            return int(cursor.lastrowid)

    def list_events(
        self,
        account_id: int | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if account_id is not None:
            clauses.append("account_id = ?")
            params.append(account_id)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM events{where} ORDER BY id DESC LIMIT ? OFFSET ?",
                (*params, limit, offset),
            ).fetchall()
            return [_normalize_bool_fields(row) for row in rows]

    def create_artifact(
        self,
        event_id: int,
        screenshot_path: str = "",
        html_path: str = "",
        trace_path: str = "",
    ) -> dict[str, Any]:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO artifacts(event_id, screenshot_path, html_path, trace_path)
                VALUES (?, ?, ?, ?)
                """,
                (event_id, screenshot_path, html_path, trace_path),
            )
            row = conn.execute(
                "SELECT * FROM artifacts WHERE id = ?",
                (cursor.lastrowid,),
            ).fetchone()
            return _row_dict(row)

    def upsert_worker(
        self,
        account_id: int,
        pid: int | None,
        status: str,
        started_at: str | None = None,
        last_heartbeat_at: str | None = None,
    ) -> dict[str, Any]:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO workers(account_id, pid, status, started_at, last_heartbeat_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(account_id) DO UPDATE SET
                    pid = excluded.pid,
                    status = excluded.status,
                    started_at = excluded.started_at,
                    last_heartbeat_at = excluded.last_heartbeat_at
                """,
                (account_id, pid, status, started_at, last_heartbeat_at),
            )
            return self.get_worker(account_id, conn=conn)

    def update_worker_status(
        self,
        account_id: int,
        status: str,
        pid: int | None = None,
        last_heartbeat_at: str | None = None,
    ) -> dict[str, Any]:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO workers(account_id, pid, status, last_heartbeat_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(account_id) DO UPDATE SET
                    pid = excluded.pid,
                    status = excluded.status,
                    last_heartbeat_at = COALESCE(excluded.last_heartbeat_at, workers.last_heartbeat_at)
                """,
                (account_id, pid, status, last_heartbeat_at),
            )
            return self.get_worker(account_id, conn=conn)

    def update_worker_heartbeat(self, account_id: int, last_heartbeat_at: str) -> dict[str, Any]:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE workers SET last_heartbeat_at = ?, status = 'running'
                WHERE account_id = ?
                """,
                (last_heartbeat_at, account_id),
            )
            worker = self.get_worker(account_id, conn=conn)
            if worker is None:
                conn.execute(
                    """
                    INSERT INTO workers(account_id, status, last_heartbeat_at)
                    VALUES (?, 'running', ?)
                    """,
                    (account_id, last_heartbeat_at),
                )
                worker = self.get_worker(account_id, conn=conn)
            return worker

    def get_worker(
        self,
        account_id: int,
        conn: sqlite3.Connection | None = None,
    ) -> dict[str, Any] | None:
        owns_conn = conn is None
        if conn is None:
            if self._shared is not None:
                # :memory: 模式必须复用常驻连接，否则裸开会得到一个空库（no such table）。
                conn = self._shared
                owns_conn = False
            else:
                conn = sqlite3.connect(self.path)
                conn.row_factory = sqlite3.Row
        try:
            row = conn.execute("SELECT * FROM workers WHERE account_id = ?", (account_id,)).fetchone()
            return _row_dict(row) if row is not None else None
        finally:
            if owns_conn:
                conn.close()

    def list_workers(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM workers ORDER BY account_id").fetchall()
            return [_row_dict(row) for row in rows]


def encode_raw_json(data: Mapping[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def _row_dict(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


def _normalize_bool_fields(row: sqlite3.Row) -> dict[str, Any]:
    data = _row_dict(row)
    for key in ("enabled", "dry_run", "auto_click_entry", "on_hit_handoff", "available"):
        if key in data:
            data[key] = bool(data[key])
    return data


def _sqlite_bool(value: Any) -> Any:
    if isinstance(value, bool):
        return int(value)
    return value
