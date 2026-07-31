from app import db


def _conn(tmp_env):
    from app import config
    return db.get_conn(config.DB_PATH)


def _make_project_with_tasks(conn, n=3):
    project_id = db.create_project(conn, "goal")
    task_ids = [
        db.create_task(conn, project_id, seq, f"t{seq}", "", "text", "claude")
        for seq in range(1, n + 1)
    ]
    return project_id, task_ids


def test_migration_creates_new_tables_idempotently(tmp_env):
    from app import config
    conn = db.get_conn(config.DB_PATH)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(workflow_runs)")}
    assert {"project_id", "status", "started_at", "updated_at", "heartbeat_at"} <= cols
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(task_runs)")}
    assert {"task_id", "run_id", "status", "attempt", "output", "agent",
            "error", "finished_at"} <= cols
    conn.close()

    # 같은 DB 파일에 재연결(=재기동 시뮬레이션)해도 안전해야 한다.
    conn2 = db.get_conn(config.DB_PATH)
    conn2.execute("SELECT 1 FROM workflow_runs")
    conn2.execute("SELECT 1 FROM task_runs")
    conn2.close()


def test_migration_preserves_existing_data(tmp_env):
    from app import config
    conn = _conn(tmp_env)
    project_id, task_ids = _make_project_with_tasks(conn)
    run_id = db.create_workflow_run(conn, project_id)
    tr_id = db.create_task_run(conn, task_ids[0], run_id)
    db.mark_task_run_done(conn, tr_id)
    conn.close()

    # DB 파일을 다시 열어(재기동 시뮬레이션) 기존 데이터가 그대로인지 확인.
    conn2 = db.get_conn(config.DB_PATH)
    assert db.get_project(conn2, project_id) is not None
    assert len(db.list_tasks(conn2, project_id)) == 3
    run = db.get_workflow_run(conn2, run_id)
    assert run["status"] == "running"
    task_run = db.get_task_run(conn2, tr_id)
    assert task_run["status"] == "done"


def test_workflow_run_lifecycle(tmp_env):
    conn = _conn(tmp_env)
    project_id, _ = _make_project_with_tasks(conn, n=1)
    run_id = db.create_workflow_run(conn, project_id)
    run = db.get_workflow_run(conn, run_id)
    assert run["status"] == "running"
    assert run["project_id"] == project_id

    db.heartbeat_workflow_run(conn, run_id)
    run = db.get_workflow_run(conn, run_id)
    assert run["heartbeat_at"] is not None

    db.update_workflow_run(conn, run_id, status="paused")
    assert db.get_workflow_run(conn, run_id)["status"] == "paused"


def test_get_resumable_run_prefers_latest_active_session(tmp_env):
    conn = _conn(tmp_env)
    project_id, _ = _make_project_with_tasks(conn, n=1)

    old_run = db.create_workflow_run(conn, project_id)
    db.update_workflow_run(conn, old_run, status="completed")
    assert db.get_resumable_run(conn, project_id) is None

    active_run = db.create_workflow_run(conn, project_id)
    resumable = db.get_resumable_run(conn, project_id)
    assert resumable["id"] == active_run

    db.update_workflow_run(conn, active_run, status="paused")
    resumable = db.get_resumable_run(conn, project_id)
    assert resumable["id"] == active_run
    assert resumable["status"] == "paused"


def test_get_pending_tasks_for_run_skips_completed(tmp_env):
    conn = _conn(tmp_env)
    project_id, task_ids = _make_project_with_tasks(conn, n=3)
    run_id = db.create_workflow_run(conn, project_id)

    # 아직 아무 시도도 없으면 전부 미완료.
    pending = db.get_pending_tasks_for_run(conn, run_id)
    assert [t["id"] for t in pending] == task_ids

    tr1 = db.create_task_run(conn, task_ids[0], run_id)
    db.mark_task_run_done(conn, tr1)

    tr2 = db.create_task_run(conn, task_ids[1], run_id)
    db.mark_task_run_failed(conn, tr2, error="boom")

    pending = db.get_pending_tasks_for_run(conn, run_id)
    pending_ids = [t["id"] for t in pending]
    assert task_ids[0] not in pending_ids  # done은 제외
    assert task_ids[1] in pending_ids      # failed는 여전히 미완료 취급
    assert task_ids[2] in pending_ids      # 시도조차 없던 태스크


