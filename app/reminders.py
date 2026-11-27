"""提醒规则：纯函数，`now` 一律由参数传入，方便测试假时钟。

调用方（scheduler）负责去重：`reminders_sent` 表里已有 key 就不再发。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Callable

from . import db as dbm, services
from .config import Config

log = logging.getLogger(__name__)

# 任务到点的容忍窗口：定时器每分钟一跳，可能晚几十秒
DUE_WINDOW = timedelta(minutes=2)
# 打卡后多久之内算「刚上班」，用来发早报
MORNING_WINDOW = timedelta(minutes=1)
# 项目多少天没日志算停滞
STALE_DAYS = 3


@dataclass(frozen=True)
class Reminder:
    key: str   # 去重用，写进 reminders_sent.key
    title: str
    body: str


# ---------------------------------------------------------------- 去重
def already_sent(conn, key: str) -> bool:
    return dbm.one(conn, "SELECT 1 AS x FROM reminders_sent WHERE key = ?", (key,)) is not None


def mark_sent(conn, key: str, now: datetime) -> None:
    """记录已发送；并发下 key 冲突就当已发过，不报错。"""
    try:
        dbm.insert(
            conn,
            "reminders_sent",
            {"key": key, "sent_at": now.replace(microsecond=0).isoformat()},
        )
    except Exception:  # sqlite3.IntegrityError：唯一键冲突
        log.debug("提醒已存在，跳过写入：%s", key)


# ---------------------------------------------------------------- 规则
def _task_reminders(conn, now: datetime) -> list[Reminder]:
    """任务到点：未完成、有具体时间，落在 (now-2min, now]。"""
    out: list[Reminder] = []
    rows = dbm.all_(
        conn,
        """SELECT * FROM tasks
           WHERE done = 0 AND due_date IS NOT NULL AND due_time IS NOT NULL""",
    )
    for task in rows:
        try:
            due = datetime.fromisoformat(f"{task['due_date']}T{task['due_time']}")
        except ValueError:
            continue
        if now - DUE_WINDOW < due <= now:
            out.append(
                Reminder(
                    key=f"task:{task['id']}:{due.isoformat()}",
                    title=f"到点了：{task['title']}",
                    body=f"{task['due_time']} 的任务还没完成"
                    + (f"\n{task['notes']}" if task.get("notes") else ""),
                )
            )
    return out


def _clock_in_reminder(conn, cfg: Config, now: datetime) -> list[Reminder]:
    """该上班了：工作日、到点还没打卡、且还没到中午。"""
    if now.weekday() >= 5:
        return []
    if now.hour >= 12:
        return []
    try:
        remind_at = time.fromisoformat(str(cfg.work.clock_in_remind))
    except ValueError:
        remind_at = time(9, 30)
    if now.time() < remind_at:
        return []
    if services.get_open_session(conn):
        return []  # 昨晚的打卡还开着，说明人还在班上
    today = now.date().isoformat()
    if dbm.one(conn, "SELECT 1 AS x FROM work_sessions WHERE date = ?", (today,)):
        return []
    return [
        Reminder(
            key=f"clockin:{today}",
            title="该上班打卡了",
            body="到点还没打卡，先写下今天三件事。",
        )
    ]


def _overtime_reminder(conn, cfg: Config, now: datetime) -> list[Reminder]:
    """工作太久：进行中的 session 超过 max_hours。"""
    session = services.get_open_session(conn)
    if not session:
        return []
    started = datetime.fromisoformat(session["clock_in"])
    hours = (now - started).total_seconds() / 3600
    if hours < float(cfg.work.max_hours):
        return []
    return [
        Reminder(
            key=f"overtime:{session['id']}",
            title="该下班了",
            body=f"已经连续工作 {hours:.1f} 小时，超过 {cfg.work.max_hours} 小时了。",
        )
    ]


def _morning_reminder(conn, now: datetime) -> list[Reminder]:
    """上班早报：打卡后 1 分钟内，报逾期和今天到期的任务。"""
    session = services.get_open_session(conn)
    if not session:
        return []
    started = datetime.fromisoformat(session["clock_in"])
    if not (timedelta(0) <= now - started <= MORNING_WINDOW):
        return []

    today = now.date().isoformat()
    tasks = dbm.all_(
        conn,
        """SELECT * FROM tasks
           WHERE done = 0 AND due_date IS NOT NULL AND due_date <= ?
           ORDER BY due_date, priority""",
        (today,),
    )
    overdue = [t for t in tasks if t["due_date"] < today]
    due_today = [t for t in tasks if t["due_date"] == today]
    if not overdue and not due_today:
        return [Reminder(
            key=f"morning:{session['id']}",
            title="早上好",
            body="今天没有到期任务，按计划推进就好。",
        )]

    preview = [t["title"] for t in (overdue + due_today)[:3]]
    lines = [f"逾期 {len(overdue)} 个，今天到期 {len(due_today)} 个。"]
    lines += [f"· {title}" for title in preview]
    return [
        Reminder(
            key=f"morning:{session['id']}",
            title="早上好，先看这三件" if len(preview) >= 3 else "早上好",
            body="\n".join(lines),
        )
    ]


def _stale_projects(conn, now: datetime) -> list[Reminder]:
    """项目停滞：active 项目超过 3 天没日志，每天 10 点后提醒一次。"""
    if now.hour < 10:
        return []
    today = now.date()
    out: list[Reminder] = []
    for project in dbm.all_(conn, "SELECT * FROM projects WHERE status = 'active'"):
        last = dbm.one(
            conn,
            "SELECT MAX(date) AS d FROM project_logs WHERE project_id = ?",
            (project["id"],),
        )
        base_text = (last or {}).get("d") or str(project.get("created_at") or today.isoformat())[:10]
        try:
            base = date.fromisoformat(base_text[:10])
        except ValueError:
            continue
        days = (today - base).days
        if days > STALE_DAYS:
            out.append(
                Reminder(
                    key=f"stale:{project['id']}:{today.isoformat()}",
                    title=f"「{project['name']}」停滞 {days} 天",
                    body=(
                        f"下一步：{project['next_step']}"
                        if project.get("next_step")
                        else "已经 3 天以上没写进度日志了，今天推进一点？"
                    ),
                )
            )
    return out


# ---------------------------------------------------------------- 汇总
def due_reminders(conn, cfg: Config, now: datetime) -> list[Reminder]:
    """当前该发的提醒（尚未去重，调用方负责过滤已发送的）。"""
    found: list[Reminder] = []
    for collector in (
        lambda: _task_reminders(conn, now),
        lambda: _clock_in_reminder(conn, cfg, now),
        lambda: _overtime_reminder(conn, cfg, now),
        lambda: _morning_reminder(conn, now),
        lambda: _stale_projects(conn, now),
    ):
        try:
            found.extend(collector())
        except Exception:  # 单条规则出错不能拖垮整轮提醒
            log.exception("提醒规则出错")
    return found


def dispatch(
    conn,
    cfg: Config,
    now: datetime | None = None,
    notify_func: Callable[[str, str], Any] | None = None,
) -> list[Reminder]:
    """算出提醒、去重、发通知并记账，返回本次真正发出去的。"""
    moment = now or datetime.now()
    if notify_func is None:
        from .notify import notify as notify_func  # 延迟导入，测试可注入

    sent: list[Reminder] = []
    for reminder in due_reminders(conn, cfg, moment):
        if already_sent(conn, reminder.key):
            continue
        try:
            notify_func(reminder.title, reminder.body)
        except Exception:  # 通知失败也要记账，否则会每分钟重试刷屏
            log.exception("发送通知失败：%s", reminder.key)
        mark_sent(conn, reminder.key, moment)
        sent.append(reminder)
    return sent
