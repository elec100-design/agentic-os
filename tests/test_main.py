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
