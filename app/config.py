"""配置读取：config.toml -> Config 对象。

查找顺序（前面的优先）：
1. 环境变量 PERSONAL_OS_CONFIG 指向的文件
2. 项目根目录下的 config.toml
3. 都没有时使用 DEFAULTS，保证首次启动不需要任何配置

config.toml 不进 Git（见 .gitignore），Gist Token 等密钥只存在本机。
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

# 项目根目录（app/ 的上一级）
ROOT = Path(__file__).resolve().parent.parent

# 内置默认值，与 docs/ARCHITECTURE.md 第 7 节保持一致
DEFAULTS: dict[str, Any] = {
    "timezone": "Asia/Shanghai",
    "work": {
        "clock_in_remind": "09:30",
        "max_hours": 9,
    },
    "screenshot": {
        "enabled": True,
        "interval_minutes": 30,
        "keep_days": 30,
        "quality": 70,
        "exclude_apps": [],
    },
    "gist": {
        "token": "",
        "gist_id": "",
    },
    "web": {
        "port": 8765,
    },
}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """递归合并：override 覆盖 base，未出现的键沿用默认值。"""
    result = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


class Section:
    """配置分区，支持属性访问（cfg.screenshot.keep_days）。"""

    def __init__(self, data: dict[str, Any]):
        self._data = dict(data)

    def __getattr__(self, name: str) -> Any:
        try:
            return self._data[name]
        except KeyError as exc:  # 拼错的键名要报错，避免静默拿到 None
            raise AttributeError(f"未知配置项: {name}") from exc

    def get(self, name: str, default: Any = None) -> Any:
        return self._data.get(name, default)

    def as_dict(self) -> dict[str, Any]:
        return dict(self._data)

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"Section({self._data!r})"


class Config:
    """全局配置。所有可变路径都从这里派生，便于测试时替换根目录。"""

    def __init__(self, data: dict[str, Any] | None = None, root: Path | str = ROOT,
                 source: Path | None = None):
        self.raw = _deep_merge(DEFAULTS, data or {})
        self.root = Path(root)
        self.source = source  # 实际读取的配置文件，None 表示全部用默认值
        self.work = Section(self.raw["work"])
        self.screenshot = Section(self.raw["screenshot"])
        self.gist = Section(self.raw["gist"])
        self.web = Section(self.raw["web"])

    # ---------- 顶层标量 ----------
    @property
    def timezone(self) -> str:
        return str(self.raw.get("timezone") or DEFAULTS["timezone"])

    @property
    def port(self) -> int:
        return int(self.raw["web"].get("port", DEFAULTS["web"]["port"]))

    @property
    def host(self) -> str:
        # 只监听本机回环，不暴露到局域网
        return "127.0.0.1"

    # ---------- 派生的路径 ----------
    @property
    def data_dir(self) -> Path:
        return Path(os.environ.get("PERSONAL_OS_DATA_DIR") or (self.root / "data"))

    @property
    def db_path(self) -> Path:
        return self.data_dir / "personal_os.db"

    @property
    def screenshots_dir(self) -> Path:
        return self.data_dir / "screenshots"

    def ensure_dirs(self) -> None:
        """确保数据目录存在（截图目录在 M2 第一次截图时再创建）。"""
        self.data_dir.mkdir(parents=True, exist_ok=True)


def load_config(path: Path | str | None = None) -> Config:
    """读取配置；文件不存在时全部使用默认值。"""
    if path is None:
        env_path = os.environ.get("PERSONAL_OS_CONFIG")
        candidate = Path(env_path) if env_path else (ROOT / "config.toml")
    else:
        candidate = Path(path)

    if candidate.is_file():
        with candidate.open("rb") as fh:
            data = tomllib.load(fh)
        return Config(data, source=candidate)

    return Config(source=None)
