from fastapi.testclient import TestClient


def _client(tmp_env):
    from app.main import app
    return TestClient(app)


def test_index_renders(tmp_env):
    with _client(tmp_env) as client:
        r = client.get("/")
        assert r.status_code == 200
        assert "Agentic OS" in r.text


def test_create_job_queues_it(tmp_env):
    from app import config, db
    with _client(tmp_env) as client:
        r = client.post("/jobs", data={"prompt": "버그 수정해줘", "provider": "claude"},
                        follow_redirects=False)
        assert r.status_code == 303
    conn = db.get_conn(config.DB_PATH)
    jobs = db.list_jobs(conn)
    assert jobs[0]["prompt"] == "버그 수정해줘"
    assert jobs[0]["provider"] == "claude"


def test_create_job_auto_routes_simple_to_hermes(tmp_env):
    from app import config, db
    with _client(tmp_env) as client:
        client.post("/jobs", data={"prompt": "안녕", "provider": "auto"},
                    follow_redirects=False)
    conn = db.get_conn(config.DB_PATH)
    assert db.list_jobs(conn)[0]["provider"] == "hermes"


def test_create_job_auto_routes_complex_by_quota(tmp_env):
    from app import config, db
    with _client(tmp_env) as client:
        client.post("/jobs", data={"prompt": "이 코드 버그 수정 구현해줘", "provider": "auto"},
                    follow_redirects=False)
    conn = db.get_conn(config.DB_PATH)
    # 사용 기록이 없으면 잔여율 동률 → 우선순위 첫 번째인 claude
    assert db.list_jobs(conn)[0]["provider"] == "claude"


def test_create_job_with_session_continues(tmp_env):
    from app import config, db
    with _client(tmp_env) as client:
        client.post("/jobs", data={"prompt": "이어서 해줘", "provider": "claude",
                                   "session_id": "sess-42"},
                    follow_redirects=False)
    conn = db.get_conn(config.DB_PATH)
    job = db.list_jobs(conn)[0]
    assert job["session_id"] == "sess-42"
    assert job["provider"] == "claude"


def test_create_job_with_upload_appends_path(tmp_env):
    from app import config, db
    with _client(tmp_env) as client:
        client.post("/jobs", data={"prompt": "파일 분석해줘", "provider": "claude"},
                    files={"files": ("메모.txt", b"hello", "text/plain")},
                    follow_redirects=False)
    conn = db.get_conn(config.DB_PATH)
    job = db.list_jobs(conn)[0]
    assert "첨부 파일" in job["prompt"]
    uploads = list(config.UPLOAD_DIR.glob("*"))
    assert len(uploads) == 1
    assert uploads[0].read_bytes() == b"hello"


def test_note_view_and_containment(tmp_env):
    from app import config, memory
    path = memory.save_note("질문", "claude", "답변", session_id="sess-7")
    with _client(tmp_env) as client:
        r = client.get("/note", params={"path": str(path)})
        assert r.status_code == 200
        assert "sess-7" in r.text
        # 볼트 밖 경로는 404
        assert client.get("/note", params={"path": "/etc/hosts"}).status_code == 404


def test_create_job_with_valid_model(tmp_env):
    from app import config, db
    with _client(tmp_env) as client:
        client.post("/jobs", data={"prompt": "p", "provider": "claude",
                                   "model": "claude-opus-4-8"},
                    follow_redirects=False)
    conn = db.get_conn(config.DB_PATH)
    assert db.list_jobs(conn)[0]["model"] == "claude-opus-4-8"


def test_create_job_rejects_bogus_model(tmp_env):
    from app import config, db
    with _client(tmp_env) as client:
        client.post("/jobs", data={"prompt": "p", "provider": "claude",
                                   "model": "not-a-real-model"},
                    follow_redirects=False)
    conn = db.get_conn(config.DB_PATH)
    # 허용 목록에 없는 모델은 무시하고 기본값(None)으로
    assert db.list_jobs(conn)[0]["model"] is None


