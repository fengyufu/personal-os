"""任务/项目的纯逻辑测试：重复任务的到期日推算。"""

from __future__ import annotations

import pytest

from app.api import advance_due


@pytest.mark.parametrize(
    ("due_date", "repeat", "expected"),
    [
        ("2026-11-24", "none", "2026-11-24"),
        ("2026-11-24", "daily", "2026-11-25"),
        ("2026-11-24", "weekly", "2026-12-01"),
        # 周五 +1 个工作日 → 下周一
        ("2026-11-27", "weekdays", "2026-11-30"),
        # 周六也先跳到周一
        ("2026-11-28", "weekdays", "2026-11-30"),
        # 1 月 31 日的下一个月要收敛到 2 月 28 日
        ("2026-01-31", "monthly", "2026-02-28"),
        ("2026-11-24", "monthly", "2026-12-24"),
        ("2026-12-24", "monthly", "2027-01-24"),
    ],
)
def test_advance_due(due_date, repeat, expected):
    assert advance_due(due_date, repeat) == expected


def test_advance_due_without_due_date_uses_today():
    """没有到期日的重复任务，以下一次为基准从今天开始算。"""
    from datetime import date, timedelta

    assert advance_due(None, "daily") == (date.today() + timedelta(days=1)).isoformat()


def test_advance_due_unknown_repeat_is_noop():
    assert advance_due("2026-11-24", "yearly") == "2026-11-24"
