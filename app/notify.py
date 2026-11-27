"""系统通知：Windows 用 win11toast，其他平台退化为日志。

原则：任何异常都吞掉，绝不能让调度器因为通知失败而崩。
"""

from __future__ import annotations

import logging
import sys
from typing import Any

log = logging.getLogger(__name__)


def notify(title: str, body: str, url: str | None = None, app_id: str = "Personal OS") -> bool:
    """弹一条系统通知；返回是否真的发出去（False 表示已退化为日志）。"""
    if sys.platform == "win32":
        try:
            from win11toast import notify as win_notify

            kwargs: dict[str, Any] = {"app_id": app_id}
            if url:
                kwargs["on_click"] = url  # 点通知打开面板
            win_notify(title, body, **kwargs)
            return True
        except Exception:
            log.exception("Windows 通知发送失败，退化为日志")

    log.info("【提醒】%s —— %s", title, body.replace("\n", " / "))
    return False
