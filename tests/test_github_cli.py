import json
import subprocess

import pytest

from app import github_cli


class FakeProc:
    def __init__(self, stdout="", returncode=0, stderr=""):
        self.stdout, self.returncode, self.stderr = stdout, returncode, stderr


def test_status_logged_in():
    runner = lambda args, **kw: FakeProc(stdout="elec100-design\n")
    st = github_cli.status(runner=runner)
    assert st == {"installed": True, "loggedIn": True, "user": "elec100-design"}


def test_status_not_logged_in():
    runner = lambda args, **kw: FakeProc(returncode=1, stderr="not logged in")
    st = github_cli.status(runner=runner)
    assert st["loggedIn"] is False


def test_status_not_installed():
    def runner(args, **kw):
        raise FileNotFoundError()
    assert github_cli.status(runner=runner)["installed"] is False


def test_list_repos_parses_json():
    payload = json.dumps([
        {"nameWithOwner": "me/a", "isPrivate": True, "description": "x"},
        {"nameWithOwner": "me/b", "isPrivate": False, "description": None},
    ])
    repos = github_cli.list_repos(runner=lambda args, **kw: FakeProc(stdout=payload))
    assert repos[0] == {"repo": "me/a", "private": True, "desc": "x"}
    assert repos[1]["desc"] == ""


def test_list_branches_and_default():
    def runner(args, **kw):
        if args[0] == "api":               # 브랜치 목록
            return FakeProc(stdout="master\ndev\n")
        return FakeProc(stdout="master\n")  # repo view: 기본 브랜치
    out = github_cli.list_branches("me/a", runner=runner)
    assert out["branches"] == ["master", "dev"]
    assert out["default"] == "master"


def test_list_branches_rejects_bad_repo():
    with pytest.raises(ValueError):
        github_cli.list_branches("not-a-repo", runner=lambda *a, **k: FakeProc())
