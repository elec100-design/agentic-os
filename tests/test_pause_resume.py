"""일시정지 · 재개 · 중단 후 이어하기 — 오케스트레이터 의미론과 API.

설계 문서: docs/plan-sidebar-workflow-resume.md
"""
import pytest

from app import db, orchestrator


def _conn(tmp_env):
    from app import config
    return db.get_conn(config.DB_PATH)


def _running_project(conn, tasks_spec):
    """tasks_spec: [(seq, deps, provider)]"""
    pid = db.create_project(conn, "목표")
    db.update_project(conn, pid, status="running")
    for seq, deps, provider in tasks_spec:
        db.create_task(conn, pid, seq, f"태스크{seq}", f"설명{seq}", "text",
                       provider, depends_on=",".join(str(d) for d in deps))
    return pid


def _advance(conn, pid):
    orchestrator._advance(conn, db.get_project(conn, pid))


# --- 일시정지 ----------------------------------------------------------------

def test_pause_keeps_inflight_task_running_until_it_finishes(tmp_env):
    """일시정지는 진행 중 태스크를 죽이지 않는다 — 다 빠진 뒤에야 paused."""
    conn = _conn(tmp_env)
    pid = _running_project(conn, [(1, [], "claude"), (2, [], "grok")])
    _advance(conn, pid)
    t1, t2 = db.list_tasks(conn, pid)
    assert t1["status"] == "queued" and t2["status"] == "queued"

    orchestrator.pause_project(conn, pid)
    project = db.get_project(conn, pid)
    # 인플라이트가 남아 있는 동안은 running을 유지해야 결과 회수(_sync_tasks)가 돈다.
    assert project["status"] == "running" and project["pause_reason"] == "manual"

    db.update_job(conn, t1["job_id"], status="done", output="결과1")
    _advance(conn, pid)
    assert db.get_project(conn, pid)["status"] == "running"
    assert db.get_task(conn, t1["id"])["status"] == "done"

    db.update_job(conn, t2["job_id"], status="done", output="결과2")
    _advance(conn, pid)
    # 태스크가 전부 끝났으므로 이 프로젝트는 done (남은 게 있었다면 paused).
    assert db.get_project(conn, pid)["status"] == "done"


def test_pause_blocks_new_dispatch_and_settles_to_paused(tmp_env):
    conn = _conn(tmp_env)
    pid = _running_project(conn, [(1, [], "claude"), (2, [1], "grok")])
    _advance(conn, pid)
    t1 = db.list_tasks(conn, pid)[0]

    orchestrator.pause_project(conn, pid)
    db.update_job(conn, t1["job_id"], status="done", output="결과1")
    _advance(conn, pid)

    project = db.get_project(conn, pid)
    assert project["status"] == "paused"
    # 의존성이 풀렸어도 새 태스크는 디스패치되지 않는다.
    assert db.list_tasks(conn, pid)[1]["status"] == "pending"


def test_pause_rejects_non_running_project(tmp_env):
    conn = _conn(tmp_env)
    pid = _running_project(conn, [(1, [], "claude")])
    db.update_project(conn, pid, status="plan_ready")
    with pytest.raises(ValueError):
        orchestrator.pause_project(conn, pid)


# --- 재개 --------------------------------------------------------------------

def test_resume_continues_without_rerunning_done_tasks(tmp_env):
    conn = _conn(tmp_env)
    pid = _running_project(conn, [(1, [], "claude"), (2, [1], "grok")])
    _advance(conn, pid)
    t1 = db.list_tasks(conn, pid)[0]
    orchestrator.pause_project(conn, pid)
    db.update_job(conn, t1["job_id"], status="done", output="결과1")
    _advance(conn, pid)
    assert db.get_project(conn, pid)["status"] == "paused"
    done_job_id = db.get_task(conn, t1["id"])["job_id"]

    orchestrator.resume_project(conn, pid)
    project = db.get_project(conn, pid)
    assert project["status"] == "running" and project["pause_reason"] is None

    _advance(conn, pid)
    t1, t2 = db.list_tasks(conn, pid)
    assert t1["status"] == "done" and t1["job_id"] == done_job_id  # 재실행 안 함
    assert t2["status"] == "queued"  # 남은 태스크만 이어서 실행


