"""reminders.py：五条提醒规则的命中 / 不命中，以及去重。"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app import db as dbm, reminders, services
from app.config import Config

# 2026-11-27 是周五，2026-11-28 是周六
FRI = datetime(2026, 11, 27, 10, 0)


@pytest.fixture
def conn(db_path):
    dbm.init_db(db_path)
    with dbm.connect(db_path) as connection:
        yield connection


@pytest.fixture
def cfg(tmp_path):
    return Config({"work": {"clock_in_remind": "09:30", "max_hours": 9}}, root=tmp_path)


def add_task(conn, **fields):
    payload = {"title": "任务", "priority": 2, "repeat": "none", "done": 0, **fields}
    return dbm.insert(conn, "tasks", payload)


def keys(found):
    return [r.key for r in found]


# ---------------------------------------------------------------- 任务到点
def test_task_due_hits_within_window(conn, cfg):
    task_id = add_task(conn, title="交周报", due_date="2026-11-27", due_time="10:00")
    found = reminders.due_reminders(conn, cfg, datetime(2026, 11, 27, 10, 0, 30))
    assert f"task:{task_id}:2026-11-27T10:00:00" in keys(found)
    assert found[0].title == "到点了：交周报"


def test_task_due_misses_outside_window(conn, cfg):
    add_task(conn, due_date="2026-11-27", due_time="09:50")  # 已经过了 2 分钟窗口
    add_task(conn, due_date="2026-11-27", due_time="10:05")  # 还没到
    found = reminders.due_reminders(conn, cfg, datetime(2026, 11, 27, 10, 0, 30))
    assert [k for k in keys(found) if k.startswith("task:")] == []


def test_task_done_not_reminded(conn, cfg):
    add_task(conn, due_date="2026-11-27", due_time="10:00", done=1)
    found = reminders.due_reminders(conn, cfg, datetime(2026, 11, 27, 10, 0, 30))
    assert [k for k in keys(found) if k.startswith("task:")] == []


# ---------------------------------------------------------------- 该上班了
def test_clockin_reminder_hits_weekday_morning(conn, cfg):
    found = reminders.due_reminders(conn, cfg, datetime(2026, 11, 27, 9, 35))
    assert "clockin:2026-11-27" in keys(found)


def test_clockin_reminder_misses_before_time_and_after_noon(conn, cfg):
    assert "clockin:2026-11-27" not in keys(
        reminders.due_reminders(conn, cfg, datetime(2026, 11, 27, 9, 0))
    )
    assert "clockin:2026-11-27" not in keys(
        reminders.due_reminders(conn, cfg, datetime(2026, 11, 27, 12, 30))
    )


def test_clockin_reminder_misses_weekend(conn, cfg):
    found = reminders.due_reminders(conn, cfg, datetime(2026, 11, 28, 9, 35))
    assert "clockin:2026-11-28" not in keys(found)


def test_clockin_reminder_misses_when_already_clocked(conn, cfg):
    services.clock_in(conn, "已经上班了", at=datetime(2026, 11,27, 8, 50))
    found = reminders.due_reminders(conn, cfg, datetime(2026, 11, 27, 9, 35))
    assert "clockin:2026-11-27" not in keys(found)


def test_clockin_reminder_misses_with_open_session_from_yesterday(conn, cfg):
    """昨晚没下班，人还在班上，不该催打卡。"""
    services.clock_in(conn, "通宵", at=datetime(2026, 11, 26, 22, 0))
    found = reminders.due_reminders(conn, cfg, datetime(2026, 11, 27, 9, 35))
    assert "clockin:2026-11-27" not in keys(found)


# ---------------------------------------------------------------- 工作太久
def test_overtime_hits_after_max_hours(conn, cfg):
    session = services.clock_in(conn, None, at=datetime(2026, 11, 27, 8, 0))
    found = reminders.due_reminders(conn, cfg, datetime(2026, 11, 27, 17, 0))
    assert f"overtime:{session['id']}" in keys(found)


def test_overtime_misses_before_max_hours(conn, cfg):
    services.clock_in(conn, None, at=datetime(2026, 11, 27, 8, 0))
    found = reminders.due_reminders(conn, cfg, datetime(2026, 11, 27, 16, 0))
    assert not [k for k in keys(found) if k.startswith("overtime:")]


def test_overtime_across_midnight(conn, cfg):
    """昨晚 22:00 上班，今早 09:00 还在岗：11 小时，要提醒。"""
    session = services.clock_in(conn, "通宵", at=datetime(2026, 11, 26, 22, 0))
    found = reminders.due_reminders(conn, cfg, datetime(2026, 11, 27, 9, 0))
    assert f"overtime:{session['id']}" in keys(found)


# ---------------------------------------------------------------- 上班早报
def test_morning_report_hits_right_after_clock_in(conn, cfg):
    session = services.clock_in(conn, "写 M2", at=datetime(2026, 11, 27, 9, 29, 30))
    add_task(conn, title="A", due_date="2026-11-26")
    add_task(conn, title="B", due_date="2026-11-27")
    add_task(conn, title="C", due_date="2026-11-27")

    found = reminders.due_reminders(conn, cfg, datetime(2026, 11, 27, 9, 30, 0))
    report = next(r for r in found if r.key == f"morning:{session['id']}")
    assert "逾期 1 个" in report.body
    assert "今天到期 2 个" in report.body
    assert "A" in report.body


def test_morning_report_misses_later(conn, cfg):
    session = services.clock_in(conn, None, at=datetime(2026, 11, 27, 9, 0))
    found = reminders.due_reminders(conn, cfg, datetime(2026, 11, 27, 9, 30))
    assert f"morning:{session['id']}" not in keys(found)


# ---------------------------------------------------------------- 项目停滞
def _project(conn, name="Personal OS", days_ago=10, log_days_ago=None):
    project_id = dbm.insert(conn, "projects", {"name": name, "status": "active", "progress": 10})
    created = (FRI - timedelta(days=days_ago)).replace(microsecond=0).isoformat()
    conn.execute("UPDATE projects SET created_at = ? WHERE id = ?", (created, project_id))
    if log_days_ago is not None:
        dbm.insert(
            conn,
            "project_logs",
            {
                "project_id": project_id,
                "date": (FRI - timedelta(days=log_days_ago)).strftime("%Y-%m-%d"),
                "text": "推进了一点",
            },
        )
    return project_id


def test_stale_project_hits_after_three_days(conn, cfg):
    project_id = _project(conn, days_ago=10)
    found = reminders.due_reminders(conn, cfg, datetime(2026, 11, 27, 10, 30))
    assert f"stale:{project_id}:2026-11-27" in keys(found)


def test_stale_project_misses_before_ten_am(conn, cfg):
    _project(conn, days_ago=10)
    found = reminders.due_reminders(conn, cfg, datetime(2026, 11, 27, 9, 30))
    assert not [k for k in keys(found) if k.startswith("stale:")]


def test_stale_project_misses_with_recent_log(conn, cfg):
    _project(conn, days_ago=10, log_days_ago=1)
    found = reminders.due_reminders(conn, cfg, datetime(2026, 11, 27, 10, 30))
    assert not [k for k in keys(found) if k.startswith("stale:")]


def test_stale_project_misses_within_three_days(conn, cfg):
    _project(conn, days_ago=3)
    found = reminders.due_reminders(conn, cfg, datetime(2026, 11, 27, 10, 30))
    assert not [k for k in keys(found) if k.startswith("stale:")]


# ---------------------------------------------------------------- 去重
def test_dispatch_sends_once_and_records(conn, cfg):
    add_task(conn, title="交周报", due_date="2026-11-27", due_time="10:00")
    sent_calls: list[tuple[str, str]] = []

    def fake_notify(title, body):
        sent_calls.append((title, body))

    at = datetime(2026, 11, 27, 10, 0, 30)
    first = reminders.dispatch(conn, cfg, at, notify_func=fake_notify)
    second = reminders.dispatch(conn, cfg, at, notify_func=fake_notify)

    assert "task:1:2026-11-27T10:00:00" in keys(first)
    assert second == []                      # 同一轮提醒不会重复发
    assert len(sent_calls) == len(first)     # 每个都真的发了通知
    assert dbm.one(conn, "SELECT COUNT(*) AS n FROM reminders_sent")["n"] == len(first)


def test_dispatch_survives_notify_error(conn, cfg):
    add_task(conn, title="交周报", due_date="2026-11-27", due_time="10:00")

    def boom(title, body):
        raise RuntimeError("通知服务挂了")

    at = datetime(2026, 11, 27, 10, 0, 30)
    first = reminders.dispatch(conn, cfg, at, notify_func=boom)
    assert any(key.startswith("task:") for key in keys(first))
    # 通知失败也要记账，否则每分钟重试刷屏
    assert reminders.dispatch(conn, cfg, at, notify_func=boom) == []
