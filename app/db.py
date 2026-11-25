"""SQLite 连接、建表与迁移，以及一层极薄的读写助手。

约定：
- 所有时间用本地时区的 ISO 字符串（如 2026-11-24T09:31:00），日期用 YYYY-MM-DD。
- 每张表都有 id / created_at / updated_at，由助手函数自动维护。
- 每次请求开一个新连接（SQLite 开销极小），避免多线程共享连接的问题。
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Iterator

SCHEMA_VERSION = 1


# ---------------------------------------------------------------- 时间工具
def now_iso() -> str:
    """当前本地时间，秒级精度。"""
    return datetime.now().replace(microsecond=0).isoformat()


def today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


# ---------------------------------------------------------------- 连接
@contextmanager
def connect(db_path: Path | str) -> Iterator[sqlite3.Connection]:
    """打开连接，正常结束自动 commit，异常自动 rollback。"""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # FastAPI 的 yield 依赖可能在不同线程里打开和关闭（commit）同一个连接，
    # 所以关闭同线程检查；每个请求独占一个连接，不会被并发使用。
    conn = sqlite3.connect(str(path), timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")  # 每个连接都要单独开
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------- 建表
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS work_sessions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    date        TEXT NOT NULL,              -- YYYY-MM-DD，一天可多段
    clock_in    TEXT NOT NULL,              -- ISO 时间
    clock_out   TEXT,
    plan        TEXT,                       -- 今天三件事
    summary     TEXT,                       -- 下班总结
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_date ON work_sessions(date);

CREATE TABLE IF NOT EXISTS screenshots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  INTEGER REFERENCES work_sessions(id) ON DELETE CASCADE,
    taken_at    TEXT NOT NULL,
    path        TEXT NOT NULL,              -- 相对 screenshots/ 的路径
    note        TEXT,                       -- 预留给以后 AI 生成的描述
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_shots_taken ON screenshots(taken_at);

CREATE TABLE IF NOT EXISTS projects (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'active',   -- active/paused/done
    progress      INTEGER NOT NULL DEFAULT 0,       -- 0-100
    deadline      TEXT,
    current_state TEXT,
    next_step     TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS project_logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    date        TEXT NOT NULL,
    text        TEXT NOT NULL,
    minutes     INTEGER,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_logs_project ON project_logs(project_id, date);

CREATE TABLE IF NOT EXISTS tasks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT NOT NULL,
    notes       TEXT,
    project_id  INTEGER REFERENCES projects(id) ON DELETE SET NULL,
    due_date    TEXT,
    due_time    TEXT,                       -- HH:MM，可空
    priority    INTEGER NOT NULL DEFAULT 2, -- 1 高 / 2 中 / 3 低
    repeat      TEXT NOT NULL DEFAULT 'none', -- none/daily/weekdays/weekly/monthly
    done        INTEGER NOT NULL DEFAULT 0,
    done_at     TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_due ON tasks(done, due_date);

CREATE TABLE IF NOT EXISTS reminders_sent (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    key         TEXT NOT NULL UNIQUE,       -- 如 task:12:2026-11-24T09:00
    sent_at     TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ai_accounts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    provider    TEXT NOT NULL,              -- claude/chatgpt/gemini/grok
    label       TEXT NOT NULL,
    plan        TEXT,
    source      TEXT NOT NULL DEFAULT 'manual', -- manual/server
    host        TEXT,                       -- source=server 时对应 usage-<host>.json
    renew_day   INTEGER,                    -- 订阅续费日
    notes       TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ai_limits (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id  INTEGER NOT NULL REFERENCES ai_accounts(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,              -- 如 "5 小时窗口"
    period      TEXT NOT NULL,              -- 5h/day/week/month
    cap         REAL,
    unit        TEXT,                       -- % / msgs / tokens / usd
    used        REAL NOT NULL DEFAULT 0,
    reset_at    TEXT,                       -- 到点自动清零并顺延一个周期
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_limits_account ON ai_limits(account_id);

CREATE TABLE IF NOT EXISTS ai_usage (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id    INTEGER NOT NULL REFERENCES ai_accounts(id) ON DELETE CASCADE,
    host          TEXT,
    tool          TEXT,                     -- claude_code / codex / gemini_cli
    period_start  TEXT NOT NULL,            -- 小时窗口起点
    input_tokens  INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cache_tokens  INTEGER NOT NULL DEFAULT 0,
    requests      INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_usage_account ON ai_usage(account_id, period_start);
"""


def init_db(db_path: Path | str) -> None:
    """建表 + 迁移。用 PRAGMA user_version 记录架构版本。"""
    with connect(db_path) as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version < SCHEMA_VERSION:
            # 第 1 版：一次建全所有表，后续里程碑只加逻辑，不再频繁改表
            conn.executescript(SCHEMA_SQL)
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


# ---------------------------------------------------------------- 读写助手
def rows(cursor: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(r) for r in cursor]


def all_(conn: sqlite3.Connection, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
    return rows(conn.execute(sql, tuple(params)).fetchall())


def one(conn: sqlite3.Connection, sql: str, params: Iterable[Any] = ()) -> dict[str, Any] | None:
    row = conn.execute(sql, tuple(params)).fetchone()
    return dict(row) if row else None


def get(conn: sqlite3.Connection, table: str, row_id: int) -> dict[str, Any] | None:
    return one(conn, f"SELECT * FROM {table} WHERE id = ?", (row_id,))


def insert(conn: sqlite3.Connection, table: str, data: dict[str, Any]) -> int:
    """插入一行，自动补 created_at / updated_at，返回新 id。"""
    ts = now_iso()
    payload = {**data, "created_at": ts, "updated_at": ts}
    cols = ", ".join(payload)
    placeholders = ", ".join("?" for _ in payload)
    cur = conn.execute(
        f"INSERT INTO {table} ({cols}) VALUES ({placeholders})", tuple(payload.values())
    )
    return int(cur.lastrowid)


def update(conn: sqlite3.Connection, table: str, row_id: int, data: dict[str, Any]) -> bool:
    """按 id 局部更新，自动刷新 updated_at。data 为空时直接返回 False。"""
    if not data:
        return False
    payload = {**data, "updated_at": now_iso()}
    assignments = ", ".join(f"{key} = ?" for key in payload)
    cur = conn.execute(
        f"UPDATE {table} SET {assignments} WHERE id = ?", (*payload.values(), row_id)
    )
    return cur.rowcount > 0


def delete(conn: sqlite3.Connection, table: str, row_id: int) -> bool:
    cur = conn.execute(f"DELETE FROM {table} WHERE id = ?", (row_id,))
    return cur.rowcount > 0
