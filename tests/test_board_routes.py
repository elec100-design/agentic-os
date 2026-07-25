from fastapi.testclient import TestClient

from app import config, db, workspace


def _client(tmp_env):
    from app.main import app
    return TestClient(app)


def test_board_page_renders(tmp_env):
    with _client(tmp_env) as client:
        r = client.get("/board")
        assert r.status_code == 200
        assert "Vision board" in r.text  # 기본 언어는 영어
        # 메인 컴포저와 동일한 에이전트/모델 칩 마크업
        assert 'id="agent-btn"' in r.text
        assert 'id="model-btn"' in r.text
        assert 'id="provider-input"' in r.text
        assert 'id="model-input"' in r.text
        assert 'id="agent-popup"' in r.text
        assert 'id="model-popup"' in r.text
        # 메인 챗과 동일한 작업 위치(로컬/GitHub) 피커·모달
        assert 'id="workspace-picker"' in r.text
        assert 'hx-get="/partials/workspaces"' in r.text
        assert 'id="ws-modal"' in r.text
        assert 'id="ws-modal-body"' in r.text
        assert 'id="ws-modal-foot"' in r.text
        assert 'id="ws-modal-close"' in r.text
        assert 'data-tab="folder"' in r.text
        assert 'data-tab="github"' in r.text
        # 메인 챗과 동일한 첨부·도구 팝업(파일/메모리/타임아웃)
        assert 'id="tools-btn"' in r.text
        assert 'id="tools-popup"' in r.text
        assert 'id="file-chips"' in r.text
        assert 'name="files"' in r.text
        assert 'name="attach_memory"' in r.text
        assert 'name="timeout_min"' in r.text
        assert 'enctype="multipart/form-data"' in r.text
        assert "const MODELS" in r.text
        assert "const AGENT_ORDER" in r.text
        assert "const COUNCIL_ENABLED" in r.text
        assert "/static/app.js" in r.text


def test_board_page_includes_agent_selection_context(tmp_env, monkeypatch):
    """비전 보드도 메인 인덱스와 동일한 provider_models/agent_order 컨텍스트를 받는다."""
    from app import main
    captured = {}
    orig = main.templates.TemplateResponse

    def spy(request, name, context=None, *a, **kw):
        if name == "board.html":
            captured.update(context or {})
        return orig(request, name, context, *a, **kw)

    monkeypatch.setattr(main.templates, "TemplateResponse", spy)
    with _client(tmp_env) as client:
        assert client.get("/board").status_code == 200
    assert "provider_models" in captured
    assert "agent_order" in captured
    assert "council_enabled" in captured


def test_create_project_with_provider_and_model(tmp_env, monkeypatch):
    from app import orchestrator
    monkeypatch.setattr(orchestrator.models, "is_valid_model",
                        lambda provider, model: True)
    with _client(tmp_env) as client:
        r = client.post("/projects", data={
            "goal": "목표", "provider": "grok", "model": "gpt-5",
        }, follow_redirects=False)
        assert r.status_code == 303
        pid = int(r.headers["location"].rsplit("/", 1)[-1])
    conn = db.get_conn(config.DB_PATH)
    project = db.get_project(conn, pid)
    assert project["planner"] == "grok"
    assert project["planner_model"] == "gpt-5"


def test_create_project_with_workdir_tools_and_model(tmp_env, tmp_path, monkeypatch):
    """워크스페이스·에이전트/모델·첨부(메모리/타임아웃)를 한 폼에서 함께 제출."""
    from app import main, orchestrator
    monkeypatch.setattr(orchestrator.models, "is_valid_model",
                        lambda provider, model: True)
    monkeypatch.setattr(main.memory, "build_context",
                        lambda goal: "[메모리 컨텍스트]\n")
    d = tmp_path / "proj"
    d.mkdir()
    ws = workspace.add_local("proj", str(d))
    captured = {}
    orig_start = orchestrator.start_project

    def spy(conn, goal, **kw):
        captured.update(kw)
        return orig_start(conn, goal, **kw)
    monkeypatch.setattr(orchestrator, "start_project", spy)

    with _client(tmp_env) as client:
        r = client.post("/projects", data={
            "goal": "목표", "provider": "claude", "model": "opus",
            "workdir": ws["path"], "attach_memory": "true", "timeout_min": "20",
        }, files={"files": ("note.txt", b"hello", "text/plain")},
        follow_redirects=False)
        assert r.status_code == 303
        pid = int(r.headers["location"].rsplit("/", 1)[-1])

    conn = db.get_conn(config.DB_PATH)
    project = db.get_project(conn, pid)
    assert project["planner"] == "claude"
    assert project["planner_model"] == "opus"
    assert project["workdir"] == ws["path"]
    plan_job = db.get_job(conn, project["plan_job_id"])
    assert plan_job["timeout_sec"] == 20 * 60
    assert "[메모리 컨텍스트]" in plan_job["prompt"]
    assert "note.txt" in plan_job["prompt"]


def test_create_project_invalid_model_falls_back(tmp_env, monkeypatch):
    from app import orchestrator
    monkeypatch.setattr(orchestrator.models, "is_valid_model",
                        lambda provider, model: False)
    with _client(tmp_env) as client:
        r = client.post("/projects", data={
            "goal": "목표", "provider": "grok", "model": "bogus",
        }, follow_redirects=False)
        pid = int(r.headers["location"].rsplit("/", 1)[-1])
    conn = db.get_conn(config.DB_PATH)
    project = db.get_project(conn, pid)
    assert project["planner"] == "grok"
    assert project["planner_model"] is None


