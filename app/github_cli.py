"""`gh`(GitHub CLI)로 로그인된 계정의 리포·브랜치를 읽는다.

모든 함수는 runner(기본 _run)를 주입받을 수 있어 테스트에서 gh 호출을 대체할 수 있다.
"""
from __future__ import annotations

import json
import re
import subprocess

REPO_RE = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")


def _run(args, timeout=20):
    return subprocess.run(["gh", *args], capture_output=True, text=True,
                          timeout=timeout)


def status(runner=_run):
    """gh 설치·로그인 상태."""
    try:
        r = runner(["api", "user", "--jq", ".login"])
    except (FileNotFoundError, OSError):
        return {"installed": False, "loggedIn": False, "user": None}
    except subprocess.TimeoutExpired:
        return {"installed": True, "loggedIn": False, "user": None}
    if r.returncode == 0 and r.stdout.strip():
        return {"installed": True, "loggedIn": True, "user": r.stdout.strip()}
    return {"installed": True, "loggedIn": False, "user": None}


def list_repos(runner=_run, limit=200):
    r = runner(["repo", "list", "--json", "nameWithOwner,isPrivate,description",
                "--limit", str(limit)])
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or "gh repo list 실패")
    data = json.loads(r.stdout or "[]")
    return [{"repo": d["nameWithOwner"], "private": d.get("isPrivate", False),
             "desc": d.get("description") or ""} for d in data]


def list_branches(repo, runner=_run):
    if not REPO_RE.match(repo):
        raise ValueError("잘못된 리포 형식")
    r = runner(["api", "--paginate", f"repos/{repo}/branches", "--jq", ".[].name"])
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or "브랜치 조회 실패")
    branches = [b for b in r.stdout.splitlines() if b.strip()]
    d = runner(["repo", "view", repo, "--json", "defaultBranchRef",
                "--jq", ".defaultBranchRef.name"])
    default = d.stdout.strip() if d.returncode == 0 and d.stdout.strip() else (
        branches[0] if branches else None)
    return {"branches": branches, "default": default}
