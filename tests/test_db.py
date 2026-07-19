from datetime import datetime, timedelta, timezone

from app import db


def _conn(tmp_env):
    from app import config
    return db.get_conn(config.DB_PATH)


def test_create_and_get_job(tmp_env):
    conn = _conn(tmp_env)
    job_id = db.create_job(conn, "테스트 프롬프트", "claude")
    job = db.get_job(conn, job_id)
    assert job["prompt"] == "테스트 프롬프트"
    assert job["provider"] == "claude"
    assert job["status"] == "queued"
    assert job["attempts"] == 0
    assert job["output"] == ""


def test_create_job_with_timeout(tmp_env):
    conn = _conn(tmp_env)
    job_id = db.create_job(conn, "p", "claude", timeout_sec=120)
    assert db.get_job(conn, job_id)["timeout_sec"] == 120


def test_update_and_append(tmp_env):
    conn = _conn(tmp_env)
    job_id = db.create_job(conn, "p", "antigravity")
    db.update_job(conn, job_id, status="running", session_id="s1")
    db.append_output(conn, job_id, "hello ")
    db.append_output(conn, job_id, "world")
    job = db.get_job(conn, job_id)
    assert job["status"] == "running"
    assert job["session_id"] == "s1"
    assert job["output"] == "hello world"


def test_claim_next_job_picks_queued_and_marks_running(tmp_env):
    conn = _conn(tmp_env)
    a = db.create_job(conn, "first", "claude")
    db.create_job(conn, "second", "claude")
    job = db.claim_next_job(conn)
    assert job["id"] == a
    assert job["status"] == "running"
    assert job["attempts"] == 1


def test_claim_next_job_skips_future_rate_limited(tmp_env):
    conn = _conn(tmp_env)
    job_id = db.create_job(conn, "p", "claude")
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(timespec="seconds")
    db.update_job(conn, job_id, status="rate_limited", resume_at=future)
    assert db.claim_next_job(conn) is None


def test_claim_next_job_resumes_past_rate_limited(tmp_env):
    conn = _conn(tmp_env)
    job_id = db.create_job(conn, "p", "claude")
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(timespec="seconds")
    db.update_job(conn, job_id, status="rate_limited", resume_at=past)
    job = db.claim_next_job(conn)
    assert job["id"] == job_id
    assert job["status"] == "running"


def test_recover_running(tmp_env):
    conn = _conn(tmp_env)
    job_id = db.create_job(conn, "p", "claude")
    db.update_job(conn, job_id, status="running")
    db.recover_running(conn)
    assert db.get_job(conn, job_id)["status"] == "queued"


def test_recover_running_fails_council_jobs_instead_of_requeuing(tmp_env):
    conn = _conn(tmp_env)
    job_id = db.create_job(conn, "p", "council")
    db.update_job(conn, job_id, status="running", output="partial progress")
    db.recover_running(conn)
    job = db.get_job(conn, job_id)
    assert job["status"] == "failed"
    assert job["error"] == "interrupted: server restarted"
    # 부분 출력은 그대로 보존 (재시도로 인한 중복 누적을 막는 것이 목적)
    assert job["output"] == "partial progress"


def test_usage_counts_and_limit_status(tmp_env):
    conn = _conn(tmp_env)
    db.log_usage(conn, "claude", 1.5, "ok")
    db.log_usage(conn, "claude", 2.0, "rate_limited")
    db.log_usage(conn, "antigravity", 0.5, "ok")
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat(timespec="seconds")
    counts = db.usage_counts(conn, since)
    assert counts["claude"]["ok"] == 1
    assert counts["claude"]["rate_limited"] == 1
    assert counts["antigravity"]["ok"] == 1

    job_id = db.create_job(conn, "p", "claude")
    future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(timespec="seconds")
    db.update_job(conn, job_id, status="rate_limited", resume_at=future)
    status = db.limit_status(conn)
    assert status["claude"] == future
    assert "antigravity" not in status
