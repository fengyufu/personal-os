"""pytest 公共夹具：每个测试用独立的临时数据库，绝不碰真实 data/。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.api import create_app  # noqa: E402
from app.config import Config  # noqa: E402


@pytest.fixture
def config(tmp_path: Path) -> Config:
    """根目录指向 tmp_path，data/ 也就落在临时目录里。"""
    return Config({"web": {"port": 8765}}, root=tmp_path)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "data" / "test.db"


@pytest.fixture
def client(config: Config, db_path: Path) -> TestClient:
    app = create_app(config, db_path=db_path)
    with TestClient(app) as test_client:
        yield test_client