def test_resume_requires_failed_tasks_to_be_handled_first(tmp_env):
    conn = _conn(tmp_env)
    pid = _running_project(conn, [(1, [], "claude")])
    _advance(conn, pid)
    t1 = db.list_tasks(conn, pid)[0]
    db.update_job(conn, t1["job_id"], status="failed", error="터짐")
    _advance(conn, pid)
    assert db.get_project(conn, pid)["status"] == "paused"

    with pytest.raises(ValueError):
        orchestrator.resume_project(conn, pid)

    # 재시도로 되돌리면(=편집 후 다시 실행) 그 자체로 실행이 재개된다.
    orchestrator.retry_task(conn, t1["id"])
    assert db.get_project(conn, pid)["status"] == "running"


def test_resume_rejects_project_that_is_not_paused(tmp_env):
    conn = _conn(tmp_env)
    pid = _running_project(conn, [(1, [], "claude")])
    with pytest.raises(ValueError):
        orchestrator.resume_project(conn, pid)


# --- 실행 세션(workflow_runs) 기록 ------------------------------------------

def test_dispatch_records_task_run_and_marks_done(tmp_env):
    conn = _conn(tmp_env)
    pid = _running_project(conn, [(1, [], "claude")])
    _advance(conn, pid)
    t1 = db.list_tasks(conn, pid)[0]

    run = db.get_resumable_run(conn, pid)
    assert run is not None and run["status"] == "running"
    task_run = db.get_active_task_run(conn, t1["id"], run["id"])
    assert task_run["status"] == "running" and task_run["agent"] == "claude"

    db.update_job(conn, t1["job_id"], status="done", output="결과1")
    _advance(conn, pid)
    task_run = db.get_task_run(conn, task_run["id"])
    assert task_run["status"] == "done" and "결과1" in task_run["output"]
    # 프로젝트가 끝나면 세션도 마감돼 재개 대상에서 빠진다.
    assert db.get_resumable_run(conn, pid) is None


def test_failed_task_run_keeps_error_for_diagnosis(tmp_env):
    conn = _conn(tmp_env)
    pid = _running_project(conn, [(1, [], "claude")])
    _advance(conn, pid)
    t1 = db.list_tasks(conn, pid)[0]
    run_id = db.get_resumable_run(conn, pid)["id"]
    db.update_job(conn, t1["job_id"], status="failed", error="터짐",
                  output="여기까지 하다 멈춤")
    _advance(conn, pid)

    task_run = db.get_active_task_run(conn, t1["id"], run_id)
    assert task_run["status"] == "failed"
    assert "여기까지" in task_run["output"] and "터짐" in task_run["error"]


def test_server_restart_marks_runs_interrupted_and_resumes_same_session(tmp_env):
    """중단 이력이 남고, 재개는 같은 세션을 이어 쓰며 완료 태스크는 건너뛴다."""
    conn = _conn(tmp_env)
    pid = _running_project(conn, [(1, [], "claude"), (2, [1], "grok")])
    _advance(conn, pid)
    t1 = db.list_tasks(conn, pid)[0]
    run_id = db.get_resumable_run(conn, pid)["id"]
    db.update_job(conn, t1["job_id"], status="done", output="결과1")
    _advance(conn, pid)  # t1 done, t2 디스패치
    t2 = db.list_tasks(conn, pid)[1]
    db.update_job(conn, t2["job_id"], status="running")
    _advance(conn, pid)

    # 서버가 죽었다 살아난다 — worker_loop 기동 시의 복구 경로.
    db.recover_running(conn)

    assert db.get_workflow_run(conn, run_id)["status"] == "interrupted"
    t2_runs = db.list_task_runs(conn, t2["id"])
    assert t2_runs[-1]["status"] == "interrupted"
    assert db.get_task_run(conn, db.list_task_runs(conn, t1["id"])[-1]["id"])["status"] == "done"
    # 완료된 t1은 재개 대상에서 빠지고 t2만 남는다.
    pending = [t["id"] for t in db.get_pending_tasks_for_run(conn, run_id)]
    assert pending == [t2["id"]]

    # 재개하면 같은 세션을 이어 쓰고, 재개된 실행은 새 시도로 기록된다.
    _advance(conn, pid)
    assert db.get_resumable_run(conn, pid)["id"] == run_id
    db.update_job(conn, t2["job_id"], status="running")
    _advance(conn, pid)
    t2_runs = db.list_task_runs(conn, t2["id"])
    assert [r["attempt"] for r in t2_runs] == [1, 2]
    assert t2_runs[-1]["status"] == "running"


def test_cancel_closes_run_session(tmp_env):
    conn = _conn(tmp_env)
    pid = _running_project(conn, [(1, [], "claude")])
    _advance(conn, pid)
    assert db.get_resumable_run(conn, pid) is not None
    orchestrator.cancel_project(conn, pid)
    assert db.get_resumable_run(conn, pid) is None
