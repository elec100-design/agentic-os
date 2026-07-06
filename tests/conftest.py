import os
os.environ["AOS_DISABLE_WORKER"] = "1"

import pytest

from app import config


@pytest.fixture
def tmp_env(tmp_path, monkeypatch):
    """테스트마다 DB/볼트를 임시 디렉토리로 격리한다."""
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(config, "USAGE_CACHE_PATH", tmp_path / "usage_cache.json")
    monkeypatch.setattr(config, "NOTE_STATE_PATH", tmp_path / "note_state.json")
    monkeypatch.setattr(config, "WORKSPACES_PATH", tmp_path / "workspaces.json")
    monkeypatch.setattr(config, "WORKSPACES_DIR", tmp_path / "workspaces")
    monkeypatch.setattr(config, "VAULT_PATH", tmp_path / "vault")
    monkeypatch.setattr(config, "MEMORY_DIR", tmp_path / "vault" / "Agentic OS")
    return tmp_path