def test_create_project_with_registered_workdir(tmp_env, tmp_path):
    d = tmp_path / "proj"
    d.mkdir()
    ws = workspace.add_local("proj", str(d))
    with _client(tmp_env) as client:
        r = client.post("/projects", data={
            "goal": "목표", "workdir": ws["path"],
        }, follow_redirects=False)
        pid = int(r.headers["location"].rsplit("/", 1)[-1])
    conn = db.get_conn(config.DB_PATH)
    assert db.get_project(conn, pid)["workdir"] == ws["path"]


def test_create_project_rejects_unregistered_workdir(tmp_env, tmp_path):
    d = tmp_path / "unregistered"
    d.mkdir()
    with _client(tmp_env) as client:
        r = client.post("/projects", data={
            "goal": "목표", "workdir": str(d),
        }, follow_redirects=False)
        pid = int(r.headers["location"].rsplit("/", 1)[-1])
    conn = db.get_conn(config.DB_PATH)
    assert db.get_project(conn, pid)["workdir"] is None


def test_create_project_starts_planning(tmp_env):
    with _client(tmp_env) as client:
        r = client.post("/projects", data={"goal": "랜딩페이지 만들어줘"},
                        follow_redirects=False)
        assert r.status_code == 303
        pid = int(r.headers["location"].rsplit("/", 1)[-1])
    conn = db.get_conn(config.DB_PATH)
    project = db.get_project(conn, pid)
    assert project["status"] == "planning"
    assert db.get_job(conn, project["plan_job_id"]) is not None


def test_project_page_and_board_partial(tmp_env):
    with _client(tmp_env) as client:
        r = client.post("/projects", data={"goal": "목표"}, follow_redirects=False)
        pid = int(r.headers["location"].rsplit("/", 1)[-1])
        assert client.get(f"/projects/{pid}").status_code == 200
        r = client.get(f"/partials/board/{pid}")
        assert r.status_code == 200
        assert "drafting a plan" in r.text
        assert client.get("/partials/projects").status_code == 200
        assert client.get("/projects/999").status_code == 404


def test_approve_guard_while_planning(tmp_env):
    with _client(tmp_env) as client:
        r = client.post("/projects", data={"goal": "목표"}, follow_redirects=False)
        pid = int(r.headers["location"].rsplit("/", 1)[-1])
        assert client.post(f"/projects/{pid}/approve",
                           follow_redirects=False).status_code == 400
        conn = db.get_conn(config.DB_PATH)
        db.update_project(conn, pid, status="plan_ready")
        assert client.post(f"/projects/{pid}/approve",
                           follow_redirects=False).status_code == 303
        assert db.get_project(conn, pid)["status"] == "running"


def test_board_partial_renders_graph(tmp_env):
    with _client(tmp_env) as client:
        conn = db.get_conn(config.DB_PATH)
        pid = db.create_project(conn, "목표")
        db.update_project(conn, pid, status="plan_ready")
        db.create_task(conn, pid, 1, "첫 태스크", "설명", "text", "claude")
        db.create_task(conn, pid, 2, "둘째", "설명", "text", "grok", depends_on="1")
        r = client.get(f"/partials/board/{pid}")
        assert r.status_code == 200
        assert "graph-svg" in r.text
        assert "첫 태스크" in r.text
        assert "Approve" in r.text


def test_task_detail_and_retry(tmp_env):
    with _client(tmp_env) as client:
        conn = db.get_conn(config.DB_PATH)
        pid = db.create_project(conn, "목표")
        db.update_project(conn, pid, status="paused")
        tid = db.create_task(conn, pid, 1, "태스크", "설명", "text", "claude")
        db.update_task(conn, tid, status="failed", error="터짐")
        r = client.get(f"/partials/task/{tid}")
        assert r.status_code == 200
        assert "Retry" in r.text and "터짐" in r.text
        assert client.post(f"/tasks/{tid}/retry",
                           follow_redirects=False).status_code == 303
        assert db.get_task(conn, tid)["status"] == "pending"
        assert db.get_project(conn, pid)["status"] == "running"


def test_delete_project_removes_tasks_and_jobs(tmp_env):
    with _client(tmp_env) as client:
        conn = db.get_conn(config.DB_PATH)
        pid = db.create_project(conn, "목표")
        job_id = db.create_job(conn, "태스크 잡", "claude")
        tid = db.create_task(conn, pid, 1, "태스크", "설명", "text", "claude")
        db.update_task(conn, tid, job_id=job_id)
        r = client.post(f"/projects/{pid}/delete", follow_redirects=False)
        assert r.status_code == 303
        assert db.get_project(conn, pid) is None
        assert db.list_tasks(conn, pid) == []
        assert db.get_job(conn, job_id) is None


def test_artifact_route_blocks_traversal(tmp_env):
    with _client(tmp_env) as client:
        art_dir = config.ARTIFACTS_DIR / "1"
        art_dir.mkdir(parents=True, exist_ok=True)
        (art_dir / "ok.png").write_bytes(b"png")
        secret = config.ARTIFACTS_DIR.parent / "secret.txt"
        secret.write_text("비밀")
        assert client.get("/artifacts/1/ok.png").status_code == 200
        r = client.get("/artifacts/1/..%2F..%2Fsecret.txt")
        assert r.status_code == 404
        r = client.get("/artifacts/1/../../secret.txt")
        assert r.status_code == 404
