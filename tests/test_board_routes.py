from fastapi.testclient import TestClient

from app import config, db, workspace


def _client(tmp_env):
    from app.main import app
    return TestClient(app)


def test_board_page_redirects_to_dashboard(tmp_env):
    """비전 보드 전용 페이지는 홈 대시보드로 흡수됐다 — 북마크만 살려 둔다."""
    with _client(tmp_env) as client:
        r = client.get("/board", follow_redirects=False)
        assert r.status_code == 308
        assert r.headers["location"] == "/"
        # 실제로 따라가면 홈이 뜬다 (컴포저·프로젝트 목록이 여기 있다)
        assert client.get("/board").status_code == 200


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


def test_done_task_with_error_output_offers_recovery(tmp_env):
    """'완료'지만 결과가 오류 안내문인 태스크 — 조치 패널과 경고가 보여야 한다."""
    with _client(tmp_env) as client:
        conn = db.get_conn(config.DB_PATH)
        pid = db.create_project(conn, "목표")
        db.update_project(conn, pid, status="done")
        tid = db.create_task(conn, pid, 1, "태스크", "설명", "text", "claude")
        db.update_task(conn, tid, status="done",
                       output="jetski: no output produced — auto-denied.")
        r = client.get(f"/partials/task/{tid}")
        assert r.status_code == 200
        assert "Switch model" in r.text and "Add instructions" in r.text
        # 캔버스에서도 눈에 띄어야 조치할 노드를 찾을 수 있다
        assert "has-warn" in client.get(f"/partials/board/{pid}").text

        r = client.post(f"/tasks/{tid}/retry",
                        data={"agent": "claude", "model": "",
                              "instruction": "권한 없는 명령은 쓰지 마라"},
                        follow_redirects=False)
        assert r.status_code == 303
        task = db.get_task(conn, tid)
        assert task["status"] == "pending"
        assert task["extra_instruction"] == "권한 없는 명령은 쓰지 마라"
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


def test_delete_cancelled_project_removes_tasks_and_jobs(tmp_env):
    with _client(tmp_env) as client:
        conn = db.get_conn(config.DB_PATH)
        pid = db.create_project(conn, "목표")
        db.update_project(conn, pid, status="cancelled")
        job_id = db.create_job(conn, "태스크 잡", "claude")
        tid = db.create_task(conn, pid, 1, "태스크", "설명", "text", "claude")
        db.update_task(conn, tid, job_id=job_id)
        r = client.post(f"/projects/{pid}/delete-cancelled", follow_redirects=False)
        assert r.status_code == 303
        assert db.get_project(conn, pid) is None
        assert db.list_tasks(conn, pid) == []
        assert db.get_job(conn, job_id) is None


def test_delete_cancelled_project_rejects_non_cancelled(tmp_env):
    with _client(tmp_env) as client:
        conn = db.get_conn(config.DB_PATH)
        pid = db.create_project(conn, "목표")
        db.update_project(conn, pid, status="running")
        r = client.post(f"/projects/{pid}/delete-cancelled")
        assert r.status_code == 400
        assert "취소" in r.json()["detail"]
        assert db.get_project(conn, pid) is not None


def test_delete_cancelled_project_404_when_missing(tmp_env):
    with _client(tmp_env) as client:
        r = client.post("/projects/999/delete-cancelled")
        assert r.status_code == 404


def test_clear_cancelled_projects_deletes_only_cancelled(tmp_env):
    with _client(tmp_env) as client:
        conn = db.get_conn(config.DB_PATH)
        cancelled_pid = db.create_project(conn, "취소된 목표")
        db.update_project(conn, cancelled_pid, status="cancelled")
        job_id = db.create_job(conn, "태스크 잡", "claude")
        tid = db.create_task(conn, cancelled_pid, 1, "태스크", "설명",
                             "text", "claude")
        db.update_task(conn, tid, job_id=job_id)

        running_pid = db.create_project(conn, "실행 중 목표")
        db.update_project(conn, running_pid, status="running")
        other_cancelled_pid = db.create_project(conn, "취소된 목표2")
        db.update_project(conn, other_cancelled_pid, status="cancelled")

        r = client.post("/projects/cancelled/clear")
        assert r.status_code == 200
        assert r.json() == {"deleted": 2}

        assert db.get_project(conn, cancelled_pid) is None
        assert db.get_project(conn, other_cancelled_pid) is None
        assert db.list_tasks(conn, cancelled_pid) == []
        assert db.get_job(conn, job_id) is None
        assert db.get_project(conn, running_pid) is not None


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


