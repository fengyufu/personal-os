"""Personal OS 入口。

启动顺序：配置 → 数据库 → 调度器 →（后台线程）uvicorn → 浏览器 → 托盘。
托盘占主线程（pystray 要求），uvicorn 放后台线程；退出时先停调度器再停服务。

运行：
    python -m app.main               # 托盘 + 自动开浏览器
    python -m app.main --no-tray     # 只有服务（服务器 / 调试用）
    python -m app.main --no-browser  # 不自动开浏览器
"""

from __future__ import annotations

import argparse
import logging
import socket
import sys
import threading
import time
import webbrowser

import uvicorn

from .api import create_app
from .config import load_config
from .scheduler import Scheduler


def port_in_use(host: str, port: int) -> bool:
    """端口上已经有人监听 → 视为已经有一个实例在跑。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


def _banner(cfg) -> None:
    print("=" * 52)
    print("  Personal OS 正在启动")
    print(f"  面板地址：http://{cfg.host}:{cfg.port}/")
    print(f"  数据库  ：{cfg.db_path}")
    print(f"  配置文件：{cfg.source or '（未找到 config.toml，使用默认值）'}")
    print(f"  截图间隔：{cfg.screenshot.interval_minutes} 分钟"
          f"（{'开启' if cfg.screenshot.enabled else '关闭'}）")
    print("  按 Ctrl+C 退出")
    print("=" * 52)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="personal-os", description="个人工作系统")
    parser.add_argument("--no-tray", action="store_true", help="不显示托盘图标")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    args = parser.parse_args(argv)

    # 不配置的话 log.info 全都看不到，排查「为什么没截图 / 没提醒」会瞎
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    cfg = load_config()
    cfg.ensure_dirs()
    url = f"http://{cfg.host}:{cfg.port}/"

    # 单实例：端口被占说明已经在跑了，只把面板打开
    if port_in_use(cfg.host, cfg.port):
        print(f"已经有一个 Personal OS 在运行：{url}")
        if not args.no_browser:
            webbrowser.open(url)
        return 0

    _banner(cfg)

    app = create_app(cfg)
    scheduler = Scheduler(cfg, cfg.db_path)
    app.state.scheduler = scheduler
    scheduler.start()

    server = uvicorn.Server(uvicorn.Config(app, host=cfg.host, port=cfg.port, log_level="info"))
    thread = threading.Thread(target=server.run, name="uvicorn", daemon=True)
    thread.start()

    # 等服务真的起来再开浏览器，否则会看到「无法连接」
    deadline = time.time() + 15
    while not server.started and thread.is_alive() and time.time() < deadline:
        time.sleep(0.1)
    if not server.started:
        print("服务启动失败，请检查端口是否被占用。", file=sys.stderr)
        scheduler.shutdown()
        return 1

    print(f"面板已就绪：{url}")
    if not args.no_browser:
        webbrowser.open(url)

    def shutdown() -> None:
        scheduler.shutdown()
        server.should_exit = True
        thread.join(timeout=5)

    try:
        from . import tray

        if not args.no_tray and tray.available():
            tray.run(cfg, cfg.db_path, scheduler, on_quit=shutdown)
        else:
            if not args.no_tray:
                print("当前环境不支持托盘（需要 Windows + pystray），只跑后台服务。")
            while thread.is_alive():
                thread.join(timeout=0.5)
    except KeyboardInterrupt:
        print("\n正在退出…")
    finally:
        shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
