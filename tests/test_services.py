"""services.py：打卡逻辑（api 和托盘共用）的行为。"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app import db as dbm, services


@pytest.fixture
def conn(db_path):
    dbm.init_db(db_path)
    with dbm.connect(db_path) as connection:
        yield connection


def test_clock_in_creates_open_session(conn):
    session = services.clock_in(conn, "1. 写代码\n2. 跑步")
    assert session["plan"].startswith("1. 写代码")
    assert session["clock_out"] is None
    assert session["date"] == datetime.now().strftime("%Y-%m-%d")
    assert services.get_open_session(conn)["id"] == session["id"]


def test_clock_in_twice_conflicts(conn):
    services.clock_in(conn, None)
    with pytest.raises(services.ConflictError):
        services.clock_in(conn, "再来一次")


def test_clock_out_closes_session(conn):
    start = datetime(2026, 11, 27, 9, 0)
    services.clock_in(conn, "写 M2", at=start)
    session = services.clock_out(conn, "做完了", at=start + timedelta(hours=8, minutes=30))
    assert session["summary"] == "做完了"
    assert session["worked_minutes"] == 510
    assert services.get_open_session(conn) is None


def test_clock_out_without_clock_in_conflicts(conn):
    with pytest.raises(services.ConflictError):
        services.clock_out(conn, "没上班")


def test_clock_out_across_midnight(conn):
    """昨晚 22:00 上班、今天 01:30 下班：不按日期过滤才能正常结束。"""
    start = datetime(2026, 11, 26, 22, 0)
    services.clock_in(conn, "通宵修 bug", at=start)
    session = services.clock_out(conn, "修完了", at=start + timedelta(hours=3, minutes=30))
    assert session["date"] == "2026-11-26"
    assert session["clock_out"] == "2026-11-27T01:30:00"
    assert session["worked_minutes"] == 210


def test_session_minutes_open_uses_given_now(conn):
    start = datetime(2026, 11, 27, 9, 0)
    session = services.clock_in(conn, None, at=start)
    assert services.session_minutes(session, until=start + timedelta(minutes=90)) == 90
