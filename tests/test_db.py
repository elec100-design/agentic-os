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


def test_migrate_adds_node_position_columns(tmp_env):
    """다이어그램 편집기 이전에 만들어진 DB에도 pos_x/pos_y가 붙는다."""
    import sqlite3
    path = tmp_env / "old.db"
    old = sqlite3.connect(path)
    old.execute("""CREATE TABLE tasks (
      id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL,
      seq INTEGER NOT NULL, title TEXT NOT NULL,
      description TEXT NOT NULL DEFAULT '', task_type TEXT NOT NULL DEFAULT 'text',
      provider TEXT NOT NULL, depends_on TEXT NOT NULL DEFAULT '',
      status TEXT NOT NULL DEFAULT 'pending', job_id INTEGER,
      output TEXT NOT NULL DEFAULT '', artifact_path TEXT, error TEXT,
      created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT)""")
    old.execute("INSERT INTO tasks (project_id, seq, title, provider, created_at) "
                "VALUES (1, 1, '옛 태스크', 'claude', '2026-01-01T00:00:00+00:00')")
    old.commit()
    old.close()

    conn = db.get_conn(path)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(tasks)")}
    assert {"pos_x", "pos_y"} <= cols
    row = conn.execute("SELECT * FROM tasks").fetchone()
    assert row["title"] == "옛 태스크"      # 기존 데이터는 그대로
    assert row["pos_x"] is None


def test_migrate_adds_messages_created_task_id_column(tmp_env):
    """채널/메시지 도입 전 DB에도 messages.created_task_id가 붙고(멱등), 기존 행은 NULL."""
    import sqlite3
    path = tmp_env / "old.db"
    old = sqlite3.connect(path)
    old.execute("""CREATE TABLE channels (
      id INTEGER PRIMARY KEY AUTOINCREMENT, slug TEXT NOT NULL UNIQUE,
      title TEXT NOT NULL, topic TEXT NOT NULL DEFAULT '', workdir TEXT,
      default_provider TEXT, status TEXT NOT NULL DEFAULT 'active',
      created_at TEXT NOT NULL, updated_at TEXT)""")
    old.execute("""CREATE TABLE messages (
      id INTEGER PRIMARY KEY AUTOINCREMENT, channel_id INTEGER NOT NULL,
      parent_id INTEGER, root_id INTEGER, seq INTEGER NOT NULL, role TEXT NOT NULL,
      author TEXT NOT NULL DEFAULT '', body TEXT NOT NULL DEFAULT '',
      status TEXT NOT NULL DEFAULT 'done', provider TEXT, model TEXT,
      session_id TEXT, job_id INTEGER, reply_count INTEGER NOT NULL DEFAULT 0,
      last_reply_at TEXT, error TEXT, created_at TEXT NOT NULL,
      started_at TEXT, finished_at TEXT)""")
    old.execute("INSERT INTO channels (slug, title, created_at) "
                "VALUES ('old', '옛 채널', '2026-01-01T00:00:00+00:00')")
    old.execute("INSERT INTO messages (channel_id, seq, role, body, created_at) "
                "VALUES (1, 1, 'user', '옛 메시지', '2026-01-01T00:00:00+00:00')")
    old.commit()
    old.close()

    conn = db.get_conn(path)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(messages)")}
    assert "created_task_id" in cols
    row = conn.execute("SELECT * FROM messages").fetchone()
    assert row["body"] == "옛 메시지"       # 기존 데이터는 그대로
    assert row["created_task_id"] is None

    # 재접속(마이그레이션 재실행)해도 에러 없이 멱등하게 통과해야 한다.
    conn2 = db.get_conn(path)
    cols2 = {r["name"] for r in conn2.execute("PRAGMA table_info(messages)")}
    assert "created_task_id" in cols2


def test_create_and_get_test_goal(tmp_env):
    conn = _conn(tmp_env)
    goal_id = db.create_test_goal(conn, "test goal")
    goal = db.get_test_goal(conn, goal_id)
    assert goal["name"] == "test goal"
    assert goal["status"] == "pending"
    assert goal["result"] is None


def test_update_test_goal_transitions_status(tmp_env):
    conn = _conn(tmp_env)
    goal_id = db.create_test_goal(conn, "test goal")
    db.update_test_goal(conn, goal_id, status="running")
    assert db.get_test_goal(conn, goal_id)["status"] == "running"
    db.update_test_goal(conn, goal_id, status="done", result="완료")
    goal = db.get_test_goal(conn, goal_id)
    assert goal["status"] == "done"
    assert goal["result"] == "완료"
