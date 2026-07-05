import os
os.environ["AOS_DISABLE_WORKER"] = "1"

import pytest

from app import config


@pytest.fixture
def tmp_env(tmp_path, monkeypatch):
    """테스트마다 DB/볼트를 임시 디렉토리로 격리한다."""
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "VAULT_PATH", tmp_path / "vault")
    monkeypatch.setattr(config, "MEMORY_DIR", tmp_path / "vault" / "Agentic OS")
    return tmp_path
