from fastapi.testclient import TestClient

from app import health


def _client(tmp_env):
    from app.main import app
    return TestClient(app)


def test_collect_ok_when_cli_present(tmp_env, monkeypatch):
    monkeypatch.setattr("shutil.which",
                        lambda n: "/usr/bin/" + n if n == "claude" else None)
    h = health.collect()
    assert h["ok"] is True                      # 데이터 쓰기 가능 + CLI 1개
    assert h["server"]["data_writable"] is True
    assert h["providers"]["claude"]["installed"] is True
    assert h["providers"]["grok"]["installed"] is False
    assert "python" in h["server"] and "platform" in h["server"]


def test_collect_not_ok_without_any_cli(tmp_env, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda n: None)
    h = health.collect()
    assert h["ok"] is False                     # 설치된 CLI 없음


def test_collect_not_ok_when_data_unwritable(tmp_env, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda n: "/usr/bin/" + n)
    monkeypatch.setattr(health, "_data_writable", lambda: False)
    assert health.collect()["ok"] is False


def test_api_health_route(tmp_env):
    with _client(tmp_env) as client:
        r = client.get("/api/health")
        assert r.status_code == 200
        data = r.json()
        assert set(["ok", "version", "server", "setup", "providers", "tools"]) <= set(data)
        assert set(data["providers"]) == {
            "claude", "codex", "antigravity", "gemini", "grok", "openclaw", "hermes",
        }


def test_doctor_exit_code(tmp_env, monkeypatch, capsys):
    from app import __main__ as appmain
    monkeypatch.setattr("shutil.which", lambda n: "/usr/bin/" + n)
    assert appmain.doctor() == 0
    out = capsys.readouterr().out
    assert "진단" in out and "동작 가능" in out
    monkeypatch.setattr("shutil.which", lambda n: None)
    assert appmain.doctor() == 1


def test_main_dispatch(tmp_env, monkeypatch):
    from app import __main__ as appmain
    called = {}
    monkeypatch.setattr(appmain, "serve", lambda: called.setdefault("serve", True))
    assert appmain.main(["doctor"]) in (0, 1)   # doctor 경로
    assert "serve" not in called
    assert appmain.main(["--help"]) == 0
    assert "serve" not in called
    appmain.main([])                            # 인자 없으면 serve
    assert called.get("serve") is True
