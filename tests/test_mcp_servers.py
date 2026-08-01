"""app/mcp_servers.py — 워크스페이스별 MCP 서버 설정(claude 전용).

claude 는 --mcp-config <file> 로 그 실행 한 번에만 적용되는 MCP 서버 목록을
줄 수 있다(실측 확인, 모듈 상단 주석). codex/gemini 는 전역 설정 파일에만
쓸 수 있어 워크스페이스 스코프와 맞지 않아 지원하지 않는다.
"""
import json

import pytest

from fastapi.testclient import TestClient

from app import mcp_servers, workspace

pytestmark = pytest.mark.usefixtures("tmp_env")  # workspaces.json/mcp_servers.json/DATA_DIR 격리


def _register_workspace(tmp_path, name="proj"):
    d = tmp_path / name
    d.mkdir()
    return workspace.add_local(name, str(d))


# --- add: 검증 -------------------------------------------------------------

def test_add_requires_registered_workspace():
    with pytest.raises(ValueError, match="작업 위치"):
        mcp_servers.add("no-such-id", "s1", "npx")


def test_add_requires_name_and_command(tmp_path):
    ws = _register_workspace(tmp_path)
    with pytest.raises(ValueError, match="이름"):
        mcp_servers.add(ws["id"], "", "npx")
    with pytest.raises(ValueError, match="명령"):
        mcp_servers.add(ws["id"], "s1", "")


def test_add_rejects_duplicate_name_in_same_workspace(tmp_path):
    ws = _register_workspace(tmp_path)
    mcp_servers.add(ws["id"], "s1", "npx")
    with pytest.raises(ValueError, match="이미"):
        mcp_servers.add(ws["id"], "s1", "node")


def test_same_name_allowed_in_different_workspaces(tmp_path):
    ws1 = _register_workspace(tmp_path, "a")
    ws2 = _register_workspace(tmp_path, "b")
    mcp_servers.add(ws1["id"], "s1", "npx")
    mcp_servers.add(ws2["id"], "s1", "npx")  # 다른 워크스페이스면 이름이 겹쳐도 됨
    assert len(mcp_servers.list_all()) == 2


# --- add/list/remove: 기본 CRUD --------------------------------------------

def test_add_persists_and_is_listed(tmp_path):
    ws = _register_workspace(tmp_path)
    s = mcp_servers.add(ws["id"], "fs", "npx", ["-y", "server-fs"], {"KEY": "v"})
    assert s["id"]
    listed = mcp_servers.list_for_workspace(ws["id"])
    assert len(listed) == 1
    assert listed[0]["name"] == "fs"
    assert listed[0]["args"] == ["-y", "server-fs"]
    assert listed[0]["env"] == {"KEY": "v"}


def test_list_for_workspace_empty_for_unknown_or_none():
    assert mcp_servers.list_for_workspace("nope") == []
    assert mcp_servers.list_for_workspace(None) == []


def test_remove_deletes_and_returns_false_when_missing(tmp_path):
    ws = _register_workspace(tmp_path)
    s = mcp_servers.add(ws["id"], "fs", "npx")
    assert mcp_servers.remove(s["id"]) is True
    assert mcp_servers.list_for_workspace(ws["id"]) == []
    assert mcp_servers.remove(s["id"]) is False  # 이미 지워짐


# --- config_for_workdir / write_config_file --------------------------------

def test_config_for_workdir_builds_claude_mcp_config_shape(tmp_path):
    ws = _register_workspace(tmp_path)
    mcp_servers.add(ws["id"], "fs", "npx", ["-y", "server-fs"], {"KEY": "v"})
    data = mcp_servers.config_for_workdir(ws["path"])
    assert data == {
        "mcpServers": {"fs": {"command": "npx", "args": ["-y", "server-fs"], "env": {"KEY": "v"}}}
    }