def test_recommend_endpoint(tmp_env):
    with _client(tmp_env) as client:
        simple = client.get("/api/recommend", params={"prompt": "안녕"}).json()
        assert simple["provider"] == "hermes"
        complex_ = client.get("/api/recommend",
                              params={"prompt": "이 코드 버그 구현 수정"}).json()
        assert complex_["provider"] in ("claude", "gemini", "grok")
        assert complex_["reason"]


def test_note_pin_and_rename_endpoints(tmp_env):
    from app import memory
    path = memory.save_note("원본", "claude", "본문")
    with _client(tmp_env) as client:
        r = client.post("/notes/pin", data={"path": str(path)})
        assert r.status_code == 200
        assert memory.note_flags(path)["pinned"] is True
        # rename
        r = client.post("/notes/rename", data={"path": str(path), "name": "바뀐이름"})
        assert r.status_code == 200
        assert (path.parent / "바뀐이름.md").exists()


def test_note_endpoints_reject_unmanaged(tmp_env):
    from app import config
    config.VAULT_PATH.mkdir(parents=True, exist_ok=True)
    outside = config.VAULT_PATH / "밖.md"
    outside.write_text("x", encoding="utf-8")
    with _client(tmp_env) as client:
        assert client.post("/notes/delete",
                           data={"path": str(outside)}).status_code == 404


def test_note_delete_endpoint(tmp_env):
    from app import memory
    path = memory.save_note("삭제할것", "claude", "본문")
    with _client(tmp_env) as client:
        r = client.post("/notes/delete", data={"path": str(path)})
        assert r.status_code == 200
    assert not path.exists()


def test_partial_usage_and_memory_render(tmp_env):
    with _client(tmp_env) as client:
        assert client.get("/partials/usage").status_code == 200
        assert client.get("/partials/memory").status_code == 200
        assert client.get("/partials/memory", params={"q": "x"}).status_code == 200


# --- 메모리 ↔ 작업큐 연동 ---

def test_deleting_note_removes_linked_job(tmp_env):
    from pathlib import Path
    from app import config, db, memory
    conn = db.get_conn(config.DB_PATH)
    job_id = db.create_job(conn, "질문", "claude")
    note = memory.save_note("질문", "claude", "답변")
    db.update_job(conn, job_id, note_path=str(note.resolve()))
    with _client(tmp_env) as client:
        # 노트 삭제 → 연결된 작업도 사라짐
        r = client.post("/notes/delete", data={"path": str(note)})
        assert r.status_code == 200
    assert db.get_job(conn, job_id) is None
    assert not note.exists()


def test_deleting_job_removes_linked_note(tmp_env):
    from app import config, db, memory
    conn = db.get_conn(config.DB_PATH)
    job_id = db.create_job(conn, "질문", "claude")
    note = memory.save_note("질문", "claude", "답변")
    db.update_job(conn, job_id, note_path=str(note.resolve()))
    with _client(tmp_env) as client:
        r = client.post(f"/jobs/{job_id}/delete")
        assert r.status_code == 200
        assert r.headers.get("HX-Trigger") == "refresh-memory"
    assert db.get_job(conn, job_id) is None
    assert not note.exists()


def test_renaming_note_relinks_job(tmp_env):
    from app import config, db, memory
    conn = db.get_conn(config.DB_PATH)
    note = memory.save_note("옛질문", "claude", "답변")
    job_id = db.create_job(conn, "옛질문", "claude")
    db.update_job(conn, job_id, note_path=str(note.resolve()))
    with _client(tmp_env) as client:
        client.post("/notes/rename", data={"path": str(note), "name": "새질문"})
    new = note.parent / "새질문.md"
    assert db.get_job(conn, job_id)["note_path"] == str(new.resolve())


# --- 작업 위치(workspace) ---

