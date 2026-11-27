"""托盘图标（pystray）：只在 Windows 上启用。

菜单：打开面板 / 上班·下班打卡 / 立即截图 / 暂停截图 / 恢复 / 退出。
图标每分钟刷新一次颜色：上班中绿色，未上班灰色。
"""

from __future__ import annotations

import logging
import sys
import threading
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from . import db as dbm, screenshot, services
from .config import Config

log = logging.getLogger(__name__)

REFRESH_SECONDS = 60


def available() -> bool:
    """当前环境能不能用托盘。"""
    if sys.platform != "win32":
        return False
    try:
        import pystray  # noqa: F401
    except Exception:
        return False
    return True


def make_icon_image(clocked_in: bool):
    """用 Pillow 画一个圆点：上班中绿色，否则灰色。"""
    from PIL import Image, ImageDraw

    size = 64
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    fill = (52, 199, 89, 255) if clocked_in else (142, 142, 147, 255)
    draw.ellipse((6, 6, size - 6, size - 6), fill=fill)
    if clocked_in:
        # 内圈小白点，缩小尺寸时也能一眼看出状态
        draw.ellipse((24, 24, size - 24, size - 24), fill=(255, 255, 255, 230))
    return image


def _ask(prompt: str, title: str) -> str | None:
    """弹一个简单的文本输入框；取消返回 None。"""
    try:
        import tkinter as tk
        from tkinter import simpledialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        try:
            return simpledialog.askstring(title, prompt, parent=root)
        finally:
            root.destroy()
    except Exception:  # 没有 GUI 环境时不要让托盘菜单崩掉
        log.exception("弹出输入框失败")
        return None


class TrayApp:
    def __init__(
        self,
        cfg: Config,
        db_path: Path | str,
        scheduler: Any = None,
        on_quit: Callable[[], None] | None = None,
    ):
        self.cfg = cfg
        self.db_path = Path(db_path)
        self.scheduler = scheduler
        self.on_quit = on_quit
        self.icon = None
        self._stop = threading.Event()

    # ------------------------------------------------------------ 便捷读取
    @property
    def url(self) -> str:
        return f"http://{self.cfg.host}:{self.cfg.port}/"

    def _clocked_in(self) -> bool:
        try:
            with dbm.connect(self.db_path) as conn:
                return services.get_open_session(conn) is not None
        except Exception:
            return False

    # ------------------------------------------------------------ 菜单动作
    def open_panel(self, *_args) -> None:
        webbrowser.open(self.url)

    def toggle_clock(self, *_args) -> None:
        if self._clocked_in():
            summary = _ask("今天做完了什么？（可留空）", "下班打卡")
            if summary is None:
                return
            try:
                with dbm.connect(self.db_path) as conn:
                    session = services.clock_out(conn, summary.strip() or None)
                log.info("托盘下班打卡：%s 分钟", session.get("worked_minutes"))
            except Exception:
                log.exception("托盘下班打卡失败")
        else:
            plan = _ask("今天三件事，一行一件（可留空）", "上班打卡")
            if plan is None:
                return
            try:
                with dbm.connect(self.db_path) as conn:
                    services.clock_in(conn, plan.strip() or None)
                log.info("托盘上班打卡完成")
            except Exception:
                log.exception("托盘上班打卡失败")
        self.refresh()

    def capture_now(self, *_args) -> None:
        try:
            with dbm.connect(self.db_path) as conn:
                result = screenshot.capture(
                    conn,
                    self.cfg,
                    force=True,
                    paused_until=getattr(self.scheduler, "paused_until", None),
                )
            log.info("托盘立即截图：%s", result.message)
        except Exception:
            log.exception("托盘截图失败")

    def toggle_pause(self, *_args) -> None:
        if not self.scheduler:
            return
        if self.scheduler.is_paused():
            self.scheduler.pause(0)
        else:
            self.scheduler.pause(60)
        self.refresh()

    def quit(self, *_args) -> None:
        self._stop.set()
        if self.icon:
            self.icon.stop()
        if self.on_quit:
            try:
                self.on_quit()
            except Exception:
                log.exception("退出回调失败")

    # ------------------------------------------------------------ 菜单
    def _menu(self):
        import pystray

        return pystray.Menu(
            pystray.MenuItem("打开面板", self.open_panel, default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                lambda _item: "下班打卡" if self._clocked_in() else "上班打卡",
                self.toggle_clock,
            ),
            pystray.MenuItem("立即截图", self.capture_now),
            pystray.MenuItem(
                lambda _item: (
                    "恢复截图" if self.scheduler and self.scheduler.is_paused() else "暂停截图 1 小时"
                ),
                self.toggle_pause,
                enabled=bool(self.scheduler),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出", self.quit),
        )

    def refresh(self, *_args) -> None:
        if not self.icon:
            return
        try:
            self.icon.icon = make_icon_image(self._clocked_in())
            self.icon.update_menu()
        except Exception:
            log.debug("刷新托盘状态失败", exc_info=True)

    def _refresh_loop(self) -> None:
        while not self._stop.wait(REFRESH_SECONDS):
            self.refresh()

    # ------------------------------------------------------------ 运行
    def run(self) -> None:
        import pystray

        self.icon = pystray.Icon(
            "personal-os",
            make_icon_image(self._clocked_in()),
            f"Personal OS · {self.url}",
            self._menu(),
        )
        threading.Thread(target=self._refresh_loop, daemon=True).start()
        log.info("托盘已启动")
        self.icon.run()  # 阻塞在主线程


def run(cfg: Config, db_path: Path | str, scheduler: Any = None,
        on_quit: Callable[[], None] | None = None) -> None:
    """启动托盘（阻塞）。调用前请确认 available()。"""
    TrayApp(cfg, db_path, scheduler, on_quit).run()
