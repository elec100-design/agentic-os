from fastapi.testclient import TestClient

from app import config, db


def _client(tmp_env):
    from app.main import app
    return TestClient(app)


def test_board_page_renders(tmp_env):
    with _client(tmp_env) as client:
        r = client.get("/board")
        assert r.status_code == 200
        assert "Vision board" in r.text  # 기본 언어는 영어


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
