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


def test_create_job_auto_routes(tmp_env):
    from app import config, db
    with _client(tmp_env) as client:
        client.post("/jobs", data={"prompt": "최신 뉴스 검색", "provider": "auto"},
                    follow_redirects=False)
    conn = db.get_conn(config.DB_PATH)
    assert db.list_jobs(conn)[0]["provider"] == "grok"


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
