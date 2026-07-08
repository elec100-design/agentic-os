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


def test_valid_path_only_registered(tmp_env, tmp_path, monkeypatch):
    from pathlib import Path
    from app import config
    root = tmp_path / "home"
    d = root / "proj"
    d.mkdir(parents=True)
    data_d = Path(f"/System/Volumes/Data{d}")
    monkeypatch.setattr(config, "BROWSE_ROOT", root)
    orig_variants = config._path_variants
    monkeypatch.setattr(
        config,
        "_path_variants",
        lambda path: [d.resolve(), data_d]
        if str(config.resolve_path(path)) in {str(d.resolve()), str(data_d)}
        else orig_variants(path),
    )
    ws = workspace.add_local("p", str(d))
    assert workspace.valid_path(ws["path"]) is True
    assert workspace.valid_path(str(data_d)) is True
    assert workspace.valid_path("/some/other/path") is False
    assert workspace.valid_path("") is False


def test_looks_like_git_url():
    assert workspace.looks_like_git_url("https://github.com/x/y.git")
    assert workspace.looks_like_git_url("git@github.com:x/y.git")
    assert not workspace.looks_like_git_url("/Users/me/proj")


def test_add_github_uses_runner_and_records_remote(tmp_env):
    calls = {}

    def fake_clone(repo, branch, dest):
        calls["repo"] = repo
        calls["branch"] = branch
        dest.mkdir(parents=True, exist_ok=True)

    # 이름을 비우면 URL에서 slug(demo)를 유도
    ws = workspace.add("", "https://github.com/acme/demo.git", runner=fake_clone)
    assert calls["repo"] == "https://github.com/acme/demo.git"
    assert calls["branch"] is None
    assert ws["remote"] == "https://github.com/acme/demo.git"
    assert ws["path"].endswith("demo")
    assert ws["name"] == "demo"


def test_add_github_with_repo_and_branch(tmp_env):
    calls = {}

    def fake_clone(repo, branch, dest):
        calls["repo"], calls["branch"] = repo, branch
        dest.mkdir(parents=True, exist_ok=True)

    ws = workspace.add_github("", "acme/demo", branch="dev", runner=fake_clone)
    assert calls == {"repo": "acme/demo", "branch": "dev"} or (
        calls["repo"] == "acme/demo" and calls["branch"] == "dev")
    assert ws["branch"] == "dev"
    assert ws["remote"] == "acme/demo"
    assert ws["name"] == "demo"


def test_add_github_propagates_clone_failure(tmp_env):
    def failing(repo, branch, dest):
        raise ValueError("클론 실패: auth")

    with pytest.raises(ValueError):
        workspace.add_github("x", "acme/x", branch="main", runner=failing)


def test_remove(tmp_env, tmp_path):
    d = tmp_path / "proj"
    d.mkdir()
    ws = workspace.add_local("p", str(d))
    assert workspace.remove(ws["id"]) is True
    assert workspace.list_workspaces() == []
    assert workspace.remove("nope") is False
