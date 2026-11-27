"""自动截图：抓全屏、去重、落库、清理。

设计要点：
- 平台相关代码集中在 `_foreground_window()` 里；非 Windows 返回 None 表示「未知」，
  一律不跳过，保证 Linux/CI 上能跑测试。
- 截图只存本机 `data/screenshots/`，数据库存相对路径，绝不上传。
- `grab_screen()` 是唯一真正抓屏的地方，测试里 monkeypatch 掉它即可。
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from . import db as dbm, services
from .config import Config

log = logging.getLogger(__name__)

# 平均哈希的边长（32×32 = 1024 位）与判定「没变化」的汉明距离阈值
HASH_SIZE = 32
HASH_DISTANCE = 3

# 跳过原因 → 给前端看的中文说明
SKIP_TEXT = {
    "disabled": "截图功能已在配置里关闭",
    "no_session": "还没上班打卡，不截图",
    "paused": "截图已暂停",
    "locked": "锁屏或屏保中，不截图",
    "excluded": "当前窗口在隐私排除列表里，不截图",
    "unchanged": "画面和上一张几乎一样，跳过",
    "error": "截图失败，详见日志",
}


@dataclass
class CaptureResult:
    """一次截图的结果：成功带 record，跳过带 skipped。"""

    ok: bool
    record: dict[str, Any] | None = None
    skipped: str | None = None
    detail: str = ""

    @property
    def message(self) -> str:
        if self.ok:
            return "已截图"
        return SKIP_TEXT.get(self.skipped or "", self.detail or "已跳过")

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "record": self.record,
            "skipped": self.skipped,
            "message": self.message,
        }


# ---------------------------------------------------------------- 抓屏
def grab_screen():
    """抓所有显示器合并后的整屏，返回 PIL.Image。

    mss 只在真正截图时导入：没装 mss 的机器也能 import 本模块。
    """
    import mss  # 延迟导入
    from PIL import Image

    with mss.mss() as sct:
        shot = sct.grab(sct.monitors[0])  # [0] 是所有显示器合并的虚拟屏
        return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")


def _process_name(pid: int) -> str:
    """按 pid 取进程可执行文件名；失败返回空串。"""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(260)
        buf = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return Path(buf.value).name
        return ""
    finally:
        kernel32.CloseHandle(handle)


def _foreground_window() -> tuple[str, str] | None:
    """返回 (前台窗口标题, 进程名)；无法判断时返回 None（视为未知，不跳过）。

    Windows 上锁屏 / 屏保时拿不到前台窗口，或前台属于 LockApp.exe。
    """
    if sys.platform != "win32":
        return None
    import ctypes

    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        # 打不开输入桌面 = 当前不在用户桌面（锁屏 / 屏保 / 切换用户）
        if not user32.OpenInputDesktop(0, False, 0x0001):
            return ("", "LockApp.exe")
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return ("", "LockApp.exe")
        length = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        pid = ctypes.c_ulong(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return (buf.value, _process_name(pid.value) if pid.value else "")
    except Exception:  # 任何平台 API 异常都当作「未知」，宁可截图也不误跳过
        log.debug("前台窗口检测失败", exc_info=True)
        return None


# ---------------------------------------------------------------- 去重
def average_hash(image) -> str:
    """32×32 灰度的平均哈希，返回十六进制字符串。"""
    from PIL import Image as PILImage

    small = image.convert("L").resize((HASH_SIZE, HASH_SIZE), PILImage.BILINEAR)
    pixels = list(small.getdata())
    avg = sum(pixels) / len(pixels)
    bits = 0
    for value in pixels:
        bits = (bits << 1) | (1 if value >= avg else 0)
    return f"{bits:0{HASH_SIZE * HASH_SIZE // 4}x}"


def hamming(a: str, b: str) -> int:
    """两个同长哈希的汉明距离。"""
    return (int(a, 16) ^ int(b, 16)).bit_count()


def _last_shot(conn) -> dict[str, Any] | None:
    return dbm.one(conn, "SELECT * FROM screenshots ORDER BY taken_at DESC LIMIT 1")


def _previous_hash(conn, cfg: Config) -> str | None:
    """上一张截图的内容哈希；文件不在了就当没有上一张。"""
    last = _last_shot(conn)
    if not last:
        return None
    path = Path(cfg.screenshots_dir) / last["path"]
    if not path.is_file():
        return None
    try:
        from PIL import Image

        with Image.open(path) as img:
            return average_hash(img)
    except Exception:
        log.debug("读取上一张截图失败：%s", path, exc_info=True)
        return None


# ---------------------------------------------------------------- 截图
def capture(
    conn,
    cfg: Config,
    force: bool = False,
    now: datetime | None = None,
    paused_until: datetime | None = None,
) -> CaptureResult:
    """按配置截一张图。

    force=True 时忽略「暂停」和「画面无变化」两条，但锁屏和隐私窗口仍然跳过。
    """
    moment = now or datetime.now()

    if not cfg.screenshot.enabled:
        return CaptureResult(ok=False, skipped="disabled")

    session = services.get_open_session(conn)
    if not session:
        return CaptureResult(ok=False, skipped="no_session")

    if paused_until and moment < paused_until and not force:
        return CaptureResult(ok=False, skipped="paused")

    # 锁屏 / 隐私窗口：force 也不能越过
    fg = _foreground_window()
    if fg is not None:
        title, exe = fg
        if not title or (exe or "").lower() == "lockapp.exe":
            return CaptureResult(ok=False, skipped="locked")
        keywords = [k for k in (cfg.screenshot.exclude_apps or []) if k]
        if any(keyword in title for keyword in keywords):
            return CaptureResult(ok=False, skipped="excluded", detail=f"窗口标题：{title}")

    try:
        image = grab_screen()
    except Exception:
        log.exception("抓屏失败")
        return CaptureResult(ok=False, skipped="error")

    digest = average_hash(image)
    if not force:
        prev = _previous_hash(conn, cfg)
        if prev and hamming(digest, prev) <= HASH_DISTANCE:
            return CaptureResult(ok=False, skipped="unchanged")

    rel_dir = moment.strftime("%Y-%m-%d")
    filename = moment.strftime("%H%M%S")
    target_dir = Path(cfg.screenshots_dir) / rel_dir
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{filename}.jpg"
        # 同一秒重复截图时不要覆盖已有文件
        index = 1
        while target.exists():
            target = target_dir / f"{filename}-{index}.jpg"
            index += 1
        image.convert("RGB").save(target, "JPEG", quality=int(cfg.screenshot.quality))
    except Exception:
        log.exception("保存截图失败")
        return CaptureResult(ok=False, skipped="error")

    rel_path = f"{rel_dir}/{target.name}"  # 统一用 /，前端 /shots/<path> 直接可用
    shot_id = dbm.insert(
        conn,
        "screenshots",
        {
            "session_id": session["id"],
            "taken_at": moment.replace(microsecond=0).isoformat(),
            "path": rel_path,
        },
    )
    record = dbm.get(conn, "screenshots", shot_id) or {}
    log.info("已截图：%s", rel_path)
    return CaptureResult(ok=True, record=record)


# ---------------------------------------------------------------- 清理
def cleanup(conn, cfg: Config, now: datetime | None = None) -> dict[str, int]:
    """删除超过 keep_days 天的截图：文件、数据库记录、空目录。"""
    moment = now or datetime.now()
    keep_days = int(cfg.screenshot.keep_days)
    cutoff = (moment - timedelta(days=keep_days)).replace(microsecond=0).isoformat()

    rows = dbm.all_(conn, "SELECT * FROM screenshots WHERE taken_at < ?", (cutoff,))
    base = Path(cfg.screenshots_dir)
    removed_files = 0
    for row in rows:
        path = base / row["path"]
        try:
            if path.is_file():
                path.unlink()
                removed_files += 1
        except OSError:
            log.warning("删除截图文件失败：%s", path, exc_info=True)
        dbm.delete(conn, "screenshots", row["id"])

    removed_dirs = 0
    if base.is_dir():
        # 只清理形如 YYYY-MM-DD 的空目录，不动其他东西
        for sub in sorted(base.iterdir()):
            if sub.is_dir() and not any(sub.iterdir()):
                try:
                    sub.rmdir()
                    removed_dirs += 1
                except OSError:
                    log.debug("删除空目录失败：%s", sub, exc_info=True)

    return {
        "removed_records": len(rows),
        "removed_files": removed_files,
        "removed_dirs": removed_dirs,
    }
