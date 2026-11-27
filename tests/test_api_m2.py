"""M2 新增接口：调度器状态、立即截图、暂停截图。"""

from __future__ import annotations

from PIL import Image

from app import screenshot
from app.scheduler import Scheduler


def fake_screen(monkeypatch, image=None, foreground=("Chrome", "chrome.exe")):
    monkeypatch.setattr(
        screenshot, "grab_screen", lambda: image or Image.new("RGB", (64, 48), (20, 20, 20))
    )
    monkeypatch.setattr(screenshot, "_foreground_window", lambda: foreground)


# ---------------------------------------------------------------- 状态
def test_scheduler_status_without_scheduler(client):
    data = client.get("/api/scheduler/status").json()
    assert data == {
        "running": False,
        "screenshot_paused_until": None,
        "last_shot_at": None,
        "next_shot_in_minutes": None,
        "last_tick_at": None,
        "last_error": None,
    }


def test_scheduler_status_with_injected_scheduler(client, config, db_path):
    client.app.state.scheduler = Scheduler(config, db_path)
    data = client.get("/api/scheduler/status").json()
    assert data["running"] is False  # 没 start，测试里绝不真的跑调度
    assert data["next_shot_in_minutes"] is None


# ---------------------------------------------------------------- 立即截图
def test_capture_now_saves_and_serves_file(client, monkeypatch):
    fake_screen(monkeypatch)
    client.post("/api/clock/in", json={"plan": "写 M2"})

    data = client.post("/api/screenshots/capture").json()
    assert data["ok"] is True
    assert data["skipped"] is None
    assert data["message"] == "已截图"
    assert data["record"]["path"].endswith(".jpg")

    shot = client.get(f"/shots/{data['record']['path']}")
    assert shot.status_code == 200
    assert shot.headers["content-type"].startswith("image/jpeg")


def test_capture_now_without_clock_in(client, monkeypatch):
    fake_screen(monkeypatch)
    data = client.post("/api/screenshots/capture").json()
    assert data["ok"] is False
    assert data["skipped"] == "no_session"
    assert "上班" in data["message"]


def test_capture_now_still_skips_locked_screen(client, monkeypatch):
    fake_screen(monkeypatch, foreground=("", "LockApp.exe"))
    client.post("/api/clock/in", json={"plan": None})
    data = client.post("/api/screenshots/capture").json()
    assert data["ok"] is False
    assert data["skipped"] == "locked"


# ---------------------------------------------------------------- 暂停
def test_pause_and_resume_screenshots(client, config, db_path):
    scheduler = Scheduler(config, db_path)
    client.app.state.scheduler = scheduler

    paused = client.post("/api/screenshots/pause", json={"minutes": 30}).json()
    assert paused["screenshot_paused_until"] is not None
    assert scheduler.is_paused() is True

    resumed = client.post("/api/screenshots/pause", json={"minutes": 0}).json()
    assert resumed["screenshot_paused_until"] is None
    assert scheduler.is_paused() is False


def test_pause_without_scheduler_conflicts(client):
    resp = client.post("/api/screenshots/pause", json={"minutes": 30})
    assert resp.status_code == 409
    assert "调度器" in resp.json()["detail"]


def test_pause_rejects_negative_minutes(client):
    assert client.post("/api/screenshots/pause", json={"minutes": -5}).status_code == 422