# --- 워크플로 다이어그램 편집기 -------------------------------------------------

def _diagram_project(status="plan_ready", specs=((1, []), (2, [1]))):
    """편집 가능한 상태의 프로젝트 + 태스크. (pid, {seq: task_id}) 반환."""
    conn = db.get_conn(config.DB_PATH)
    pid = db.create_project(conn, "목표")
    db.update_project(conn, pid, status=status)
    ids = {}
    for seq, deps in specs:
        ids[seq] = db.create_task(conn, pid, seq, f"태스크{seq}", f"설명{seq}",
                                  "text", "claude",
                                  depends_on=",".join(str(d) for d in deps))
    return pid, ids


def test_board_partial_shows_editor_when_plan_ready(tmp_env):
    pid, _ = _diagram_project()
    with _client(tmp_env) as client:
        r = client.get(f"/partials/board/{pid}")
        assert r.status_code == 200
        assert 'class="graph-toolbar"' in r.text
        assert 'data-canvas="add"' in r.text          # 노드 팔레트
        assert 'data-canvas="relayout"' in r.text     # 자동 정렬
        assert 'class="node-port port-out"' in r.text  # 연결 포트
        assert 'class="node-palette"' in r.text
        assert 'data-editable="1"' in r.text


def test_board_partial_hides_editor_while_running(tmp_env):
    pid, _ = _diagram_project(status="running")
    with _client(tmp_env) as client:
        r = client.get(f"/partials/board/{pid}")
        assert r.status_code == 200
        assert "node-port" not in r.text
        assert "node-palette" not in r.text
        assert 'data-canvas="fit"' in r.text          # 팬/줌은 실행 중에도 쓴다


def test_board_partial_vertical_orientation_for_mobile(tmp_env):
    pid, _ = _diagram_project()
    with _client(tmp_env) as client:
        lr = client.get(f"/partials/board/{pid}?o=lr").text
        tb = client.get(f"/partials/board/{pid}?o=tb").text
    assert 'data-orientation="lr"' in lr
    assert 'data-orientation="tb"' in tb
    assert lr != tb
    # 알 수 없는 값은 가로로 폴백한다
    with _client(tmp_env) as client:
        assert 'data-orientation="lr"' in client.get(
            f"/partials/board/{pid}?o=diagonal").text


def test_edit_task_route_updates_and_returns_board(tmp_env):
    pid, ids = _diagram_project()
    with _client(tmp_env) as client:
        r = client.post(f"/tasks/{ids[1]}/edit", data={
            "title": "고친 제목", "description": "고친 설명",
            "task_type": "image", "agent": "auto", "o": "lr"})
        assert r.status_code == 200
        assert "graph-toolbar" in r.text       # 보드 조각을 그대로 돌려준다
    conn = db.get_conn(config.DB_PATH)
    task = db.get_task(conn, ids[1])
    assert task["title"] == "고친 제목"
    assert task["task_type"] == "image"
    assert task["provider"] == "media"


def test_edit_task_route_rejects_bad_input(tmp_env):
    pid, ids = _diagram_project()
    with _client(tmp_env) as client:
        r = client.post(f"/tasks/{ids[1]}/edit", data={
            "title": "제목", "description": "설명",
            "task_type": "hologram", "agent": "auto"})
        assert r.status_code == 400
        assert client.post("/tasks/9999/edit", data={
            "title": "t", "description": "d"}).status_code == 404


def test_edit_task_route_rejects_unknown_agent(tmp_env):
    pid, ids = _diagram_project()
    with _client(tmp_env) as client:
        r = client.post(f"/tasks/{ids[1]}/edit", data={
            "title": "제목", "description": "설명",
            "task_type": "text", "agent": "no-such-agent"})
        assert r.status_code == 400


def test_edit_task_route_updates_model_and_extra_instruction(tmp_env, monkeypatch):
    from app import orchestrator
    monkeypatch.setattr(orchestrator.models, "is_valid_model",
                        lambda provider, model: True)
    pid, ids = _diagram_project()
    with _client(tmp_env) as client:
        r = client.post(f"/tasks/{ids[1]}/edit", data={
            "title": "태스크1", "description": "설명1",
            "task_type": "text", "agent": "claude", "model": "opus",
            "extra_instruction": "파일 편집만으로 끝내라"})
        assert r.status_code == 200
    conn = db.get_conn(config.DB_PATH)
    task = db.get_task(conn, ids[1])
    assert task["model"] == "opus"
    assert task["extra_instruction"] == "파일 편집만으로 끝내라"


