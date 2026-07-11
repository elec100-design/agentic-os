import importlib

from app import config as c


def _reload_clean(monkeypatch, tmp_path, **env):
    """env 를 지정해 config 를 다시 로드한다. 호출 후 반드시 복구할 것.
    AOS_ENV_FILE 을 없는 경로로 고정해 실제 aos.env 의 간섭을 막는다."""
    for k in ("AOS_VAULT_PATH", "AOS_NOTES_DIR"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("AOS_ENV_FILE", str(tmp_path / "no-such.env"))
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    importlib.reload(c)


def test_default_notes_dir_is_local(monkeypatch, tmp_path):
    """볼트 env 가 없으면 노트는 저장소 안 data/notes 에 저장(오비시디언 불필요)."""
    try:
        _reload_clean(monkeypatch, tmp_path)
        assert c.MEMORY_DIR == c.DATA_DIR / "notes"
        assert c.VAULT_PATH == c.MEMORY_DIR
    finally:
        monkeypatch.undo()
        importlib.reload(c)


def test_vault_env_uses_obsidian_subdir(monkeypatch, tmp_path):
    """AOS_VAULT_PATH 를 주면 노트는 볼트/Agentic OS 하위, 컨텍스트는 볼트 전체."""
    vault = tmp_path / "my vault"
    try:
        _reload_clean(monkeypatch, tmp_path, AOS_VAULT_PATH=str(vault))
        assert c.MEMORY_DIR == vault / "Agentic OS"
        assert c.VAULT_PATH == vault
    finally:
        monkeypatch.undo()
        importlib.reload(c)


def test_notes_dir_env_overrides(monkeypatch, tmp_path):
    notes = tmp_path / "notes-here"
    try:
        _reload_clean(monkeypatch, tmp_path, AOS_NOTES_DIR=str(notes))
        assert c.MEMORY_DIR == notes
    finally:
        monkeypatch.undo()
        importlib.reload(c)


def test_env_file_is_loaded(monkeypatch, tmp_path):
    """aos.env 파일의 값이 환경에 로드된다(재설치·수동실행에도 유지되게)."""
    env_file = tmp_path / "aos.env"
    vault = tmp_path / "vault-from-file"
    env_file.write_text(
        "# 주석\n"
        f'AOS_VAULT_PATH="{vault}"\n'
        "AOS_EXTRA_ORIGINS=host.tailnet.ts.net\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("AOS_VAULT_PATH", raising=False)
    monkeypatch.delenv("AOS_EXTRA_ORIGINS", raising=False)
    monkeypatch.setenv("AOS_ENV_FILE", str(env_file))
    try:
        importlib.reload(c)
        assert c.MEMORY_DIR == vault / "Agentic OS"
        import os
        assert os.environ.get("AOS_EXTRA_ORIGINS") == "host.tailnet.ts.net"
    finally:
        monkeypatch.undo()
        importlib.reload(c)


def test_real_env_wins_over_file(monkeypatch, tmp_path):
    """실제 환경변수가 aos.env 값보다 우선한다."""
    env_file = tmp_path / "aos.env"
    env_file.write_text("AOS_NOTES_DIR=/from/file\n", encoding="utf-8")
    monkeypatch.setenv("AOS_ENV_FILE", str(env_file))
    monkeypatch.setenv("AOS_NOTES_DIR", str(tmp_path / "from-env"))
    try:
        importlib.reload(c)
        assert c.MEMORY_DIR == tmp_path / "from-env"
    finally:
        monkeypatch.undo()
        importlib.reload(c)