def test_config_for_workdir_none_when_no_servers(tmp_path):
    ws = _register_workspace(tmp_path)
    assert mcp_servers.config_for_workdir(ws["path"]) is None


def test_config_for_workdir_none_for_unregistered_or_missing_workdir(tmp_path):
    assert mcp_servers.config_for_workdir(str(tmp_path / "unregistered")) is None
    assert mcp_servers.config_for_workdir(None) is None


def test_write_config_file_produces_valid_json_claude_can_read(tmp_path):
    ws = _register_workspace(tmp_path)
    mcp_servers.add(ws["id"], "fs", "npx", ["-y", "server-fs"], {})
    path = mcp_servers.write_config_file(ws["path"])
    assert path is not None and path.exists()
    data = json.loads(path.read_text())
    assert "fs" in data["mcpServers"]


def test_write_config_file_none_and_no_file_when_no_servers(tmp_path):
    ws = _register_workspace(tmp_path)
    assert mcp_servers.write_config_file(ws["path"]) is None


def test_write_config_file_reflects_removal_on_next_call(tmp_path):
    """무효화 로직 없이 매번 다시 계산한다 — 삭제하면 다음 호출부터 즉시 반영."""
    ws = _register_workspace(tmp_path)
    s = mcp_servers.add(ws["id"], "fs", "npx")
    path = mcp_servers.write_config_file(ws["path"])
    assert "fs" in json.loads(path.read_text())["mcpServers"]

    mcp_servers.remove(s["id"])
    assert mcp_servers.write_config_file(ws["path"]) is None


# --- 라우트: GET/POST /settings/mcp ----------------------------------------

def _client():
    from app.main import app
    return TestClient(app)


def test_settings_page_lists_workspaces_and_prompts_when_empty(tmp_env):
    with _client() as client:
        html = client.get("/settings/mcp").text
    assert "No workspaces registered yet" in html  # 기본 언어는 영어


def test_settings_page_lists_registered_servers(tmp_env, tmp_path):
    ws = _register_workspace(tmp_path)
    mcp_servers.add(ws["id"], "fs", "npx", ["-y", "server-fs"])
    with _client() as client:
        html = client.get("/settings/mcp").text
    assert "fs" in html
    assert "npx -y server-fs" in html


def test_settings_add_route_creates_server_and_redirects(tmp_env, tmp_path):
    ws = _register_workspace(tmp_path)
    with _client() as client:
        r = client.post("/settings/mcp/add", data={
            "workspace_id": ws["id"], "name": "fs", "command": "npx",
            "args": "-y server-fs", "env": "KEY=v1\nBAD_LINE\nKEY2=v2",
        }, follow_redirects=False)
    assert r.status_code == 303
    listed = mcp_servers.list_for_workspace(ws["id"])
    assert len(listed) == 1
    assert listed[0]["args"] == ["-y", "server-fs"]
    assert listed[0]["env"] == {"KEY": "v1", "KEY2": "v2"}  # 등호 없는 줄은 무시


def test_settings_add_route_shows_error_on_duplicate(tmp_env, tmp_path):
    ws = _register_workspace(tmp_path)
    mcp_servers.add(ws["id"], "fs", "npx")
    with _client() as client:
        r = client.post("/settings/mcp/add", data={
            "workspace_id": ws["id"], "name": "fs", "command": "node",
        })
    assert r.status_code == 400
    assert "이미" in r.text


def test_settings_remove_route_deletes_and_redirects(tmp_env, tmp_path):
    ws = _register_workspace(tmp_path)
    s = mcp_servers.add(ws["id"], "fs", "npx")
    with _client() as client:
        r = client.post(f"/settings/mcp/{s['id']}/remove", follow_redirects=False)
    assert r.status_code == 303
    assert mcp_servers.list_for_workspace(ws["id"]) == []


def test_sidebar_links_to_mcp_settings(tmp_env, completed_setup):
    with _client() as client:
        html = client.get("/").text
    assert 'href="/settings/mcp"' in html
