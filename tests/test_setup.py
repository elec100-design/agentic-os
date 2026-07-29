from fastapi.testclient import TestClient

from app import config, setup, settings


def _client(tmp_env):
    from app.main import app
    return TestClient(app)


def test_detect_all_installed():
    result = setup.detect(
        which=lambda name: f"/usr/local/bin/{name}", probe_auth=False
    )
    assert all(p["installed"] for p in result["providers"].values())
    assert result["providers"]["antigravity"]["path"] == "/usr/local/bin/agy"
    assert result["tools"]["codexbar"]["installed"] is True
    assert "codex" in result["providers"]
    assert "gemini" in result["providers"]
    assert "openclaw" in result["providers"]


def test_detect_none_installed():
    result = setup.detect(which=lambda name: None, probe_auth=False)
    assert not any(p["installed"] for p in result["providers"].values())
    # 안내 메타는 미설치여도 채워진다
    assert result["providers"]["claude"]["authHint"]
    assert result["providers"]["hermes"]["authCmd"] is None
    assert result["providers"]["codex"]["authStatus"] == "needed"


def test_api_setup_status_shape(tmp_env):
    with _client(tmp_env) as client:
        r = client.get("/api/setup/status")
        assert r.status_code == 200
        data = r.json()
        assert set(data["providers"]) == {
            "claude", "codex", "antigravity", "gemini", "grok", "openclaw", "hermes",
        }
        assert {"installed", "label", "authHint", "authStatus"} <= set(
            data["providers"]["claude"]
        )
        assert data["providers"]["codex"]["label"] == "Codex"
        assert data["providers"]["openclaw"]["authCmd"]


def test_setup_page_renders(tmp_env):
    with _client(tmp_env) as client:
        r = client.get("/setup")
        assert r.status_code == 200
        assert "setup-steps" in r.text


def test_first_run_redirects_to_setup(tmp_env):
    with _client(tmp_env) as client:
        r = client.get("/", follow_redirects=False)
        assert r.status_code == 307
        assert r.headers["location"] == "/setup"


def test_complete_persists_and_stops_redirect(tmp_env):
    with _client(tmp_env) as client:
        r = client.post("/api/setup/complete",
                        data={"providers": ["claude", "hermes"]})
        assert r.status_code == 200
        assert r.json()["enabled"] == ["claude", "hermes"]
        assert settings.setup_completed() is True
        r2 = client.get("/", follow_redirects=False)
        assert r2.status_code == 200


def test_complete_rejects_empty(tmp_env):
    with _client(tmp_env) as client:
        r = client.post("/api/setup/complete", data={})
        assert r.status_code == 400
        r = client.post("/api/setup/complete", data={"providers": ["nope"]})
        assert r.status_code == 400
        assert settings.setup_completed() is False


def test_probe_codex_auth_needed(monkeypatch):
    def fake_probe(argv, timeout=None):
        if argv[:3] == ["codex", "login", "status"]:
            return 1, "Not logged in\n"
        return None
    monkeypatch.setattr(setup, "_run_probe", fake_probe)
    assert setup.probe_codex_auth()["authStatus"] == "needed"


def test_probe_codex_auth_ok(monkeypatch):
    def fake_probe(argv, timeout=None):
        return 0, "Logged in using ChatGPT\n"
    monkeypatch.setattr(setup, "_run_probe", fake_probe)
    st = setup.probe_codex_auth()
    assert st["authStatus"] == "ok"


def test_probe_openclaw_auth_empty_profiles(monkeypatch):
    def fake_probe(argv, timeout=None):
        return 0, '{"profiles": [], "agentId": "main"}'
    monkeypatch.setattr(setup, "_run_probe", fake_probe)
    assert setup.probe_openclaw_auth()["authStatus"] == "needed"


def test_probe_openclaw_auth_with_profiles(monkeypatch):
    def fake_probe(argv, timeout=None):
        return 0, '{"profiles": [{"id": "p1"}], "agentId": "main"}'
    monkeypatch.setattr(setup, "_run_probe", fake_probe)
    assert setup.probe_openclaw_auth()["authStatus"] == "ok"


def test_probe_claude_auth_json(monkeypatch):
    def fake_probe(argv, timeout=None):
        return 0, '{"loggedIn": true, "authMethod": "claude.ai"}'
    monkeypatch.setattr(setup, "_run_probe", fake_probe)
    st = setup.probe_claude_auth()
    assert st["authStatus"] == "ok"
    assert "claude.ai" in (st.get("authDetail") or "")


def test_probe_gemini_api_key_mode(tmp_path, monkeypatch):
    import pathlib
    home = tmp_path / "home"
    gem = home / ".gemini"
    gem.mkdir(parents=True)
    (gem / "settings.json").write_text(
        '{"security":{"auth":{"selectedType":"gemini-api-key"}}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(pathlib.Path, "home", staticmethod(lambda: home))
    st = setup.probe_gemini_auth()
    assert st["authStatus"] == "needed"
    assert "API key" in (st.get("authDetail") or "")


def test_probe_gemini_oauth_ok(tmp_path, monkeypatch):
    import pathlib
    home = tmp_path / "home"
    gem = home / ".gemini"
    gem.mkdir(parents=True)
    (gem / "settings.json").write_text(
        '{"security":{"auth":{"selectedType":"oauth-personal"}}}',
        encoding="utf-8",
    )
    (gem / "oauth_creds.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(pathlib.Path, "home", staticmethod(lambda: home))
    st = setup.probe_gemini_auth()
    assert st["authStatus"] == "ok"


def test_probe_gemini_vertex_ok(tmp_path, monkeypatch):
    import pathlib
    home = tmp_path / "home"
    gem = home / ".gemini"
    gem.mkdir(parents=True)
    (gem / "settings.json").write_text(
        '{"security":{"auth":{"selectedType":"vertex-ai"}}}',
        encoding="utf-8",
    )
    (gem / ".env").write_text(
        "GOOGLE_CLOUD_PROJECT=my-proj\nGOOGLE_CLOUD_LOCATION=global\n",
        encoding="utf-8",
    )
    adc = home / ".config" / "gcloud"
    adc.mkdir(parents=True)
    (adc / "application_default_credentials.json").write_text(
        '{"type":"authorized_user","refresh_token":"x","client_id":"y",'
        '"client_secret":"z"}',
        encoding="utf-8",
    )
    monkeypatch.setattr(pathlib.Path, "home", staticmethod(lambda: home))
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    st = setup.probe_gemini_auth()
    assert st["authStatus"] == "ok"
    assert "vertex-ai" in (st.get("authDetail") or "")
    assert "my-proj" in (st.get("authDetail") or "")


def test_probe_gemini_vertex_needs_project(tmp_path, monkeypatch):
    import pathlib
    home = tmp_path / "home"
    gem = home / ".gemini"
    gem.mkdir(parents=True)
    (gem / "settings.json").write_text(
        '{"security":{"auth":{"selectedType":"vertex-ai"}}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(pathlib.Path, "home", staticmethod(lambda: home))
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT_ID", raising=False)
    st = setup.probe_gemini_auth()
    assert st["authStatus"] == "needed"
    assert "GOOGLE_CLOUD_PROJECT" in (st.get("authDetail") or "")