def test_edit_task_route_rejects_unknown_model(tmp_env, monkeypatch):
    from app import orchestrator
    monkeypatch.setattr(orchestrator.models, "is_valid_model",
                        lambda provider, model: False)
    pid, ids = _diagram_project()
    with _client(tmp_env) as client:
        r = client.post(f"/tasks/{ids[1]}/edit", data={
            "title": "태스크1", "description": "설명1",
            "task_type": "text", "agent": "claude", "model": "no-such-model"})
        assert r.status_code == 400


def test_edit_task_route_clears_model_when_agent_changes(tmp_env):
    pid, ids = _diagram_project()
    conn = db.get_conn(config.DB_PATH)
    db.update_task(conn, ids[1], provider="claude", model="opus")
    with _client(tmp_env) as client:
        r = client.post(f"/tasks/{ids[1]}/edit", data={
            "title": "태스크1", "description": "설명1",
            "task_type": "text", "agent": "hermes"})
        assert r.status_code == 200
    assert db.get_task(conn, ids[1])["model"] is None


def test_edit_task_route_clears_model_when_default_selected(tmp_env):
    """편집 폼의 빈 model 값은 '기본값' 선택이므로 기존 선택을 제거한다."""
    pid, ids = _diagram_project()
    conn = db.get_conn(config.DB_PATH)
    db.update_task(conn, ids[1], provider="claude", model="opus")
    with _client(tmp_env) as client:
        r = client.post(f"/tasks/{ids[1]}/edit", data={
            "title": "둘째", "description": "설명", "task_type": "text",
            "agent": "claude", "model": "", "extra_instruction": "",
        })
    assert r.status_code == 200
    assert db.get_task(conn, ids[1])["model"] is None


def test_deps_route_adds_and_removes_edges(tmp_env):
    pid, ids = _diagram_project(specs=((1, []), (2, []), (3, [])))
    conn = db.get_conn(config.DB_PATH)
    with _client(tmp_env) as client:
        assert client.post(f"/tasks/{ids[3]}/deps",
                           data={"deps": ["1", "2"]}).status_code == 200
        assert db.get_task(conn, ids[3])["depends_on"] == "1,2"
        # 빈 목록 = 모든 연결 끊기
        assert client.post(f"/tasks/{ids[3]}/deps", data={}).status_code == 200
        assert db.get_task(conn, ids[3])["depends_on"] == ""


def test_deps_route_rejects_cycle(tmp_env):
    pid, ids = _diagram_project()          # 2가 1에 의존
    with _client(tmp_env) as client:
        r = client.post(f"/tasks/{ids[1]}/deps", data={"deps": ["2"]})
        assert r.status_code == 400


def test_deps_route_rejects_self_reference(tmp_env):
    pid, ids = _diagram_project(specs=((1, []),))
    with _client(tmp_env) as client:
        r = client.post(f"/tasks/{ids[1]}/deps", data={"deps": ["1"]})
        assert r.status_code == 400


def test_move_route_persists_position(tmp_env):
    pid, ids = _diagram_project()
    with _client(tmp_env) as client:
        assert client.post(f"/tasks/{ids[1]}/move",
                           data={"x": "410", "y": "96"}).status_code == 200
    conn = db.get_conn(config.DB_PATH)
    assert (db.get_task(conn, ids[1])["pos_x"],
            db.get_task(conn, ids[1])["pos_y"]) == (410.0, 96.0)


def test_relayout_route_clears_positions(tmp_env):
    pid, ids = _diagram_project()
    with _client(tmp_env) as client:
        client.post(f"/tasks/{ids[1]}/move", data={"x": "410", "y": "96"})
        assert client.post(f"/projects/{pid}/relayout").status_code == 200
    conn = db.get_conn(config.DB_PATH)
    assert db.get_task(conn, ids[1])["pos_x"] is None


def test_add_task_route_creates_node(tmp_env):
    pid, ids = _diagram_project()
    with _client(tmp_env) as client:
        r = client.post(f"/projects/{pid}/tasks", data={
            "title": "새 노드", "description": "새 설명", "task_type": "text",
            "agent": "hermes", "x": "300", "y": "40"})
        assert r.status_code == 200
        assert client.post("/projects/9999/tasks",
                           data={"title": "t"}).status_code == 404
    conn = db.get_conn(config.DB_PATH)
    added = [t for t in db.list_tasks(conn, pid) if t["title"] == "새 노드"][0]
    assert added["seq"] == 3
    assert added["provider"] == "hermes"
    assert (added["pos_x"], added["pos_y"]) == (300.0, 40.0)


