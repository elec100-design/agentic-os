from fastapi.testclient import TestClient

from app import config, db


def _client(tmp_env):
    from app.main import app
    return TestClient(app)


def _project_id():
    conn = db.get_conn(config.DB_PATH)
    return db.create_project(conn, "탭 테스트")


def test_board_tab_crud_and_closed_tabs_are_hidden(tmp_env):
    project_id = _project_id()
    with _client(tmp_env) as client:
        created = client.post(f"/api/boards/{project_id}/tabs", json={
            "title": "결과물", "kind": "artifact", "ref_id": 7,
        })
        assert created.status_code == 201
        tab = created.json()["tab"]
        assert tab["title"] == "결과물"
        assert tab["is_active"] == 1

        renamed = client.patch(f"/api/boards/{project_id}/tabs/{tab['id']}",
                               json={"title": "최종 결과물"})
        assert renamed.status_code == 200
        assert renamed.json()["tab"]["title"] == "최종 결과물"

        assert client.delete(f"/api/boards/{project_id}/tabs/{tab['id']}").status_code == 200
        assert client.get(f"/api/boards/{project_id}/tabs").json()["tabs"] == []


def test_board_tab_reorder_and_single_active_tab(tmp_env):
    project_id = _project_id()
    with _client(tmp_env) as client:
        first = client.post(f"/api/boards/{project_id}/tabs", json={
            "title": "첫째", "kind": "chat"}).json()["tab"]
        second = client.post(f"/api/boards/{project_id}/tabs", json={
            "title": "둘째", "kind": "preview"}).json()["tab"]

        response = client.put(f"/api/boards/{project_id}/tabs/order",
                              json={"tab_ids": [second["id"], first["id"]]})
        assert response.status_code == 200
        assert [tab["id"] for tab in response.json()["tabs"]] == [second["id"], first["id"]]

        activated = client.post(
            f"/api/boards/{project_id}/tabs/{first['id']}/activate")
        assert activated.status_code == 200
        tabs = client.get(f"/api/boards/{project_id}/tabs").json()["tabs"]
        assert [tab["id"] for tab in tabs if tab["is_active"]] == [first["id"]]


def test_board_tab_same_reference_is_reused(tmp_env):
    project_id = _project_id()
    with _client(tmp_env) as client:
        first = client.post(f"/api/boards/{project_id}/tabs", json={
            "title": "태스크", "kind": "task", "ref_id": 42,
        }).json()["tab"]
        reused = client.post(f"/api/boards/{project_id}/tabs", json={
            "title": "새 태스크 제목", "kind": "task", "ref_id": 42,
        }).json()["tab"]
        assert reused["id"] == first["id"]
        tabs = client.get(f"/api/boards/{project_id}/tabs").json()["tabs"]
        assert len(tabs) == 1
        assert tabs[0]["title"] == "새 태스크 제목"


def test_board_tab_flow_kind_is_accepted(tmp_env):
    project_id = _project_id()
    with _client(tmp_env) as client:
        created = client.post(f"/api/boards/{project_id}/tabs", json={
            "title": "진행 흐름", "kind": "flow",
        })
        assert created.status_code == 201
        assert created.json()["tab"]["kind"] == "flow"


def test_board_tab_rejects_unknown_kind(tmp_env):
    project_id = _project_id()
    with _client(tmp_env) as client:
        rejected = client.post(f"/api/boards/{project_id}/tabs", json={
            "title": "알 수 없음", "kind": "bogus",
        })
        assert rejected.status_code == 400


def test_partial_task_404_for_missing_task_drives_stale_tab_cleanup(tmp_env):
    # board-workspace.js의 localStorage 탭 복원 로직은 이 엔드포인트의 404를
    # 근거로 "참조된 태스크가 더 이상 없다"고 판단해 복원 중 조용히 탭을 버린다.
    with _client(tmp_env) as client:
        assert client.get("/partials/task/999999").status_code == 404
        assert client.head("/partials/task/999999").status_code == 404


def test_orchestrator_creates_and_reuses_task_tabs(tmp_env):
    from app import orchestrator

    conn = db.get_conn(config.DB_PATH)
    project_id = db.create_project(conn, "목표")
    project = db.get_project(conn, project_id)
    plan = {"title": "계획", "tasks": [{
        "id": 1, "title": "구현", "description": "구현한다", "type": "text",
        "provider": "claude", "depends_on": [],
    }]}
    orchestrator._instantiate_plan(conn, project, plan)
    task = db.list_tasks(conn, project_id)[0]
    db.get_or_create_board_tab(conn, project_id, task["title"], "task", task["id"],
                               status="pending", activate=False)
    task_tabs = [tab for tab in db.list_board_tabs(conn, project_id)
                 if tab["kind"] == "task"]
    assert len(task_tabs) == 1
