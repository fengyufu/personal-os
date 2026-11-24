"""config.py：默认值、toml 读取、派生路径。"""

from __future__ import annotations

from pathlib import Path

from app.config import Config, load_config


def test_defaults_without_file(tmp_path):
    cfg = load_config(tmp_path / "config.toml")  # 文件不存在
    assert cfg.source is None
    assert cfg.timezone == "Asia/Shanghai"
    assert cfg.port == 8765
    assert cfg.screenshot.interval_minutes == 30
    assert cfg.screenshot.exclude_apps == []
    assert cfg.gist.token == ""


def test_toml_overrides_and_keeps_defaults(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        'timezone = "Asia/Tokyo"\n'
        "[screenshot]\n"
        "interval_minutes = 15\n"
        "[gist]\n"
        'token = "abc"\n',
        encoding="utf-8",
    )
    cfg = load_config(path)
    assert cfg.timezone == "Asia/Tokyo"
    assert cfg.screenshot.interval_minutes == 15
    # 没写的键仍然走默认值
    assert cfg.screenshot.keep_days == 30
    assert cfg.gist.token == "abc"


def test_paths_derive_from_root(tmp_path):
    cfg = Config(root=tmp_path)
    assert cfg.data_dir == tmp_path / "data"
    assert cfg.db_path == tmp_path / "data" / "personal_os.db"
    cfg.ensure_dirs()
    assert Path(cfg.data_dir).is_dir()


def test_unknown_section_attribute_raises():
    cfg = Config()
    try:
        _ = cfg.screenshot.typo_key
    except AttributeError as exc:
        assert "typo_key" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("拼错的配置键应该报 AttributeError")
