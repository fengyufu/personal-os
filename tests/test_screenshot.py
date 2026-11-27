"""screenshot.py：跳过规则、去重、落盘与清理。

真正的抓屏被 monkeypatch 掉，测试不会碰用户的屏幕。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from app import db as dbm, screenshot, services
from app.config import Config


@pytest.fixture
def conn(db_path):
    dbm.init_db(db_path)
    with dbm.connect(db_path) as connection:
        yield connection


@pytest.fixture
def cfg(tmp_path):
    return Config(
        {
            "screenshot": {
                "enabled": True,
                "interval_minutes": 30,
                "keep_days": 30,
                "quality": 70,
                "exclude_apps": ["微信"],
            }
        },
        root=tmp_path,
    )


def solid(color=(30, 30, 30)) -> Image.Image:
    return Image.new("RGB", (64, 48), color)


def striped() -> Image.Image:
    """和纯色差别很大的图，用来验证「画面变了要截」。"""
    image = Image.new("RGB", (64, 48), (0, 0, 0))
    draw = ImageDraw.Draw(image)
    for x in range(0, 64, 4):
        draw.rectangle((x, 0, x + 1, 48), fill=(255, 255, 255))
    return image


@pytest.fixture
def screen(monkeypatch):
    """假的抓屏 + 假的前台窗口，测试可改 screen["image"] / screen["fg"]。"""
    state = {"image": solid(), "fg": ("Chrome · 工作台", "chrome.exe")}
    monkeypatch.setattr(screenshot, "grab_screen", lambda: state["image"])
    monkeypatch.setattr(screenshot, "_foreground_window", lambda: state["fg"])
    return state


def clock_in(conn, at=None):
    return services.clock_in(conn, "测试", at=at or datetime(2026, 11, 27, 9, 0))


# ---------------------------------------------------------------- 跳过规则
def test_skip_without_session(conn, cfg, screen):
    result = screenshot.capture(conn, cfg)
    assert result.ok is False
    assert result.skipped == "no_session"
    assert not Path(cfg.screenshots_dir).exists()


def test_skip_when_disabled(conn, cfg, screen):
    clock_in(conn)
    disabled = Config({"screenshot": {"enabled": False}}, root=cfg.root)
    assert screenshot.capture(conn, disabled).skipped == "disabled"


def test_skip_while_paused_but_force_works(conn, cfg, screen):
    clock_in(conn)
    now = datetime(2026, 11, 27, 9, 30)
    paused = now + timedelta(minutes=30)
    assert screenshot.capture(conn, cfg, now=now, paused_until=paused).skipped == "paused"

    result = screenshot.capture(conn, cfg, force=True, now=now, paused_until=paused)
    assert result.ok is True


def test_skip_when_locked_even_from_tray(conn, cfg, screen):
    clock_in(conn)
    screen["fg"] = ("", "LockApp.exe")
    assert screenshot.capture(conn, cfg, force=True).skipped == "locked"


def test_skip_when_foreground_is_excluded(conn, cfg, screen):
    clock_in(conn)
    screen["fg"] = ("微信 - 聊天", "WeChat.exe")
    result = screenshot.capture(conn, cfg, force=True)
    assert result.skipped == "excluded"
    assert "微信" in result.detail


def test_skip_when_unchanged_but_force_wins(conn, cfg, screen):
    clock_in(conn)
    now = datetime(2026, 11, 27, 10, 0)
    assert screenshot.capture(conn, cfg, now=now).ok is True

    again = screenshot.capture(conn, cfg, now=now + timedelta(minutes=30))
    assert again.skipped == "unchanged"

    forced = screenshot.capture(conn, cfg, force=True, now=now + timedelta(minutes=60))
    assert forced.ok is True
    assert dbm.one(conn, "SELECT COUNT(*) AS n FROM screenshots")["n"] == 2


def test_capture_changed_screen(conn, cfg, screen):
    clock_in(conn)
    now = datetime(2026, 11, 27, 10, 0)
    assert screenshot.capture(conn, cfg, now=now).ok is True
    screen["image"] = striped()
    assert screenshot.capture(conn, cfg, now=now + timedelta(minutes=30)).ok is True


# ---------------------------------------------------------------- 落盘
def test_file_layout_and_record(conn, cfg, screen):
    session = clock_in(conn)
    now = datetime(2026, 11, 27, 14, 5, 9)
    result = screenshot.capture(conn, cfg, now=now)

    assert result.ok is True
    record = result.record
    assert record["path"] == "2026-11-27/140509.jpg"   # 相对路径，用 / 分隔
    assert record["session_id"] == session["id"]
    assert record["taken_at"] == "2026-11-27T14:05:09"

    saved = Path(cfg.screenshots_dir) / record["path"]
    assert saved.is_file()
    with Image.open(saved) as img:
        assert img.format == "JPEG"

    assert result.message == "已截图"
    assert result.as_dict()["ok"] is True


# ---------------------------------------------------------------- 清理
def test_cleanup_removes_old_files(conn, cfg, screen):
    clock_in(conn)
    old = datetime.now() - timedelta(days=cfg.screenshot.keep_days + 5)
    assert screenshot.capture(conn, cfg, now=old).ok is True
    old_path = Path(cfg.screenshots_dir) / dbm.one(conn, "SELECT path FROM screenshots")["path"]
    assert old_path.is_file()

    screen["image"] = striped()
    assert screenshot.capture(conn, cfg, force=True).ok is True

    stats = screenshot.cleanup(conn, cfg)
    assert stats["removed_records"] == 1
    assert stats["removed_files"] == 1
    assert not old_path.exists()
    # 新截图保留
    assert dbm.one(conn, "SELECT COUNT(*) AS n FROM screenshots")["n"] == 1


def test_cleanup_keeps_fresh(conn, cfg, screen):
    clock_in(conn)
    assert screenshot.capture(conn, cfg, now=datetime.now()).ok is True
    stats = screenshot.cleanup(conn, cfg)
    assert stats["removed_records"] == 0
    assert dbm.one(conn, "SELECT COUNT(*) AS n FROM screenshots")["n"] == 1


# ---------------------------------------------------------------- 哈希
def test_average_hash_distance():
    same = screenshot.average_hash(solid())
    assert screenshot.hamming(same, same) == 0
    assert screenshot.hamming(same, screenshot.average_hash(striped())) > screenshot.HASH_DISTANCE
