"""채팅→탭 자동 오픈 연결고리(§4) — messages.created_task_id가 메시지 조회
API 응답에 실려 오는지 검증한다. 값을 채우는 쪽(orchestrator.add_task)의
동작은 tests/test_orchestrator.py에서, 마이그레이션 멱등성은
tests/test_db.py에서 각각 검증한다."""
from fastapi.testclient import TestClient

from app import config, db, orchestrator


def _client(tmp_env):
    from app.main import app
    return TestClient(app)


def _channel_and_project(conn):
    project_id = db.create_project(conn, "채팅 태스크 연결 테스트")
    db.update_project(conn, project_id, status="plan_ready")  # add_task는 편집 가능 상태만 허용
    channel_id = db.create_channel(conn, "테스트 채널")
    return channel_id, project_id


def test_channel_messages_api_includes_created_task_id_field(tmp_env):
    conn = db.get_conn(config.DB_PATH)
    channel_id, project_id = _channel_and_project(conn)
    message_id = db.create_message(conn, channel_id, role="user", body="태스크 만들어줘")

    with _client(tmp_env) as client:
        roots = client.get(f"/api/channels/{channel_id}/messages").json()
        assert roots[0]["created_task_id"] is None

        task_id = orchestrator.add_task(conn, project_id, "채팅에서 생긴 태스크",
                                        source_message_id=message_id)

        roots = client.get(f"/api/channels/{channel_id}/messages").json()
        assert roots[0]["created_task_id"] == task_id

        single = client.get(f"/api/messages/{message_id}").json()
        assert single["created_task_id"] == task_id

        thread = client.get(f"/api/messages/{message_id}/thread").json()
        assert thread["messages"][0]["created_task_id"] == task_id


def test_add_task_endpoint_accepts_message_id_and_fills_created_task_id(tmp_env):
    conn = db.get_conn(config.DB_PATH)
    channel_id, project_id = _channel_and_project(conn)
    message_id = db.create_message(conn, channel_id, role="user", body="태스크 만들어줘")

    with _client(tmp_env) as client:
        res = client.post(f"/projects/{project_id}/tasks", data={
            "title": "폼으로 만든 태스크", "message_id": str(message_id),
        })
        assert res.status_code == 200

    task = db.list_tasks(conn, project_id)[0]
    assert db.get_message(conn, message_id)["created_task_id"] == task["id"]