def test_create_task_run_increments_attempt_across_sessions(tmp_env):
    conn = _conn(tmp_env)
    project_id, task_ids = _make_project_with_tasks(conn, n=1)
    task_id = task_ids[0]

    run1 = db.create_workflow_run(conn, project_id)
    tr1 = db.create_task_run(conn, task_id, run1, agent="claude")
    assert db.get_task_run(conn, tr1)["attempt"] == 1
    db.mark_task_run_failed(conn, tr1, error="rate limited")

    # 세션이 끊기고 새 세션에서 재개해도 시도 번호는 이어진다.
    run2 = db.create_workflow_run(conn, project_id)
    tr2 = db.create_task_run(conn, task_id, run2, agent="codex")
    assert db.get_task_run(conn, tr2)["attempt"] == 2

    history = db.list_task_runs(conn, task_id)
    assert [r["attempt"] for r in history] == [1, 2]
    assert [r["status"] for r in history] == ["failed", "running"]


def test_append_task_run_output_accumulates(tmp_env):
    conn = _conn(tmp_env)
    project_id, task_ids = _make_project_with_tasks(conn, n=1)
    run_id = db.create_workflow_run(conn, project_id)
    tr_id = db.create_task_run(conn, task_ids[0], run_id)

    db.append_task_run_output(conn, tr_id, "hello ")
    db.append_task_run_output(conn, tr_id, "world")
    assert db.get_task_run(conn, tr_id)["output"] == "hello world"


def test_mark_interrupted_task_runs_and_workflow_runs(tmp_env):
    conn = _conn(tmp_env)
    project_id, task_ids = _make_project_with_tasks(conn, n=2)
    run_id = db.create_workflow_run(conn, project_id)
    tr_id = db.create_task_run(conn, task_ids[0], run_id)

    # 서버가 죽은 상황을 흉내: running 상태로 남는다.
    assert db.get_task_run(conn, tr_id)["status"] == "running"
    assert db.get_workflow_run(conn, run_id)["status"] == "running"

    db.mark_interrupted_task_runs(conn)
    db.mark_interrupted_workflow_runs(conn)

    task_run = db.get_task_run(conn, tr_id)
    assert task_run["status"] == "interrupted"
    assert task_run["error"]
    assert task_run["finished_at"] is not None

    run = db.get_workflow_run(conn, run_id)
    assert run["status"] == "interrupted"

    # 중단된 세션도 여전히 재개 대상으로 조회되어야 한다.
    resumable = db.get_resumable_run(conn, project_id)
    assert resumable["id"] == run_id

    # 완료되지 않은(interrupted) 시도가 있던 태스크는 여전히 pending으로 잡혀
    # 재개 시 다시 시도할 수 있어야 한다.
    pending_ids = [t["id"] for t in db.get_pending_tasks_for_run(conn, run_id)]
    assert task_ids[0] in pending_ids
    assert task_ids[1] in pending_ids


def test_get_active_task_run_returns_latest_for_session(tmp_env):
    conn = _conn(tmp_env)
    project_id, task_ids = _make_project_with_tasks(conn, n=1)
    run_id = db.create_workflow_run(conn, project_id)
    task_id = task_ids[0]

    assert db.get_active_task_run(conn, task_id, run_id) is None

    tr1 = db.create_task_run(conn, task_id, run_id)
    db.mark_task_run_failed(conn, tr1)
    tr2 = db.create_task_run(conn, task_id, run_id)

    active = db.get_active_task_run(conn, task_id, run_id)
    assert active["id"] == tr2
