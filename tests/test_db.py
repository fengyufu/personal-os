"""db.py：建表、迁移、读写助手。"""

from __future__ import annotations

import sqlite3

import pytest

from app import db as dbm


def test_init_db_creates_all_tables(db_path):
    dbm.init_db(db_path)
    with dbm.connect(db_path) as conn:
        names = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )}
    expected = {
        "work_sessions", "screenshots", "projects", "project_logs", "tasks",
        "reminders_sent", "ai_accounts", "ai_limits", "ai_usage",
    }
    assert expected <= names


def test_init_db_is_idempotent(db_path):
    dbm.init_db(db_path)
    dbm.init_db(db_path)  # 再跑一次不应报错
    with dbm.connect(db_path) as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
    assert version == dbm.SCHEMA_VERSION


def test_insert_fills_timestamps_and_update_stamps(db_path):
    dbm.init_db(db_path)
    with dbm.connect(db_path) as conn:
        row_id = dbm.insert(conn, "projects", {"name": "读书"})
        row = dbm.get(conn, "projects", row_id)
        assert row["created_at"] and row["updated_at"]
        assert row["created_at"] == row["updated_at"]

        assert dbm.update(conn, "projects", row_id, {"progress": 40}) is True
        assert dbm.get(conn, "projects", row_id)["progress"] == 40

        # 空 dict 不更新，避免无意义地刷新 updated_at
        assert dbm.update(conn, "projects", row_id, {}) is False
        assert dbm.delete(conn, "projects", row_id) is True
        assert dbm.get(conn, "projects", row_id) is None


def test_foreign_keys_cascade(db_path):
    """删项目要连带删掉它的日志（外键 ON DELETE CASCADE）。"""
    dbm.init_db(db_path)
    with dbm.connect(db_path) as conn:
        project_id = dbm.insert(conn, "projects", {"name": "写周报"})
        dbm.insert(conn, "project_logs", {"project_id": project_id, "date": "2026-11-24", "text": "写了一半"})
        assert len(dbm.all_(conn, "SELECT * FROM project_logs")) == 1
        dbm.delete(conn, "projects", project_id)
        assert dbm.all_(conn, "SELECT * FROM project_logs") == []


def test_reminder_key_is_unique(db_path):
    dbm.init_db(db_path)
    with dbm.connect(db_path) as conn:
        dbm.insert(conn, "reminders_sent", {"key": "task:1:2026-11-24T09:00", "sent_at": dbm.now_iso()})
        with pytest.raises(sqlite3.IntegrityError):
            dbm.insert(conn, "reminders_sent", {"key": "task:1:2026-11-24T09:00", "sent_at": dbm.now_iso()})