def test_workspace_add_local_and_use_in_job(tmp_env, tmp_path):
    from app import config, db
    d = tmp_path / "proj"
    d.mkdir()
    with _client(tmp_env) as client:
        r = client.post("/workspaces/add", data={"value": str(d), "name": "P"})
        assert r.status_code == 200
        assert "P" in r.text
        # 등록된 작업 위치로 작업 생성 → workdir 저장
        client.post("/jobs", data={"prompt": "p", "provider": "claude",
                                   "workdir": str(d.resolve())},
                    follow_redirects=False)
    conn = db.get_conn(config.DB_PATH)
    assert db.list_jobs(conn)[0]["workdir"] == str(d.resolve())


def test_job_rejects_unregistered_workdir(tmp_env, tmp_path):
    from app import config, db
    d = tmp_path / "sneaky"
    d.mkdir()
    with _client(tmp_env) as client:
        # 등록 안 된 경로는 무시(None)
        client.post("/jobs", data={"prompt": "p", "provider": "claude",
                                   "workdir": str(d)},
                    follow_redirects=False)
    conn = db.get_conn(config.DB_PATH)
    assert db.list_jobs(conn)[0]["workdir"] is None


def test_workspace_add_bad_path_returns_400(tmp_env):
    with _client(tmp_env) as client:
        r = client.post("/workspaces/add", data={"value": "/no/such/dir/xyz"})
        assert r.status_code == 400


def test_workspace_add_returns_selected_path_header(tmp_env, tmp_path):
    d = tmp_path / "proj"
    d.mkdir()
    with _client(tmp_env) as client:
        r = client.post("/workspaces/add", data={"value": str(d)})
        assert r.headers.get("X-Workspace-Path") == str(d.resolve())


# --- 폴더 탐색 API ---

def test_api_folders_lists_subdirs_within_root(tmp_env, tmp_path, monkeypatch):
    from app import config
    root = tmp_path / "home"
    (root / "Projects").mkdir(parents=True)
    (root / ".hidden").mkdir()
    (root / "file.txt").write_text("x")
    monkeypatch.setattr(config, "BROWSE_ROOT", root)
    with _client(tmp_env) as client:
        d = client.get("/api/folders").json()
        names = [x["name"] for x in d["dirs"]]
        assert "Projects" in names
        assert ".hidden" not in names   # 숨김 폴더 제외
        assert "file.txt" not in names  # 파일 제외
        assert d["canUp"] is False      # 루트에서는 상위 없음


def test_api_folders_confined_to_root(tmp_env, tmp_path, monkeypatch):
    from app import config
    root = tmp_path / "home"
    root.mkdir()
    monkeypatch.setattr(config, "BROWSE_ROOT", root)
    with _client(tmp_env) as client:
        # 루트 밖 경로를 요청해도 루트로 클램프
        d = client.get("/api/folders", params={"path": "/etc"}).json()
        assert d["path"] == str(root.resolve())


# --- GitHub API (gh 호출은 모킹) ---

def test_api_github_status_and_repos(tmp_env, monkeypatch):
    from app import github_cli
    monkeypatch.setattr(github_cli, "status",
                        lambda **k: {"installed": True, "loggedIn": True, "user": "me"})
    monkeypatch.setattr(github_cli, "list_repos",
                        lambda **k: [{"repo": "me/a", "private": True, "desc": ""}])
    with _client(tmp_env) as client:
        assert client.get("/api/github/status").json()["user"] == "me"
        repos = client.get("/api/github/repos").json()["repos"]
        assert repos[0]["repo"] == "me/a"


def test_api_github_branches(tmp_env, monkeypatch):
    from app import github_cli
    monkeypatch.setattr(github_cli, "list_branches",
                        lambda repo, **k: {"branches": ["master", "dev"], "default": "master"})
    with _client(tmp_env) as client:
        b = client.get("/api/github/branches", params={"repo": "me/a"}).json()
        assert b["default"] == "master"
        assert "dev" in b["branches"]