def test_add_task_route_without_coordinates(tmp_env):
    """폼을 JS 없이 직접 제출하면 좌표가 비어 온다 — 자동 배치에 맡긴다."""
    pid, _ = _diagram_project()
    with _client(tmp_env) as client:
        r = client.post(f"/projects/{pid}/tasks",
                        data={"title": "좌표 없는 노드", "x": "", "y": ""})
        assert r.status_code == 200
    conn = db.get_conn(config.DB_PATH)
    added = [t for t in db.list_tasks(conn, pid) if t["title"] == "좌표 없는 노드"][0]
    assert added["pos_x"] is None


def test_delete_task_route_strips_edges(tmp_env):
    pid, ids = _diagram_project()
    with _client(tmp_env) as client:
        assert client.post(f"/tasks/{ids[1]}/delete").status_code == 200
    conn = db.get_conn(config.DB_PATH)
    assert db.get_task(conn, ids[1]) is None
    assert db.get_task(conn, ids[2])["depends_on"] == ""


def test_edit_routes_blocked_while_running(tmp_env):
    pid, ids = _diagram_project(status="running")
    with _client(tmp_env) as client:
        assert client.post(f"/tasks/{ids[1]}/delete").status_code == 400
        assert client.post(f"/tasks/{ids[2]}/deps", data={}).status_code == 400
        # 배치는 시각 요소라 실행 중에도 허용된다
        assert client.post(f"/tasks/{ids[1]}/move",
                           data={"x": "5", "y": "5"}).status_code == 200


def test_task_detail_edit_form_only_when_editable(tmp_env):
    pid, ids = _diagram_project()
    with _client(tmp_env) as client:
        editable = client.get(f"/partials/task/{ids[2]}").text
        assert 'class="task-edit"' in editable
        assert "data-task-edit" in editable
        assert "data-task-close" in editable            # 패널 닫기 (모바일 시트)
        assert 'class="task-deps"' in editable
        assert f'data-task-deps="{ids[2]}"' in editable
        assert 'name="deps" value="1"' in editable     # 형제를 선행으로 고를 수 있다

    pid2, ids2 = _diagram_project(status="running")
    with _client(tmp_env) as client:
        locked = client.get(f"/partials/task/{ids2[1]}").text
        assert "task-edit" not in locked
        assert "task-deps" not in locked
        assert "data-task-close" in locked             # 읽기 전용에서도 닫을 수 있다


# --- 사이드바 태스크 편집·채팅 JSON API -------------------------------------

def test_get_task_route_returns_detail(tmp_env):
    pid, ids = _diagram_project()
    with _client(tmp_env) as client:
        r = client.get(f"/api/tasks/{ids[1]}")
        assert r.status_code == 200
        body = r.json()
        assert body["id"] == ids[1]
        assert body["title"] == "태스크1"
        assert body["description"] == "설명1"
        assert body["depends_on"] == []
        assert body["editable"] is True
        assert body["recent_job"] is None


def test_get_task_route_404(tmp_env):
    with _client(tmp_env) as client:
        assert client.get("/api/tasks/999").status_code == 404


def test_patch_task_route_updates_fields(tmp_env):
    pid, ids = _diagram_project()
    with _client(tmp_env) as client:
        r = client.patch(f"/api/tasks/{ids[1]}", json={
            "title": "고친 제목", "description": "고친 설명"})
        assert r.status_code == 200
        assert r.json()["title"] == "고친 제목"
    conn = db.get_conn(config.DB_PATH)
    task = db.get_task(conn, ids[1])
    assert task["title"] == "고친 제목"
    assert task["description"] == "고친 설명"


def test_patch_task_route_rejects_while_running(tmp_env):
    pid, ids = _diagram_project(status="running")
    with _client(tmp_env) as client:
        r = client.patch(f"/api/tasks/{ids[1]}", json={"title": "새 제목"})
        assert r.status_code == 409


def test_patch_task_route_allows_when_paused(tmp_env):
    pid, ids = _diagram_project(status="paused")
    with _client(tmp_env) as client:
        r = client.patch(f"/api/tasks/{ids[1]}", json={"title": "새 제목"})
        assert r.status_code == 200


