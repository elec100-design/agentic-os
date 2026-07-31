"""사이드바 태스크 편집·재시도·시도 이력 API와 일시정지/재개 엔드포인트."""
from fastapi.testclient import TestClient

from app import db, orchestrator


def _client(tmp_env):
    from app.main import app
    return TestClient(app)


def _project(conn, status="paused", n=2):
    pid = db.create_project(conn, "목표")
    db.update_project(conn, pid, status=status)
    ids = [db.create_task(conn, pid, seq, f"태스크{seq}", f"설명{seq}", "text",
                          "claude")
           for seq in range(1, n + 1)]
    return pid, ids


def test_pause_and_resume_endpoints(tmp_env):
    conn = db.get_conn()
    pid, _ = _project(conn, status="running")
    with _client(tmp_env) as client:
        r = client.post(f"/projects/{pid}/pause", follow_redirects=False)
        assert r.status_code == 303
        assert db.get_project(conn, pid)["pause_reason"] == "manual"

        # 인플라이트가 없으므로 오케스트레이터 틱에서 paused로 떨어진다.
        orchestrator._advance(conn, db.get_project(conn, pid))
        assert db.get_project(conn, pid)["status"] == "paused"

        r = client.post(f"/projects/{pid}/resume", follow_redirects=False)
        assert r.status_code == 303
        project = db.get_project(conn, pid)
        assert project["status"] == "running" and project["pause_reason"] is None


def test_pause_on_non_running_project_is_400(tmp_env):
    conn = db.get_conn()
    pid, _ = _project(conn, status="plan_ready")
    with _client(tmp_env) as client:
        assert client.post(f"/projects/{pid}/pause").status_code == 400


def test_board_partial_shows_pause_and_resume_buttons(tmp_env):
    conn = db.get_conn()
    pid, _ = _project(conn, status="running")
    with _client(tmp_env) as client:
        html = client.get(f"/partials/board/{pid}").text
        assert f"/projects/{pid}/pause" in html

        db.update_project(conn, pid, status="paused", pause_reason="manual")
        html = client.get(f"/partials/board/{pid}").text
        assert f"/projects/{pid}/resume" in html


def test_get_task_includes_sidebar_choices(tmp_env):
    conn = db.get_conn()
    _pid, ids = _project(conn)
    with _client(tmp_env) as client:
        data = client.get(f"/api/tasks/{ids[0]}").json()
    assert data["task_types"] and data["agents"]
    assert [s["seq"] for s in data["siblings"]] == [2]
    assert data["editable"] is True


def test_patch_task_updates_instruction_and_deps(tmp_env):
    conn = db.get_conn()
    _pid, ids = _project(conn)
    with _client(tmp_env) as client:
        r = client.patch(f"/api/tasks/{ids[1]}", json={
            "extra_instruction": "짧게 써라", "depends_on": [1]})
        assert r.status_code == 200
    task = db.get_task(conn, ids[1])
    assert task["extra_instruction"] == "짧게 써라"
    assert orchestrator.task_deps(task) == [1]


def test_patch_task_rejects_cyclic_deps(tmp_env):
    conn = db.get_conn()
    _pid, ids = _project(conn)
    with _client(tmp_env) as client:
        client.patch(f"/api/tasks/{ids[1]}", json={"depends_on": [1]})
        r = client.patch(f"/api/tasks/{ids[0]}", json={"depends_on": [2]})
    assert r.status_code == 400


def test_patch_task_blocked_while_project_running(tmp_env):
    conn = db.get_conn()
    pid, ids = _project(conn, status="running")
    with _client(tmp_env) as client:
        r = client.patch(f"/api/tasks/{ids[0]}", json={"title": "새 제목"})
    assert r.status_code == 409
    assert db.get_project(conn, pid)["status"] == "running"


def test_api_retry_returns_json_not_redirect(tmp_env):
    conn = db.get_conn()
    pid, ids = _project(conn)
    db.update_task(conn, ids[0], status="failed", error="터짐")
    with _client(tmp_env) as client:
        r = client.post(f"/api/tasks/{ids[0]}/retry", json={})
        assert r.status_code == 200
        assert r.json()["status"] == "pending"
    # 재시도는 일시정지된 프로젝트를 그대로 재개시킨다.
    assert db.get_project(conn, pid)["status"] == "running"


def test_api_retry_rejects_pending_task(tmp_env):
    conn = db.get_conn()
    _pid, ids = _project(conn)
    with _client(tmp_env) as client:
        assert client.post(f"/api/tasks/{ids[0]}/retry", json={}).status_code == 400


def test_task_runs_endpoint_lists_attempt_history(tmp_env):
    conn = db.get_conn()
    pid, ids = _project(conn)
    run_id = db.create_workflow_run(conn, pid)
    first = db.create_task_run(conn, ids[0], run_id, agent="claude")
    db.append_task_run_output(conn, first, "하다 만 출력")
    db.mark_task_run_failed(conn, first, error="사용량 초과")
    db.create_task_run(conn, ids[0], run_id, agent="codex")

    with _client(tmp_env) as client:
        runs = client.get(f"/api/tasks/{ids[0]}/runs").json()
    assert [r["attempt"] for r in runs] == [1, 2]
    assert runs[0]["status"] == "failed" and "하다 만" in runs[0]["output"]
    assert runs[1]["agent"] == "codex"