def test_add_github_endpoint_clones_and_selects(tmp_env, monkeypatch):
    from app import workspace
    captured = {}

    def fake_add_github(name, repo, branch=None):
        captured.update(repo=repo, branch=branch)
        return {"id": "x1", "name": "demo", "path": "/tmp/ws/demo",
                "remote": repo, "branch": branch}

    monkeypatch.setattr(workspace, "add_github", fake_add_github)
    with _client(tmp_env) as client:
        r = client.post("/workspaces/add-github",
                        data={"repo": "me/demo", "branch": "dev"})
        assert r.status_code == 200
        assert r.headers.get("X-Workspace-Path") == "/tmp/ws/demo"
    assert captured == {"repo": "me/demo", "branch": "dev"}


def test_add_github_endpoint_rejects_bad_repo(tmp_env):
    with _client(tmp_env) as client:
        assert client.post("/workspaces/add-github",
                           data={"repo": "not-a-repo"}).status_code == 400


def test_cancel_job(tmp_env):
    from app import config, db
    with _client(tmp_env) as client:
        client.post("/jobs", data={"prompt": "p", "provider": "claude"},
                    follow_redirects=False)
        conn = db.get_conn(config.DB_PATH)
        job_id = db.list_jobs(conn)[0]["id"]
        client.post(f"/jobs/{job_id}/cancel", follow_redirects=False)
        job = db.get_job(conn, job_id)
        assert job["status"] == "failed"
        assert job["error"] == "cancelled"


def test_partials_render(tmp_env):
    with _client(tmp_env) as client:
        assert client.get("/partials/jobs").status_code == 200
        assert client.get("/partials/usage").status_code == 200
        assert client.get("/partials/memory").status_code == 200


def test_job_detail_page(tmp_env):
    from app import config, db
    with _client(tmp_env) as client:
        conn = db.get_conn(config.DB_PATH)
        job_id = db.create_job(conn, "상세 페이지 테스트", "claude")
        r = client.get(f"/jobs/{job_id}")
        assert r.status_code == 200
        assert "상세 페이지 테스트" in r.text

def test_missing_job_returns_404(tmp_env):
    with _client(tmp_env) as client:
        assert client.get("/jobs/99999").status_code == 404
        assert client.post("/jobs/99999/cancel", follow_redirects=False).status_code == 404
        assert client.get("/jobs/99999/stream").status_code == 404


def test_create_job_rejects_unknown_provider(tmp_env):
    from app import config, db
    with _client(tmp_env) as client:
        r = client.post("/jobs", data={"prompt": "p", "provider": "claud"},
                        follow_redirects=False)
        assert r.status_code == 400
    conn = db.get_conn(config.DB_PATH)
    assert db.list_jobs(conn) == []


async def test_stream_does_not_treat_rate_limited_as_terminal(tmp_env):
    # Drive gen()'s generator directly (bypassing the HTTP/SSE transport) so
    # the test stays fast and deterministic.
    from app import config, db
    from app.main import stream_job

    conn = db.get_conn(config.DB_PATH)
    job_id = db.create_job(conn, "p", "claude")
    db.update_job(conn, job_id, status="rate_limited",
                  resume_at="2999-01-01T00:00:00+00:00", output="hello")

    response = await stream_job(job_id)
    agen = response.body_iterator
    first_chunk = await agen.__anext__()
    assert "event: status" not in first_chunk

    # flipping to done should now be the only way to end the stream
    db.update_job(conn, job_id, status="done", output="hello")
    got_status = False
    async for chunk in agen:
        if "event: status" in chunk:
            got_status = True
            break
    assert got_status


def test_cross_origin_post_is_blocked(tmp_env):
    from app import config, db
    with _client(tmp_env) as client:
        r = client.post("/jobs", data={"prompt": "p", "provider": "claude"},
                        headers={"origin": "https://evil.example"},
                        follow_redirects=False)
        assert r.status_code == 403
    conn = db.get_conn(config.DB_PATH)
    assert db.list_jobs(conn) == []


def test_same_origin_post_is_allowed(tmp_env):
    with _client(tmp_env) as client:
        r = client.post("/jobs", data={"prompt": "p", "provider": "claude"},
                        headers={"origin": "http://localhost:8899"},
                        follow_redirects=False)
        assert r.status_code == 303
