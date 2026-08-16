"""대화창(채널·DM)의 파일 첨부와 작업 위치.

우측 작업 채팅에는 있고 대화창에는 없던 두 기능의 서버 쪽 계약을 확인한다.
파일은 먼저 올려 두고(POST /api/uploads) 메시지에는 경로만 싣는다 — 메시지
API 는 JSON 이고 그 위에 낙관적 렌더링·쓰레드 답장이 얹혀 있기 때문이다.
"""
from pathlib import Path

from fastapi.testclient import TestClient

from app import attachments, config, db


def _client(tmp_env):
    from app.main import app
    return TestClient(app)


def _dm(client, conn, slug="researcher"):
    db.create_agent(conn, slug, slug.capitalize(), provider="claude")
    client.get(f"/dm/{slug}", follow_redirects=False)
    return db.list_channels(conn, kind="dm")[0]


def _upload(client, name="note.txt", body=b"hello"):
    r = client.post("/api/uploads", files={"files": (name, body, "text/plain")})
    assert r.status_code == 200, r.text
    return r.json()


# --- 업로드 -----------------------------------------------------------------

def test_upload_returns_saved_paths(tmp_env, completed_setup):
    with _client(tmp_env) as client:
        saved = _upload(client, "계획.txt", b"a" * 10)
        assert len(saved) == 1
        path = Path(saved[0]["path"])
        assert path.is_file() and path.read_bytes() == b"a" * 10
        assert path.parent == config.UPLOAD_DIR.resolve() or \
            path.is_relative_to(config.UPLOAD_DIR.resolve())
        assert saved[0]["size"] == 10 and "계획" in saved[0]["name"]


def test_upload_rejects_oversized_files(tmp_env, completed_setup, monkeypatch):
    monkeypatch.setattr(config, "MAX_UPLOAD_MB", 0)
    with _client(tmp_env) as client:
        r = client.post("/api/uploads",
                        files={"files": ("big.bin", b"x" * 1024, "application/octet-stream")})
        assert r.status_code == 413


# --- 첨부가 에이전트에게 전달되는가 ------------------------------------------

def test_message_attachments_reach_the_agent_prompt(tmp_env, completed_setup):
    with _client(tmp_env) as client:
        conn = db.get_conn()
        channel = _dm(client, conn)
        saved = _upload(client)

        r = client.post(f"/api/channels/{channel['id']}/messages", json={
            "body": "이 파일 좀 봐줘", "attachments": [saved[0]["path"]]})
        assert r.status_code == 202
        job = db.get_job(conn, r.json()["job_id"])
        assert attachments.HEADER in job["prompt"]
        assert saved[0]["path"] in job["prompt"]

        # 사용자 발화에도 남아 화면에서 칩으로 보여줄 수 있어야 한다
        user_msg = db.get_message(conn, r.json()["user_message_id"])
        assert attachments.of_message(user_msg) == [saved[0]["path"]]


def test_attachments_work_without_an_agent_too(tmp_env, completed_setup):
    """멤버가 없는 채널은 provider 로 바로 실행된다 — 그 경로에도 첨부가 붙어야 한다."""
    with _client(tmp_env) as client:
        conn = db.get_conn()
        cid = db.create_channel(conn, "팀")
        saved = _upload(client)
        r = client.post(f"/api/channels/{cid}/messages", json={
            "body": "정리해줘", "provider": "claude",
            "attachments": [saved[0]["path"]]})
        assert r.status_code == 202
        job = db.get_job(conn, r.json()["job_id"])
        assert saved[0]["path"] in job["prompt"]


