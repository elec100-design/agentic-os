import json

import pytest

from app import config, settings


def test_defaults_when_file_absent(tmp_env):
    data = settings.load()
    assert data["enabled_providers"] == settings.ALL
    assert data["setup_completed"] is False
    assert settings.setup_completed() is False
    assert settings.enabled_providers() == settings.ALL


def test_save_load_roundtrip(tmp_env):
    saved = settings.save(["grok", "claude", "grok", "nope"])
    # 정식 순서 정렬 + 중복·무효 제거
    assert saved["enabled_providers"] == ["claude", "grok"]
    assert settings.enabled_providers() == ["claude", "grok"]
    assert settings.setup_completed() is True
    assert settings.is_enabled("claude")
    assert not settings.is_enabled("hermes")


def test_save_empty_raises(tmp_env):
    with pytest.raises(ValueError):
        settings.save([])
    with pytest.raises(ValueError):
        settings.save(["nope", "invalid"])


def test_corrupt_file_falls_back_to_defaults(tmp_env):
    config.SETTINGS_PATH.write_text("{broken json", encoding="utf-8")
    assert settings.load()["enabled_providers"] == settings.ALL
    assert settings.setup_completed() is False


def test_empty_stored_list_defensive_fallback(tmp_env):
    config.SETTINGS_PATH.write_text(
        json.dumps({"enabled_providers": [], "setup_completed": True}),
        encoding="utf-8")
    # 저장값이 비어도 전체로 폴백해 앱이 계속 동작한다
    assert settings.enabled_providers() == settings.ALL


def test_council_available(tmp_env, monkeypatch):
    settings.save(["claude", "grok"])
    assert settings.council_available() is True
    settings.save(["hermes"])
    assert settings.council_available() is False
    monkeypatch.setattr(config, "COUNCIL_MEMBERS", ["claude", "grok"])
    settings.save(["claude", "hermes"])
    assert settings.council_available() is False
