import pytest

from app import config, workspace


def test_add_local_and_list(tmp_env, tmp_path):
    d = tmp_path / "proj"
    d.mkdir()
    ws = workspace.add_local("내 프로젝트", str(d))
    assert ws["name"] == "내 프로젝트"
    assert ws["path"] == str(d.resolve())
    assert ws["remote"] is None
    assert workspace.list_workspaces()[0]["id"] == ws["id"]


def test_add_local_rejects_missing_dir(tmp_env):
    with pytest.raises(ValueError):
        workspace.add_local("x", "/definitely/not/here/xyz")


def test_add_local_default_name_from_folder(tmp_env, tmp_path):
    d = tmp_path / "myrepo"
    d.mkdir()
    ws = workspace.add_local("", str(d))
    assert ws["name"] == "myrepo"


def test_valid_path_only_registered(tmp_env, tmp_path):
    d = tmp_path / "proj"
    d.mkdir()
    ws = workspace.add_local("p", str(d))
    assert workspace.valid_path(ws["path"]) is True
    assert workspace.valid_path("/some/other/path") is False
    assert workspace.valid_path("") is False


def test_looks_like_git_url():
    assert workspace.looks_like_git_url("https://github.com/x/y.git")
    assert workspace.looks_like_git_url("git@github.com:x/y.git")
    assert not workspace.looks_like_git_url("/Users/me/proj")


def test_add_github_uses_runner_and_records_remote(tmp_env):
    calls = {}

    def fake_clone(url, dest):
        calls["url"] = url
        dest.mkdir(parents=True, exist_ok=True)

    # 이름을 비우면 URL에서 slug(demo)를 유도
    ws = workspace.add("", "https://github.com/acme/demo.git", runner=fake_clone)
    assert calls["url"] == "https://github.com/acme/demo.git"
    assert ws["remote"] == "https://github.com/acme/demo.git"
    assert ws["path"].endswith("demo")
    assert ws["name"] == "demo"


def test_add_github_propagates_clone_failure(tmp_env):
    def failing(url, dest):
        raise ValueError("클론 실패: auth")

    with pytest.raises(ValueError):
        workspace.add("x", "https://github.com/acme/x.git", runner=failing)


def test_remove(tmp_env, tmp_path):
    d = tmp_path / "proj"
    d.mkdir()
    ws = workspace.add_local("p", str(d))
    assert workspace.remove(ws["id"]) is True
    assert workspace.list_workspaces() == []
    assert workspace.remove("nope") is False
