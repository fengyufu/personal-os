"""共享业务逻辑：打卡。

api.py 和托盘都调这里，避免两套逻辑写出不一致的行为。
约定：进行中的 session 不按日期过滤，所以跨零点下班也能正常结束。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

from . import db as dbm


class ConflictError(Exception):
    """业务冲突：重复上班 / 没上班就下班。API 层翻译成 409。"""


# ---------------------------------------------------------------- 时长
def session_minutes(session: dict[str, Any], until: datetime | None = None) -> int:
    """一段打卡的实际时长（分钟）；未下班则算到 until（默认现在）。"""
    start = datetime.fromisoformat(session["clock_in"])
    if session.get("clock_out"):
        end = datetime.fromisoformat(session["clock_out"])
    else:
        end = until or datetime.now()
    return max(0, int((end - start).total_seconds() // 60))


# ---------------------------------------------------------------- 查询
def get_open_session(conn: sqlite3.Connection) -> dict[str, Any] | None:
    """当前进行中的 session（没有则 None）。不分日期，支持跨零点。"""
    return dbm.one(
        conn,
        "SELECT * FROM work_sessions WHERE clock_out IS NULL ORDER BY clock_in DESC",
    )


# ---------------------------------------------------------------- 打卡
def clock_in(
    conn: sqlite3.Connection,
    plan: str | None = None,
    at: datetime | None = None,
) -> dict[str, Any]:
    """上班打卡：新建一段 session。已在上班中则抛 ConflictError。"""
    if get_open_session(conn):
        raise ConflictError("今天已经上班打卡了，先下班再重新上班")

    moment = (at or datetime.now()).replace(microsecond=0)
    session_id = dbm.insert(
        conn,
        "work_sessions",
        {"date": moment.strftime("%Y-%m-%d"), "clock_in": moment.isoformat(), "plan": plan},
    )
    # 立刻提交：前端打完卡紧接着刷新，晚几毫秒提交会读到旧状态
    conn.commit()
    return dbm.get(conn, "work_sessions", session_id) or {}


def clock_out(
    conn: sqlite3.Connection,
    summary: str | None = None,
    at: datetime | None = None,
) -> dict[str, Any]:
    """下班打卡：结束当前 session 并写入总结。没有进行中的打卡则抛 ConflictError。"""
    running = get_open_session(conn)
    if not running:
        raise ConflictError("当前没有进行中的打卡，请先上班打卡")

    moment = (at or datetime.now()).replace(microsecond=0)
    dbm.update(
        conn,
        "work_sessions",
        running["id"],
        {"clock_out": moment.isoformat(), "summary": summary},
    )
    # 立刻提交：下班后前端马上要看到「已下班」，托盘也要能立刻再上班
    conn.commit()
    session = dbm.get(conn, "work_sessions", running["id"]) or {}
    session["worked_minutes"] = session_minutes(session, until=moment)
    return session
