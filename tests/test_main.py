from pathlib import Path

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


def test_create_job_with_session_and_workdir(tmp_env, tmp_path):
    """노트 이어가기: session_id + 등록된 workdir가 job에 함께 저장된다."""
    from app import config, db, workspace
    d = tmp_path / "proj"
    d.mkdir()
    workspace.add_local("proj", str(d))
    with _client(tmp_env) as client:
        client.post("/jobs", data={
            "prompt": "이어서 해줘",
            "provider": "claude",
            "session_id": "sess-99",
            "workdir": str(d.resolve()),
        }, follow_redirects=False)
    job = db.list_jobs(db.get_conn(config.DB_PATH))[0]
    assert job["session_id"] == "sess-99"
    assert job["workdir"] == str(d.resolve())


def test_create_job_council_queues_it(tmp_env):
    from app import config, db
    with _client(tmp_env) as client:
        r = client.post("/jobs", data={
            "prompt": "아주 어려운 문제",
            "provider": "council",
            # 협의 모드는 세션·모델·작업 위치를 지원하지 않으므로 무시된다
            "model": "opus",
            "session_id": "sess-1",
        }, follow_redirects=False)
        assert r.status_code == 303
    job = db.list_jobs(db.get_conn(config.DB_PATH))[0]
    assert job["provider"] == "council"
    assert job["model"] is None
    assert job["session_id"] is None
    assert job["workdir"] is None


def test_create_job_council_requires_min_members(tmp_env, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "COUNCIL_MEMBERS", ["claude"])
    with _client(tmp_env) as client:
        r = client.post("/jobs", data={"prompt": "문제", "provider": "council"},
                        follow_redirects=False)
        assert r.status_code == 400
        assert "부족" in r.json()["detail"]


def test_create_job_rejects_oversized_upload(tmp_env, monkeypatch):
    """업로드가 한도를 넘으면 413으로 거부한다."""
    from app import config
    monkeypatch.setattr(config, "MAX_UPLOAD_MB", 1)
    big = b"x" * (1024 * 1024 + 1)   # 1MB + 1바이트
    with _client(tmp_env) as client:
        r = client.post("/jobs", data={"prompt": "분석", "provider": "claude"},
                        files={"files": ("big.bin", big, "application/octet-stream")},
                        follow_redirects=False)
    assert r.status_code == 413


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


def test_note_view_resume_form_includes_workdir(tmp_env):
    from app import memory
    wd = "/tmp/example-project"
    path = memory.save_note(
        "질문", "claude", "답변", session_id="sess-abc-123", workdir=wd)
    with _client(tmp_env) as client:
        r = client.get("/note", params={"path": str(path)})
        assert r.status_code == 200
        assert 'name="session_id"' in r.text
        assert 'value="sess-abc-123"' in r.text
        assert 'name="workdir"' in r.text
        assert f'value="{wd}"' in r.text
        assert "작업 위치" in r.text


def test_note_resume_form_has_agent_model_and_file(tmp_env):
    """재개 폼에 에이전트·모델 선택과 파일 첨부 UI가 렌더된다."""
    from app import memory
    path = memory.save_note("질문", "claude", "답변", session_id="sess-1")
    with _client(tmp_env) as client:
        r = client.get("/note", params={"path": str(path)})
    assert 'name="resume_provider"' in r.text
    assert 'id="resume-agent"' in r.text
    assert 'id="resume-model"' in r.text
    assert 'type="file"' in r.text


def test_resume_same_agent_keeps_session(tmp_env):
    """같은 에이전트를 고르면 진짜 세션 재개: session_id·스레드 노트 유지,
    노트 본문은 컨텍스트로 붙이지 않는다."""
    from app import config, db, memory
    path = memory.save_note("원래 질문", "claude", "원래 답변", session_id="sess-77")
    with _client(tmp_env) as client:
        client.post("/jobs", data={
            "prompt": "이어서 해줘",
            "provider": "claude",
            "session_id": "sess-77",
            "resume_provider": "claude",
            "origin_note": str(path),
        }, follow_redirects=False)
    job = db.list_jobs(db.get_conn(config.DB_PATH))[0]
    assert job["session_id"] == "sess-77"
    assert job["note_path"] == str(path.resolve())
    assert "원래 질문" not in job["prompt"]


def test_resume_switching_agent_drops_session_and_attaches_note(tmp_env):
    """다른 에이전트를 고르면 그 세션 id로는 재개할 수 없으므로 session_id를
    버리고, 노트를 컨텍스트로 붙여 같은 위치에서 이어간다."""
    from app import config, db, memory
    path = memory.save_note("원래 질문", "grok", "원래 답변", session_id="latest")
    with _client(tmp_env) as client:
        client.post("/jobs", data={
            "prompt": "이어서 해줘",
            "provider": "claude",
            "session_id": "latest",
            "resume_provider": "grok",
            "origin_note": str(path),
        }, follow_redirects=False)
    job = db.list_jobs(db.get_conn(config.DB_PATH))[0]
    assert job["provider"] == "claude"
    assert job["session_id"] is None
    assert job["note_path"] is None
    assert "원래 질문" in job["prompt"]


