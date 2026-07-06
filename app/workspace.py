"""작업 위치(workspace) 관리 — 로컬 폴더 또는 GitHub 리포를 등록해 두고
작업을 그 디렉터리(cwd)에서 실행한다. `data/workspaces.json`에 목록을 저장한다.

workspace = {"id": str, "name": str, "path": str(절대경로), "remote": url|None}
"""
from __future__ import annotations

import json
import re
import subprocess
import uuid
from pathlib import Path

from app import config


def _load():
    path = Path(config.WORKSPACES_PATH)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _save(items):
    path = Path(config.WORKSPACES_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")


def list_workspaces():
    return _load()


def get(ws_id):
    for w in _load():
        if w["id"] == ws_id:
            return w
    return None


def valid_path(path):
    """등록된 작업 위치의 경로만 유효한 workdir로 인정."""
    if not path:
        return False
    return any(w["path"] == path for w in _load())


def _slug(text):
    s = re.sub(r"[^\w.\-]", "-", text).strip("-")
    return s[:40] or "repo"


def looks_like_git_url(value):
    return "://" in value or value.startswith("git@")


def add(name, value, runner=None):
    """value가 git URL이면 클론, 아니면 로컬 폴더로 등록."""
    value = value.strip()
    if looks_like_git_url(value):
        return add_github(name, value, runner=runner)
    return add_local(name, value)


def _short_repo_name(repo):
    """owner/name 또는 URL에서 리포 이름만 뽑는다."""
    tail = repo.rstrip("/").split("/")[-1]
    return tail.removesuffix(".git")


def add_local(name, path):
    p = Path(path).expanduser()
    if not p.is_dir():
        raise ValueError(f"폴더를 찾을 수 없습니다: {path}")
    p = p.resolve()
    items = _load()
    if any(w["path"] == str(p) for w in items):
        raise ValueError("이미 등록된 폴더입니다")
    ws = {"id": uuid.uuid4().hex[:8], "name": name or p.name,
          "path": str(p), "remote": None}
    items.append(ws)
    _save(items)
    return ws


def add_github(name, repo, branch=None, runner=None):
    """repo(owner/name 또는 git URL)를 지정 브랜치로 클론해 등록한다."""
    runner = runner or _gh_clone
    slug = _slug(name or _short_repo_name(repo))
    dest = Path(config.WORKSPACES_DIR) / slug
    if dest.exists():
        raise ValueError(f"이미 존재하는 위치입니다: {dest.name}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    runner(repo, branch, dest)              # 실패 시 예외를 던짐
    items = _load()
    ws = {"id": uuid.uuid4().hex[:8], "name": name or slug,
          "path": str(dest.resolve()), "remote": repo, "branch": branch}
    items.append(ws)
    _save(items)
    return ws


def _gh_clone(repo, branch, dest):
    """gh repo clone — gh 인증을 그대로 써서 비공개 리포도 클론된다."""
    args = ["gh", "repo", "clone", repo, str(dest), "--", "--depth", "1"]
    if branch:
        args += ["--branch", branch]
    try:
        res = subprocess.run(args, capture_output=True, text=True,
                             timeout=config.GIT_CLONE_TIMEOUT_SEC)
    except FileNotFoundError:
        raise ValueError("gh(GitHub CLI)가 설치되어 있지 않습니다")
    except subprocess.TimeoutExpired:
        raise ValueError("클론 시간이 초과되었습니다")
    if res.returncode != 0:
        raise ValueError(f"클론 실패: {res.stderr.strip()[-300:]}")


def remove(ws_id):
    """목록에서 제거. 클론한 리포 디렉터리는 안전을 위해 지우지 않는다."""
    items = _load()
    kept = [w for w in items if w["id"] != ws_id]
    _save(kept)
    return len(kept) != len(items)