def test_patch_task_route_allows_when_done(tmp_env):
    pid, ids = _diagram_project(status="done")
    with _client(tmp_env) as client:
        r = client.patch(f"/api/tasks/{ids[1]}", json={"title": "새 제목"})
        assert r.status_code == 200


def test_patch_task_route_rejects_empty_title(tmp_env):
    pid, ids = _diagram_project()
    with _client(tmp_env) as client:
        r = client.patch(f"/api/tasks/{ids[1]}", json={"title": "   "})
        assert r.status_code == 400


def test_patch_task_route_rejects_unknown_agent(tmp_env):
    pid, ids = _diagram_project()
    with _client(tmp_env) as client:
        r = client.patch(f"/api/tasks/{ids[1]}", json={"agent": "bogus"})
        assert r.status_code == 400


def test_patch_task_route_rejects_no_fields(tmp_env):
    pid, ids = _diagram_project()
    with _client(tmp_env) as client:
        r = client.patch(f"/api/tasks/{ids[1]}", json={})
        assert r.status_code == 400


def test_patch_task_route_reset_status_flag(tmp_env):
    pid, ids = _diagram_project(status="paused")
    conn = db.get_conn(config.DB_PATH)
    db.update_task(conn, ids[1], status="failed")
    with _client(tmp_env) as client:
        r = client.patch(f"/api/tasks/{ids[1]}", json={
            "description": "고친 설명", "reset_status": True})
        assert r.status_code == 200
        assert r.json()["status"] == "pending"


def test_task_messages_route_empty_initially(tmp_env):
    pid, ids = _diagram_project()
    with _client(tmp_env) as client:
        r = client.get(f"/api/tasks/{ids[1]}/messages")
        assert r.status_code == 200
        assert r.json() == []


def test_task_messages_route_404_for_missing_task(tmp_env):
    with _client(tmp_env) as client:
        assert client.get("/api/tasks/999/messages").status_code == 404
        assert client.post("/api/tasks/999/messages",
                           json={"content": "안녕"}).status_code == 404


def test_task_messages_route_rejects_empty_content(tmp_env):
    pid, ids = _diagram_project()
    with _client(tmp_env) as client:
        r = client.post(f"/api/tasks/{ids[1]}/messages", json={"content": "  "})
        assert r.status_code == 400


def test_task_messages_route_creates_reply_and_suggestion(tmp_env, monkeypatch):
    """에이전트 호출을 모킹해, 사용자 메시지 저장 → 답변·제안 생성 →
    assistant 메시지 저장까지의 흐름을 검증한다."""
    from app import main, worker

    async def fake_run_job(conn, job, providers=None, save=True):
        db.update_job(
            conn, job["id"], status="done",
            output=('```json\n{"reply": "설명을 구체화했습니다", '
                    '"description": "더 구체적인 새 설명"}\n```'),
            finished_at=db.now_iso())

    monkeypatch.setattr(worker, "run_job", fake_run_job)
    monkeypatch.setattr(main.worker, "run_job", fake_run_job)

    pid, ids = _diagram_project()
    with _client(tmp_env) as client:
        r = client.post(f"/api/tasks/{ids[1]}/messages",
                        json={"content": "더 구체적으로 써줘"})
        assert r.status_code == 201
        body = r.json()
        assert body["reply"] == "설명을 구체화했습니다"
        assert body["suggested_description"] == "더 구체적인 새 설명"

        messages = client.get(f"/api/tasks/{ids[1]}/messages").json()
    assert len(messages) == 2
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == "더 구체적으로 써줘"
    assert messages[1]["role"] == "assistant"
    assert messages[1]["content"] == "설명을 구체화했습니다"
    assert messages[1]["suggested_description"] == "더 구체적인 새 설명"


def test_task_messages_route_falls_back_to_plain_reply_without_json(tmp_env, monkeypatch):
    from app import main, worker

    async def fake_run_job(conn, job, providers=None, save=True):
        db.update_job(conn, job["id"], status="done", output="그냥 답변입니다",
                     finished_at=db.now_iso())

    monkeypatch.setattr(worker, "run_job", fake_run_job)
    monkeypatch.setattr(main.worker, "run_job", fake_run_job)

    pid, ids = _diagram_project()
    with _client(tmp_env) as client:
        r = client.post(f"/api/tasks/{ids[1]}/messages", json={"content": "질문"})
        assert r.status_code == 201
        body = r.json()
        assert body["reply"] == "그냥 답변입니다"
        assert body["suggested_description"] is None