def test_create_job_with_valid_model(tmp_env):
    from app import config, db
    # 폴백 목록의 패밀리 별칭(opus) — CLI가 항상 최신 full id로 해석
    with _client(tmp_env) as client:
        client.post("/jobs", data={"prompt": "p", "provider": "claude",
                                   "model": "opus"},
                    follow_redirects=False)
    conn = db.get_conn(config.DB_PATH)
    assert db.list_jobs(conn)[0]["model"] == "opus"


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
        assert complex_["provider"] in ("claude", "antigravity", "grok")
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


def test_create_job_with_origin_note_links_thread(tmp_env):
    from app import config, db, memory
    note = memory.save_note("원질문", "claude", "답변", session_id="sess-1")
    with _client(tmp_env) as client:
        client.post("/jobs", data={"prompt": "이어서", "provider": "claude",
                                   "session_id": "sess-1",
                                   "origin_note": str(note)},
                    follow_redirects=False)
    conn = db.get_conn(config.DB_PATH)
    assert db.list_jobs(conn)[0]["note_path"] == str(note.resolve())


def test_create_job_ignores_origin_note_without_session(tmp_env):
    from app import config, db, memory
    note = memory.save_note("원질문", "claude", "답변")
    with _client(tmp_env) as client:
        client.post("/jobs", data={"prompt": "p", "provider": "claude",
                                   "origin_note": str(note)},
                    follow_redirects=False)
    conn = db.get_conn(config.DB_PATH)
    assert db.list_jobs(conn)[0]["note_path"] is None


def test_create_job_ignores_unmanaged_origin_note(tmp_env):
    from app import config, db
    with _client(tmp_env) as client:
        client.post("/jobs", data={"prompt": "p", "provider": "claude",
                                   "session_id": "sess-1",
                                   "origin_note": "/etc/hosts"},
                    follow_redirects=False)
    conn = db.get_conn(config.DB_PATH)
    assert db.list_jobs(conn)[0]["note_path"] is None


def test_deleting_one_thread_job_keeps_shared_note(tmp_env):
    from app import config, db, memory
    conn = db.get_conn(config.DB_PATH)
    note = memory.save_note("질문", "claude", "답변")
    a = db.create_job(conn, "질문", "claude")
    b = db.create_job(conn, "이어서", "claude")
    db.update_job(conn, a, note_path=str(note.resolve()))
    db.update_job(conn, b, note_path=str(note.resolve()))
    with _client(tmp_env) as client:
        client.post(f"/jobs/{b}/delete")
    assert note.exists()                        # 다른 잡이 공유 → 노트 보존
    assert db.get_job(conn, b) is None
    with _client(tmp_env) as client:
        client.post(f"/jobs/{a}/delete")
    assert not note.exists()                    # 마지막 잡 삭제 → 노트도 삭제


def test_note_group_endpoint_marks_manual(tmp_env):
    from app import memory
    note = memory.save_note("질문", "claude", "답변")
    with _client(tmp_env) as client:
        client.post("/notes/group", data={"path": str(note), "group": "연구"})
    flags = memory.note_flags(note)
    assert flags["group"] == "연구"
    assert flags["auto_group"] is False


# --- 작업 위치(workspace) ---

def test_workspace_add_local_and_use_in_job(tmp_env, tmp_path, monkeypatch):
    from app import config, db
    monkeypatch.setattr(config, "BROWSE_ROOT", tmp_path)
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


def test_workspace_add_returns_selected_path_header(tmp_env, tmp_path, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "BROWSE_ROOT", tmp_path)
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


def test_api_folders_icloud_shortcut_and_browse(tmp_env, tmp_path, monkeypatch):
    from app import config
    root = tmp_path / "home"
    icloud = root / "Library/Mobile Documents/com~apple~CloudDocs"
    (icloud / "LLM WIKI").mkdir(parents=True)
    monkeypatch.setattr(config, "BROWSE_ROOT", root)
    monkeypatch.setattr(config, "ICLOUD_DRIVE", icloud)
    with _client(tmp_env) as client:
        d = client.get("/api/folders").json()
        kinds = {s["kind"] for s in d.get("shortcuts", [])}
        names = {s["name"] for s in d.get("shortcuts", [])}
        assert "icloud" in kinds
        assert "홈" in names
        icloud_paths = [s["path"] for s in d["shortcuts"] if s["kind"] == "icloud"]
        assert str(icloud.resolve()) in icloud_paths
        inside = client.get(
            "/api/folders",
            params={"path": str(icloud / "LLM WIKI")},
        ).json()
        assert inside["path"] == str((icloud / "LLM WIKI").resolve())
        labeled = client.get(
            "/api/folders",
            params={"path": str(icloud.parent)},
        ).json()
        cloud = next(x for x in labeled["dirs"] if x["name"] == "com~apple~CloudDocs")
        assert cloud["label"] == "iCloud Drive"
        # iCloud 루트에서도 바로가기(홈·iCloud 등)가 보인다
        at_icloud = client.get("/api/folders", params={"path": str(icloud)}).json()
        assert at_icloud.get("shortcuts")
        assert at_icloud["canUp"] is False


