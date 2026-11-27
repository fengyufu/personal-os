"""scheduler.py：间隔到了才截图、提醒只发一次、暂停状态。"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app import db as dbm, screenshot, services
from app.config import Config
from app.scheduler import Scheduler

START = datetime(2026, 11, 27, 9, 0)


@pytest.fixture
def cfg(tmp_path):
    return Config(
        {"screenshot": {"enabled": True, "interval_minutes": 30}, "work": {"max_hours": 9}},
        root=tmp_path,
    )


class FakeCapture:
    """替身：不抓屏，只记一次时间并写一条记录。"""

    def __init__(self):
        self.calls: list[datetime] = []

    def __call__(self, conn, cfg, force=False, now=None, paused_until=None):
        self.calls.append(now)
        shot_id = dbm.insert(
            conn,
            "screenshots",
            {
                "taken_at": now.replace(microsecond=0).isoformat(),
                "path": f"2026-11-27/0{len(self.calls)}.jpg",
            },
        )
        return screenshot.CaptureResult(ok=True, record=dbm.get(conn, "screenshots", shot_id))


@pytest.fixture
def sched(cfg, db_path):
    dbm.init_db(db_path)
    fake = FakeCapture()
    scheduler = Scheduler(cfg, db_path, capture_func=fake, notifier=lambda *a: None,
                          now_fn=lambda: START)
    return scheduler, fake


def clock_in(db_path, at=START):
    with dbm.connect(db_path) as conn:
        return services.clock_in(conn, "测试", at=at)


# ---------------------------------------------------------------- 截图节奏
def test_tick_skips_without_session(sched, db_path):
    scheduler, fake = sched
    result = scheduler.tick(START)
    assert result["capture"] == "no_session"
    assert fake.calls == []


def test_tick_captures_only_when_interval_elapsed(sched, db_path):
    scheduler, fake = sched
    clock_in(db_path)

    assert scheduler.tick(START)["capture"]["ok"] is True
    assert scheduler.tick(START + timedelta(minutes=10))["capture"] == "not_due"
    assert scheduler.tick(START + timedelta(minutes=29))["capture"] == "not_due"
    assert scheduler.tick(START + timedelta(minutes=30))["capture"]["ok"] is True
    assert len(fake.calls) == 2


def test_tick_respects_disabled_screenshot(sched, db_path, cfg):
    _, fake = sched
    clock_in(db_path)
    disabled = Config({"screenshot": {"enabled": False}}, root=cfg.root)
    scheduler = Scheduler(disabled, db_path, capture_func=fake,
                          notifier=lambda *a: None, now_fn=lambda: START)
    assert scheduler.tick(START)["capture"] == "disabled"
    assert fake.calls == []


# ---------------------------------------------------------------- 提醒
def test_reminder_fires_once(sched, db_path):
    scheduler, _ = sched
    clock_in(db_path)  # 早报 + 9 点整不会有该上班提醒
    with dbm.connect(db_path) as conn:
        dbm.insert(
            conn,
            "tasks",
            {"title": "交周报", "due_date": "2026-11-27", "due_time": "09:00",
             "priority": 1, "repeat": "none", "done": 0},
        )

    first = scheduler.tick(START)
    second = scheduler.tick(START + timedelta(minutes=1))
    third = scheduler.tick(START + timedelta(minutes=2))

    assert any(key.startswith("task:") for key in first["reminders"])
    assert second["reminders"] == []
    assert third["reminders"] == []


# ---------------------------------------------------------------- 暂停
def test_pause_and_resume(sched):
    scheduler, _ = sched
    assert scheduler.is_paused(START) is False

    until = scheduler.pause(60)
    assert until == START + timedelta(minutes=60)
    assert scheduler.is_paused(START + timedelta(minutes=30)) is True
    assert scheduler.is_paused(START + timedelta(minutes=61)) is False

    scheduler.pause(0)
    assert scheduler.paused_until is None


def test_status_without_session(sched):
    scheduler, _ = sched
    status = scheduler.status()
    assert status["running"] is False
    assert status["next_shot_in_minutes"] is None
    assert status["screenshot_paused_until"] is None


def test_status_next_shot_counts_down(sched, db_path):
    scheduler, _ = sched
    clock_in(db_path)
    assert scheduler.status()["next_shot_in_minutes"] == 0  # 还没截过，立刻可截

    scheduler.tick(START)
    scheduler.now_fn = lambda: START + timedelta(minutes=12)
    assert scheduler.status()["next_shot_in_minutes"] == 18


def test_status_hides_countdown_when_paused(sched, db_path):
    scheduler, _ = sched
    clock_in(db_path)
    scheduler.pause(30)
    status = scheduler.status()
    assert status["screenshot_paused_until"] is not None
    assert status["next_shot_in_minutes"] is None


# ---------------------------------------------------------------- 生命周期
def test_start_and_shutdown(sched):
    scheduler, _ = sched
    assert scheduler.running is False
    scheduler.start()
    try:
        assert scheduler.running is True
    finally:
        scheduler.shutdown()
    assert scheduler.running is False
