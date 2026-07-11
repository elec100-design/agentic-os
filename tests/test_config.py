import importlib

from app import config as c


def _reload_clean(monkeypatch, **env):
    """env 를 지정해 config 를 다시 로드한다. 호출 후 반드시 복구할 것."""
    for k in ("AOS_VAULT_PATH", "AOS_NOTES_DIR"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    importlib.reload(c)


def test_default_notes_dir_is_local(monkeypatch):
    """볼트 env 가 없으면 노트는 저장소 안 data/notes 에 저장(오비시디언 불필요)."""
    try:
        _reload_clean(monkeypatch)
        assert c.MEMORY_DIR == c.DATA_DIR / "notes"
        assert c.VAULT_PATH == c.MEMORY_DIR
    finally:
        monkeypatch.undo()
        importlib.reload(c)


def test_vault_env_uses_obsidian_subdir(monkeypatch, tmp_path):
    """AOS_VAULT_PATH 를 주면 노트는 볼트/Agentic OS 하위, 컨텍스트는 볼트 전체."""
    vault = tmp_path / "my vault"
    try:
        _reload_clean(monkeypatch, AOS_VAULT_PATH=str(vault))
        assert c.MEMORY_DIR == vault / "Agentic OS"
        assert c.VAULT_PATH == vault
    finally:
        monkeypatch.undo()
        importlib.reload(c)


def test_notes_dir_env_overrides(monkeypatch, tmp_path):
    notes = tmp_path / "notes-here"
    try:
        _reload_clean(monkeypatch, AOS_NOTES_DIR=str(notes))
        assert c.MEMORY_DIR == notes
    finally:
        monkeypatch.undo()
        importlib.reload(c)