def test_api_folders_accepts_data_volume_icloud_path(tmp_env, tmp_path, monkeypatch):
    from app import config
    root = tmp_path / "home"
    icloud = root / "Library/Mobile Documents/com~apple~CloudDocs"
    (icloud / "Projects").mkdir(parents=True)
    data_icloud = Path(f"/System/Volumes/Data{icloud}")
    monkeypatch.setattr(config, "BROWSE_ROOT", root)
    monkeypatch.setattr(config, "ICLOUD_DRIVE", icloud)
    orig_variants = config._path_variants
    monkeypatch.setattr(
        config,
        "_path_variants",
        lambda path: [icloud.resolve(), data_icloud]
        if str(config.resolve_path(path)) in {str(icloud.resolve()), str(data_icloud)}
        else orig_variants(path),
    )
    with _client(tmp_env) as client:
        d = client.get(
            "/api/folders",
            params={"path": str(data_icloud)},
        ).json()
        assert d["path"] == str(icloud.resolve())
        assert d["canUp"] is False
        assert any(x["name"] == "Projects" for x in d["dirs"])


def test_workspace_add_icloud_path(tmp_env, tmp_path, monkeypatch):
    from app import config, workspace
    root = tmp_path / "home"
    icloud = root / "Library/Mobile Documents/com~apple~CloudDocs"
    project = icloud / "LLM WIKI" / "Agentic-OS"
    project.mkdir(parents=True)
    monkeypatch.setattr(config, "BROWSE_ROOT", root)
    monkeypatch.setattr(config, "ICLOUD_DRIVE", icloud)
    with _client(tmp_env) as client:
        r = client.post(
            "/workspaces/add",
            data={"value": str(project), "name": "Agentic-OS"},
        )
        assert r.status_code == 200
        assert r.headers.get("X-Workspace-Path") == str(project.resolve())
    assert workspace.valid_path(str(project.resolve())) is True


def test_api_folders_skips_icloud_desktop_symlink(tmp_env, tmp_path, monkeypatch):
    from app import config
    root = tmp_path / "home"
    icloud = root / "Library/Mobile Documents/com~apple~CloudDocs"
    icloud.mkdir(parents=True)
    (root / "Desktop").mkdir()
    (icloud / "Desktop").symlink_to(root / "Desktop")
    (icloud / "Projects").mkdir()
    monkeypatch.setattr(config, "BROWSE_ROOT", root)
    monkeypatch.setattr(config, "ICLOUD_DRIVE", icloud)
    with _client(tmp_env) as client:
        d = client.get("/api/folders", params={"path": str(icloud)}).json()
        names = {x["name"] for x in d["dirs"]}
        assert "Projects" in names
        assert "Desktop" not in names


def test_api_folders_notice_for_missing_icloud_child(tmp_env, tmp_path, monkeypatch):
    from app import config
    root = tmp_path / "home"
    icloud = root / "Library/Mobile Documents/com~apple~CloudDocs"
    icloud.mkdir(parents=True)
    missing = icloud / "LLM WIKI" / "Agentic-OS"
    monkeypatch.setattr(config, "BROWSE_ROOT", root)
    monkeypatch.setattr(config, "ICLOUD_DRIVE", icloud)
    with _client(tmp_env) as client:
        d = client.get("/api/folders", params={"path": str(missing)}).json()
        assert d["path"] == str(icloud.resolve())
        assert "notice" in d
        assert d["requested"] == str(missing.resolve())


def test_is_browse_allowed_cloud_storage_path(tmp_env, tmp_path, monkeypatch):
    from app import config
    root = tmp_path / "home"
    cloud = root / "Library/CloudStorage/iCloudDrive-test@icloud.com"
    project = cloud / "Projects" / "demo"
    project.mkdir(parents=True)
    monkeypatch.setattr(config, "BROWSE_ROOT", root)
    monkeypatch.setattr(config, "ICLOUD_DRIVE", root / "nope")
    assert config.is_browse_allowed(project) is True


def test_workspace_valid_path_accepts_path_variants(tmp_env, tmp_path, monkeypatch):
    from app import config, workspace
    root = tmp_path / "home"
    project = root / "Projects" / "demo"
    project.mkdir(parents=True)
    data_project = Path(f"/System/Volumes/Data{project}")
    monkeypatch.setattr(config, "BROWSE_ROOT", root)
    orig_variants = config._path_variants
    monkeypatch.setattr(
        config,
        "_path_variants",
        lambda path: [project.resolve(), data_project]
        if str(config.resolve_path(path)) in {str(project.resolve()), str(data_project)}
        else orig_variants(path),
    )
    ws = workspace.add_local("p", str(project))
    assert workspace.valid_path(str(data_project)) is True
    assert workspace.valid_path(ws["path"]) is True


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
