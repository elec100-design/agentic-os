import os
os.environ["AOS_DISABLE_WORKER"] = "1"

import pytest

from app import config


@pytest.fixture(autouse=True)
def _reset_lang():
    """UI 언어 contextvar가 테스트 간에 누수되지 않도록 매 테스트 전 기본값으로."""
    from app import i18n
    i18n.set_lang(i18n.DEFAULT_LANG)
    yield


@pytest.fixture
def tmp_env(tmp_path, monkeypatch):
    """테스트마다 DB/볼트를 임시 디렉토리로 격리한다."""
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(config, "USAGE_CACHE_PATH", tmp_path / "usage_cache.json")
    monkeypatch.setattr(config, "MODELS_CACHE_PATH", tmp_path / "models_cache.json")
    monkeypatch.setattr(config, "NOTE_STATE_PATH", tmp_path / "note_state.json")
    monkeypatch.setattr(config, "WORKSPACES_PATH", tmp_path / "workspaces.json")
    monkeypatch.setattr(config, "SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr(config, "WORKSPACES_DIR", tmp_path / "workspaces")
    monkeypatch.setattr(config, "VAULT_PATH", tmp_path / "vault")
    monkeypatch.setattr(config, "MEMORY_DIR", tmp_path / "vault" / "Agentic OS")
    return tmp_path


@pytest.fixture
def completed_setup(tmp_env):
    """셋업 완료 상태(전체 활성) — `/`가 /setup으로 리다이렉트하지 않는다."""
    from app import settings
    settings.save(["claude", "antigravity", "grok", "hermes"])
    return tmp_env