def test_paths_outside_the_upload_dir_are_dropped(tmp_env, completed_setup):
    """경로는 클라이언트가 되돌려 보내는 값이다 — 임의 파일을 읽히면 안 된다."""
    secret = Path(tmp_env) / "secret.txt"
    secret.write_text("token")
    with _client(tmp_env) as client:
        conn = db.get_conn()
        channel = _dm(client, conn)
        r = client.post(f"/api/channels/{channel['id']}/messages", json={
            "body": "봐줘", "attachments": [str(secret), "/etc/passwd"]})
        assert r.status_code == 202
        job = db.get_job(conn, r.json()["job_id"])
        assert "secret.txt" not in job["prompt"] and "/etc/passwd" not in job["prompt"]
        assert attachments.HEADER not in job["prompt"]


def test_messages_without_attachments_are_unchanged(tmp_env, completed_setup):
    with _client(tmp_env) as client:
        conn = db.get_conn()
        channel = _dm(client, conn)
        r = client.post(f"/api/channels/{channel['id']}/messages",
                        json={"body": "그냥 질문"})
        job = db.get_job(conn, r.json()["job_id"])
        assert attachments.HEADER not in job["prompt"]
        assert attachments.of_message(
            db.get_message(conn, r.json()["user_message_id"])) == []


# --- 작업 위치 ---------------------------------------------------------------

def test_channel_workdir_can_be_changed_after_creation(tmp_env, completed_setup,
                                                       monkeypatch):
    """예전에는 채널을 만들 때만 정할 수 있었다(ChannelUpdate 에 workdir 이 없었다)."""
    monkeypatch.setattr(config, "BROWSE_ROOT", tmp_env)
    folder = (Path(tmp_env) / "proj").resolve()
    folder.mkdir()
    with _client(tmp_env) as client:
        conn = db.get_conn()
        client.post("/workspaces/add", data={"value": str(folder)})
        cid = db.create_channel(conn, "팀")

        r = client.patch(f"/api/channels/{cid}", json={"workdir": str(folder)})
        assert r.status_code == 200
        assert db.get_channel(conn, cid)["workdir"] == str(folder)

        # 연동 해제(빈 문자열)도 가능해야 한다
        client.patch(f"/api/channels/{cid}", json={"workdir": ""})
        assert db.get_channel(conn, cid)["workdir"] is None

        # 등록되지 않은 경로는 거부한다
        assert client.patch(f"/api/channels/{cid}",
                            json={"workdir": "/etc"}).status_code == 400


def test_the_picked_workdir_beats_the_agents_fixed_one(tmp_env, completed_setup,
                                                      monkeypatch):
    """대화창에서 고른 폴더가 조용히 무시되던 문제 — 에이전트 고정값이 이겼다."""
    monkeypatch.setattr(config, "BROWSE_ROOT", tmp_env)
    picked = (Path(tmp_env) / "picked").resolve()
    pinned = (Path(tmp_env) / "pinned").resolve()
    picked.mkdir()
    pinned.mkdir()
    with _client(tmp_env) as client:
        conn = db.get_conn()
        client.post("/workspaces/add", data={"value": str(picked)})
        client.post("/workspaces/add", data={"value": str(pinned)})
        db.create_agent(conn, "researcher", "Researcher", provider="claude",
                        workdir=str(pinned))
        client.get("/dm/researcher", follow_redirects=False)
        channel = db.list_channels(conn, kind="dm")[0]

        r = client.post(f"/api/channels/{channel['id']}/messages",
                        json={"body": "여기서 해줘", "workdir": str(picked)})
        assert db.get_job(conn, r.json()["job_id"])["workdir"] == str(picked)

        # 안 고르면(생략) 기존 규칙대로 에이전트 고정값을 쓴다
        r = client.post(f"/api/channels/{channel['id']}/messages",
                        json={"body": "그냥 해줘"})
        assert db.get_job(conn, r.json()["job_id"])["workdir"] == str(pinned)


def test_unregistered_workdir_is_rejected(tmp_env, completed_setup):
    with _client(tmp_env) as client:
        conn = db.get_conn()
        channel = _dm(client, conn)
        r = client.post(f"/api/channels/{channel['id']}/messages",
                        json={"body": "여기서", "workdir": "/etc"})
        assert r.status_code == 400
