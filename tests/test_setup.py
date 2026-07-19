from fastapi.testclient import TestClient

from app import config, setup, settings


def _client(tmp_env):
    from app.main import app
    return TestClient(app)


def test_detect_all_installed():
    result = setup.detect(which=lambda name: f"/usr/local/bin/{name}")
    assert all(p["installed"] for p in result["providers"].values())
    assert result["providers"]["antigravity"]["path"] == "/usr/local/bin/agy"
    assert result["tools"]["codexbar"]["installed"] is True


def test_detect_none_installed():
    result = setup.detect(which=lambda name: None)
    assert not any(p["installed"] for p in result["providers"].values())
    # 안내 메타는 미설치여도 채워진다
    assert result["providers"]["claude"]["authHint"]
    assert result["providers"]["hermes"]["authCmd"] is None


def test_api_setup_status_shape(tmp_env):
    with _client(tmp_env) as client:
        r = client.get("/api/setup/status")
        assert r.status_code == 200
        data = r.json()
        assert set(data["providers"]) == {"claude", "antigravity", "grok", "hermes"}
        assert {"installed", "label", "authHint"} <= set(data["providers"]["claude"])


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
