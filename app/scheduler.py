"""调度器：每分钟 tick 一次，决定要不要截图、要不要发提醒。

只在 main.py 里启动；create_app() 绝不启动调度器，否则测试会真的去截图。
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from . import db as dbm, reminders, screenshot, services
from .config import Config

log = logging.getLogger(__name__)


class Scheduler:
    """把「定时」和「每轮做什么」分开：tick() 可以单独调用，方便测试。"""

    def __init__(
        self,
        cfg: Config,
        db_path: Path | str,
        notifier: Callable[..., Any] | None = None,
        capture_func: Callable[..., screenshot.CaptureResult] | None = None,
        now_fn: Callable[[], datetime] = datetime.now,
    ):
        self.cfg = cfg
        self.db_path = Path(db_path)
        self.notifier = notifier
        self.capture_func = capture_func or screenshot.capture
        self.now_fn = now_fn

        self.paused_until: datetime | None = None  # 内存态，重启即恢复
        self.last_tick_at: datetime | None = None
        self.last_error: str | None = None
        self._scheduler: BackgroundScheduler | None = None

    # ------------------------------------------------------------ 暂停
    def pause(self, minutes: int) -> datetime | None:
        """暂停截图 minutes 分钟；0 表示立即恢复。"""
        if minutes <= 0:
            self.paused_until = None
        else:
            self.paused_until = self.now_fn() + timedelta(minutes=minutes)
        log.info("截图暂停状态：%s", self.paused_until or "已恢复")
        return self.paused_until

    def is_paused(self, now: datetime | None = None) -> bool:
        moment = now or self.now_fn()
        return bool(self.paused_until and moment < self.paused_until)

    # ------------------------------------------------------------ 状态
    @property
    def running(self) -> bool:
        return bool(self._scheduler and self._scheduler.running)

    def status(self) -> dict[str, Any]:
        now = self.now_fn()
        last_at = None
        try:
            with dbm.connect(self.db_path) as conn:
                last = dbm.one(
                    conn, "SELECT taken_at FROM screenshots ORDER BY taken_at DESC LIMIT 1"
                )
                last_at = (last or {}).get("taken_at")
        except Exception:  # 数据库还没建好（刚启动）时不要让状态接口 500
            log.debug("读取调度器状态失败", exc_info=True)
        return {
            "running": self.running,
            "screenshot_paused_until": self.paused_until.isoformat() if self.is_paused(now) else None,
            "last_shot_at": last_at,
            "next_shot_in_minutes": self._next_shot_in(now, last_at),
            "last_tick_at": self.last_tick_at.isoformat() if self.last_tick_at else None,
            "last_error": self.last_error,
        }

    def _next_shot_in(self, now: datetime, last_at: str | None) -> int | None:
        """还有几分钟出下一张截图；不在上班 / 暂停 / 关闭时返回 None。"""
        if not self.cfg.screenshot.enabled or self.is_paused(now):
            return None
        with dbm.connect(self.db_path) as conn:
            if not services.get_open_session(conn):
                return None
        interval = int(self.cfg.screenshot.interval_minutes)
        if not last_at:
            return 0
        try:
            elapsed = (now - datetime.fromisoformat(last_at)).total_seconds() / 60
        except ValueError:
            return 0
        remaining = interval - elapsed
        return 0 if remaining <= 0 else int(math.ceil(remaining))

    # ------------------------------------------------------------ 每分钟
    def tick(self, now: datetime | None = None) -> dict[str, Any]:
        """跑一轮：截图 + 提醒。返回本轮摘要（测试和日志都用它）。"""
        moment = now or self.now_fn()
        self.last_tick_at = moment
        result: dict[str, Any] = {"at": moment.isoformat(), "capture": None, "reminders": []}

        try:
            with dbm.connect(self.db_path) as conn:
                result["capture"] = self._maybe_capture(conn, moment)
                try:
                    sent = reminders.dispatch(conn, self.cfg, moment, self.notifier)
                    result["reminders"] = [r.key for r in sent]
                    if sent:
                        log.info("发出提醒：%s", "，".join(r.title for r in sent))
                except Exception as exc:  # 提醒挂了不影响截图
                    self.last_error = f"提醒失败：{exc}"
                    log.exception("本轮提醒失败")
        except Exception as exc:
            self.last_error = f"tick 失败：{exc}"
            log.exception("tick 失败")
        return result

    def _maybe_capture(self, conn, now: datetime) -> dict[str, Any] | str | None:
        """到点就截图；返回 CaptureResult 的字典形式，或跳过原因字符串。"""
        if not self.cfg.screenshot.enabled:
            return "disabled"
        if not services.get_open_session(conn):
            return "no_session"

        last = dbm.one(conn, "SELECT taken_at FROM screenshots ORDER BY taken_at DESC LIMIT 1")
        interval = timedelta(minutes=int(self.cfg.screenshot.interval_minutes))
        if last:
            try:
                if now - datetime.fromisoformat(last["taken_at"]) < interval:
                    return "not_due"
            except ValueError:
                pass

        outcome = self.capture_func(
            conn, self.cfg, force=False, now=now, paused_until=self.paused_until
        )
        return outcome.as_dict()

    # ------------------------------------------------------------ 生命周期
    def start(self) -> None:
        if self.running:
            return
        self._scheduler = BackgroundScheduler(timezone=str(self.cfg.timezone))
        # 每分钟整点跑一轮；max_instances 防止上一轮没跑完又起一轮
        self._scheduler.add_job(
            self._safe_tick,
            CronTrigger(second=0),
            id="tick",
            max_instances=1,
            coalesce=True,
            replace_existing=True,
        )
        self._scheduler.add_job(
            self._safe_cleanup,
            CronTrigger(hour=3, minute=0),
            id="cleanup",
            max_instances=1,
            replace_existing=True,
        )
        self._scheduler.start()
        log.info("调度器已启动（每分钟 tick，每天 03:00 清理截图）")

    def shutdown(self, wait: bool = False) -> None:
        if self._scheduler:
            try:
                self._scheduler.shutdown(wait=wait)
            except Exception:
                log.debug("调度器关闭时出错", exc_info=True)
            self._scheduler = None

    def _safe_tick(self) -> None:
        try:
            self.tick()
        except Exception:  # tick 内部已捕获，这里再兜一层，绝不让 APScheduler 杀掉 job
            log.exception("调度任务异常")

    def _safe_cleanup(self) -> None:
        try:
            with dbm.connect(self.db_path) as conn:
                stats = screenshot.cleanup(conn, self.cfg, self.now_fn())
            log.info("截图清理完成：%s", stats)
        except Exception:
            log.exception("截图清理失败")
