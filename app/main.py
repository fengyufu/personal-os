"""Personal OS 入口。

M1：启动数据库 + Web 服务（http://127.0.0.1:8765）。
M2 起会在这里追加：托盘图标、APScheduler 定时任务。

运行：python -m app.main
"""

from __future__ import annotations

import sys

import uvicorn

from .api import create_app
from .config import load_config


def main() -> None:
    cfg = load_config()
    cfg.ensure_dirs()
    app = create_app(cfg)

    url = f"http://{cfg.host}:{cfg.port}"
    print("=" * 52)
    print("  Personal OS 正在启动")
    print(f"  面板地址：{url}")
    print(f"  数据库  ：{cfg.db_path}")
    print(f"  配置文件：{cfg.source or '（未找到 config.toml，使用默认值）'}")
    print("  按 Ctrl+C 退出")
    print("=" * 52)

    try:
        uvicorn.run(app, host=cfg.host, port=cfg.port, log_level="info")
    except KeyboardInterrupt:  # 命令行 Ctrl+C 优雅退出
        sys.exit(0)


if __name__ == "__main__":
    main()
