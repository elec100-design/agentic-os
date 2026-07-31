import asyncio
import json
import re

import pytest

from app import config, db, orchestrator, worker
from app.orchestrator import NODE_H, NODE_W, PlanError, layout_graph, parse_plan
from app.providers import MEDIA, PROVIDERS, ParseResult


def _plan_text(tasks, title="테스트 프로젝트"):
    return "계획입니다.\n```json\n" + json.dumps(
        {"title": title, "tasks": tasks}, ensure_ascii=False) + "\n```"


def _task(tid, deps=(), ttype="text", agent="claude", **over):
    t = {"id": tid, "title": f"태스크{tid}", "description": f"태스크 {tid} 수행",
         "type": ttype, "agent": agent, "depends_on": list(deps)}
    t.update(over)
    return t


# --- parse_plan ---------------------------------------------------------------

def test_parse_plan_valid():
    plan = parse_plan(_plan_text([_task(1), _task(2, deps=[1])]))
    assert plan["title"] == "테스트 프로젝트"
    assert [t["id"] for t in plan["tasks"]] == [1, 2]
    assert plan["tasks"][1]["depends_on"] == [1]
    assert plan["tasks"][0]["provider"] == "claude"


def test_parse_plan_brace_fallback_without_fence():
    raw = "서두 텍스트 " + json.dumps({"title": "t", "tasks": [_task(1)]})
    plan = parse_plan(raw)
    assert len(plan["tasks"]) == 1


def test_parse_plan_media_forces_media_provider():
    plan = parse_plan(_plan_text([_task(1, ttype="image", agent="claude")]))
    assert plan["tasks"][0]["provider"] == MEDIA


def test_parse_plan_auto_agent_resolved():
    plan = parse_plan(_plan_text([_task(1, agent="auto")]))
    assert plan["tasks"][0]["provider"] in PROVIDERS


def test_parse_plan_rejects_bad_json():
    with pytest.raises(PlanError):
        parse_plan("```json\n{깨진 json}\n```")


def test_parse_plan_rejects_no_json():
    with pytest.raises(PlanError):
        parse_plan("JSON 없이 그냥 텍스트")


def test_parse_plan_rejects_cycle():
    with pytest.raises(PlanError, match="순환"):
        parse_plan(_plan_text([_task(1, deps=[2]), _task(2, deps=[1])]))


def test_parse_plan_rejects_self_dep():
    with pytest.raises(PlanError):
        parse_plan(_plan_text([_task(1, deps=[1])]))


def test_parse_plan_rejects_unknown_agent():
    with pytest.raises(PlanError, match="agent"):
        parse_plan(_plan_text([_task(1, agent="gpt9")]))


def test_parse_plan_rejects_unknown_dep():
    with pytest.raises(PlanError):
        parse_plan(_plan_text([_task(1, deps=[99])]))


def test_parse_plan_rejects_too_many(monkeypatch):
    monkeypatch.setattr(config, "ORCH_MAX_TASKS", 2)
    with pytest.raises(PlanError):
        parse_plan(_plan_text([_task(1), _task(2), _task(3)]))


def test_parse_plan_rejects_dup_id():
    with pytest.raises(PlanError):
        parse_plan(_plan_text([_task(1), _task(1)]))


# --- 계획 단계 ----------------------------------------------------------------

def _conn(tmp_env):
    return db.get_conn(config.DB_PATH)


def test_start_project_queues_plan_job(tmp_env):
    conn = _conn(tmp_env)
    pid = orchestrator.start_project(conn, "블로그를 만들어줘")
    project = db.get_project(conn, pid)
    assert project["status"] == "planning"
    assert project["planner"] in PROVIDERS
    job = db.get_job(conn, project["plan_job_id"])
    assert job is not None
    assert "블로그를 만들어줘" in job["prompt"]


def test_start_project_with_explicit_planner_and_model(tmp_env, monkeypatch):
    conn = _conn(tmp_env)
    monkeypatch.setattr(orchestrator.models, "is_valid_model",
                        lambda provider, model: True)
    pid = orchestrator.start_project(conn, "목표", workdir="/tmp/work",
                                     planner="grok", model="gpt-5")
    project = db.get_project(conn, pid)
    assert project["planner"] == "grok"
    assert project["planner_model"] == "gpt-5"
    assert project["workdir"] == "/tmp/work"
    job = db.get_job(conn, project["plan_job_id"])
    assert job["provider"] == "grok"
    assert job["model"] == "gpt-5"


def test_start_project_invalid_model_falls_back_to_none(tmp_env, monkeypatch):
    conn = _conn(tmp_env)
    monkeypatch.setattr(orchestrator.models, "is_valid_model",
                        lambda provider, model: False)
    pid = orchestrator.start_project(conn, "목표", planner="grok", model="bogus")
    project = db.get_project(conn, pid)
    assert project["planner"] == "grok"
    assert project["planner_model"] is None
    job = db.get_job(conn, project["plan_job_id"])
    assert job["model"] is None


def test_start_project_unknown_planner_falls_back_to_auto(tmp_env):
    conn = _conn(tmp_env)
    pid = orchestrator.start_project(conn, "목표", planner="not-a-real-agent")
    project = db.get_project(conn, pid)
    assert project["planner"] in PROVIDERS


def test_advance_planning_instantiates_tasks(tmp_env):
    conn = _conn(tmp_env)
    pid = orchestrator.start_project(conn, "목표")
    project = db.get_project(conn, pid)
    db.update_job(conn, project["plan_job_id"], status="done",
                  output=_plan_text([_task(1), _task(2, deps=[1])]))
    orchestrator._advance(conn, project)
    project = db.get_project(conn, pid)
    assert project["status"] == "plan_ready"
    assert project["title"] == "테스트 프로젝트"
    tasks = db.list_tasks(conn, pid)
    assert [t["seq"] for t in tasks] == [1, 2]
    assert tasks[1]["depends_on"] == "1"


def test_advance_planning_retries_once_then_fails(tmp_env):
    conn = _conn(tmp_env)
    pid = orchestrator.start_project(conn, "목표")
    project = db.get_project(conn, pid)
    first_job = project["plan_job_id"]
    db.update_job(conn, first_job, status="done", output="JSON 아님")
    orchestrator._advance(conn, project)

    project = db.get_project(conn, pid)
    assert project["status"] == "planning"          # 자동 1회 재시도
    assert project["plan_job_id"] != first_job
    retry_job = db.get_job(conn, project["plan_job_id"])
    assert "이전 시도 오류" in retry_job["prompt"]

    db.update_job(conn, project["plan_job_id"], status="done", output="여전히 아님")
    orchestrator._advance(conn, project)
    assert db.get_project(conn, pid)["status"] == "plan_failed"


def test_advance_planning_plan_job_failed(tmp_env):
    conn = _conn(tmp_env)
    pid = orchestrator.start_project(conn, "목표")
    project = db.get_project(conn, pid)
    db.update_job(conn, project["plan_job_id"], status="failed", error="boom")
    orchestrator._advance(conn, project)
    project = db.get_project(conn, pid)
    assert project["status"] == "plan_failed"
    assert "boom" in project["error"]


# --- 실행 단계 ----------------------------------------------------------------

def _running_project(conn, tasks_spec):
    """tasks_spec: [(seq, deps, provider, task_type)]"""
    pid = db.create_project(conn, "목표")
    db.update_project(conn, pid, status="running")
    for seq, deps, provider, ttype in tasks_spec:
        db.create_task(conn, pid, seq, f"태스크{seq}", f"설명{seq}", ttype,
                       provider, depends_on=",".join(str(d) for d in deps))
    return pid


def test_advance_dispatches_ready_tasks_only(tmp_env):
    conn = _conn(tmp_env)
    pid = _running_project(conn, [(1, [], "claude", "text"),
                                  (2, [1], "grok", "text")])
    orchestrator._advance(conn, db.get_project(conn, pid))
    t1, t2 = db.list_tasks(conn, pid)
    assert t1["status"] == "queued" and t1["job_id"]
    assert t2["status"] == "pending" and t2["job_id"] is None
    job = db.get_job(conn, t1["job_id"])
    assert "설명1" in job["prompt"] and job["provider"] == "claude"


def test_advance_respects_inflight_cap(tmp_env, monkeypatch):
    monkeypatch.setattr(config, "ORCH_MAX_INFLIGHT", 2)
    conn = _conn(tmp_env)
    pid = _running_project(conn, [(1, [], "claude", "text"),
                                  (2, [], "grok", "text"),
                                  (3, [], "hermes", "text")])
    orchestrator._advance(conn, db.get_project(conn, pid))
    statuses = [t["status"] for t in db.list_tasks(conn, pid)]
    assert statuses.count("queued") == 2
    assert statuses.count("pending") == 1


def test_advance_passes_upstream_output(tmp_env):
    conn = _conn(tmp_env)
    pid = _running_project(conn, [(1, [], "claude", "text"),
                                  (2, [1], "grok", "text")])
    orchestrator._advance(conn, db.get_project(conn, pid))
    t1 = db.list_tasks(conn, pid)[0]
    db.update_job(conn, t1["job_id"], status="done", output="상류 결과물",
                  finished_at=db.now_iso())
    orchestrator._advance(conn, db.get_project(conn, pid))
    t1, t2 = db.list_tasks(conn, pid)
    assert t1["status"] == "done" and t1["output"] == "상류 결과물"
    assert t2["status"] == "queued"
    job2 = db.get_job(conn, t2["job_id"])
    assert "상류 결과물" in job2["prompt"]


def test_advance_clips_upstream(tmp_env, monkeypatch):
    monkeypatch.setattr(config, "ORCH_UPSTREAM_CLIP_CHARS", 10)
    conn = _conn(tmp_env)
    pid = _running_project(conn, [(1, [], "claude", "text"),
                                  (2, [1], "grok", "text")])
    orchestrator._advance(conn, db.get_project(conn, pid))
    t1 = db.list_tasks(conn, pid)[0]
    db.update_job(conn, t1["job_id"], status="done", output="가" * 100)
    orchestrator._advance(conn, db.get_project(conn, pid))
    job2 = db.get_job(conn, db.list_tasks(conn, pid)[1]["job_id"])
    assert "가" * 100 not in job2["prompt"]
    assert "길이 제한" in job2["prompt"]


def test_advance_failure_pauses_project(tmp_env):
    conn = _conn(tmp_env)
    pid = _running_project(conn, [(1, [], "claude", "text")])
    orchestrator._advance(conn, db.get_project(conn, pid))
    t1 = db.list_tasks(conn, pid)[0]
    db.update_job(conn, t1["job_id"], status="failed", error="터짐")
    orchestrator._advance(conn, db.get_project(conn, pid))
    assert db.list_tasks(conn, pid)[0]["status"] == "failed"
    assert db.get_project(conn, pid)["status"] == "paused"


def test_retry_task_resumes_project(tmp_env):
    conn = _conn(tmp_env)
    pid = _running_project(conn, [(1, [], "claude", "text")])
    orchestrator._advance(conn, db.get_project(conn, pid))
    t1 = db.list_tasks(conn, pid)[0]
    db.update_job(conn, t1["job_id"], status="failed", error="터짐")
    orchestrator._advance(conn, db.get_project(conn, pid))

    orchestrator.retry_task(conn, t1["id"])
    t1 = db.get_task(conn, t1["id"])
    assert t1["status"] == "pending" and t1["job_id"] is None
    assert db.get_project(conn, pid)["status"] == "running"


def test_output_error_hint_flags_error_notice_and_empty(tmp_env):
    conn = _conn(tmp_env)
    pid = _running_project(conn, [(1, [], "claude", "text")])
    tid = db.list_tasks(conn, pid)[0]["id"]
    db.update_task(conn, tid, status="done", output="")
    assert "결과" in orchestrator.output_error_hint(db.get_task(conn, tid))
    db.update_task(conn, tid, output="jetski: no output produced — auto-denied.")
    assert orchestrator.output_error_hint(db.get_task(conn, tid))
    db.update_task(conn, tid, output="정상 산출물입니다")
    assert orchestrator.output_error_hint(db.get_task(conn, tid)) is None
    # 긴 산출물 안에 우연히 섞인 문구는 오탐이므로 검사하지 않는다
    db.update_task(conn, tid, output="가" * 3000 + " no output produced")
    assert orchestrator.output_error_hint(db.get_task(conn, tid)) is None


def test_retry_done_task_with_model_swap_and_instruction(tmp_env, monkeypatch):
    monkeypatch.setattr(orchestrator.models, "is_valid_model",
                        lambda provider, model: True)
    conn = _conn(tmp_env)
    pid = _running_project(conn, [(1, [], "claude", "text")])
    tid = db.list_tasks(conn, pid)[0]["id"]
    db.update_task(conn, tid, status="done", output="no output produced")
    db.update_project(conn, pid, status="done")

    orchestrator.retry_task(conn, tid, agent="grok", model="grok-4",
                            instruction="권한 필요한 명령은 쓰지 마라")
    task = db.get_task(conn, tid)
    assert task["status"] == "pending" and task["output"] == ""
    assert task["provider"] == "grok" and task["model"] == "grok-4"
    assert db.get_project(conn, pid)["status"] == "running"

    orchestrator._advance(conn, db.get_project(conn, pid))
    job = db.get_job(conn, db.get_task(conn, tid)["job_id"])
    assert job["provider"] == "grok" and job["model"] == "grok-4"
    assert "권한 필요한 명령은 쓰지 마라" in job["prompt"]


def test_retry_task_rejects_running_task(tmp_env):
    conn = _conn(tmp_env)
    pid = _running_project(conn, [(1, [], "claude", "text")])
    tid = db.list_tasks(conn, pid)[0]["id"]
    db.update_task(conn, tid, status="running")
    with pytest.raises(ValueError):
        orchestrator.retry_task(conn, tid)


def test_retry_cascade_resets_downstream_tasks(tmp_env):
    conn = _conn(tmp_env)
    pid = _running_project(conn, [(1, [], "claude", "text"),
                                  (2, [1], "grok", "text"),
                                  (3, [2], "claude", "text")])
    t1, t2, t3 = db.list_tasks(conn, pid)
    for t in (t1, t2, t3):
        db.update_task(conn, t["id"], status="done", output="결과")
    db.update_project(conn, pid, status="done")

    orchestrator.retry_task(conn, t1["id"], cascade=True)
    assert [t["status"] for t in db.list_tasks(conn, pid)] == ["pending"] * 3
    # cascade 없이는 후속 태스크의 기존 결과를 건드리지 않는다
    for t in (t1, t2, t3):
        db.update_task(conn, t["id"], status="done", output="결과")
    orchestrator.retry_task(conn, t1["id"])
    assert [t["status"] for t in db.list_tasks(conn, pid)] == [
        "pending", "done", "done"]


def test_dependent_seqs_walks_transitively(tmp_env):
    conn = _conn(tmp_env)
    pid = _running_project(conn, [(1, [], "claude", "text"),
                                  (2, [1], "grok", "text"),
                                  (3, [2], "claude", "text"),
                                  (4, [], "claude", "text")])
    tasks = db.list_tasks(conn, pid)
    assert orchestrator.dependent_seqs(tasks, 1) == {2, 3}
    assert orchestrator.dependent_seqs(tasks, 3) == set()


def test_advance_completes_project(tmp_env):
    conn = _conn(tmp_env)
    pid = _running_project(conn, [(1, [], "claude", "text")])
    orchestrator._advance(conn, db.get_project(conn, pid))
    t1 = db.list_tasks(conn, pid)[0]
    db.update_job(conn, t1["job_id"], status="done", output="끝")
    orchestrator._advance(conn, db.get_project(conn, pid))
    assert db.get_project(conn, pid)["status"] == "done"


def test_media_task_dispatch_and_artifact(tmp_env):
    conn = _conn(tmp_env)
    pid = _running_project(conn, [(1, [], MEDIA, "image")])
    orchestrator._advance(conn, db.get_project(conn, pid))
    t1 = db.list_tasks(conn, pid)[0]
    job = db.get_job(conn, t1["job_id"])
    assert job["provider"] == MEDIA
    assert job["model"] == "image"       # 미디어 종류는 model 컬럼으로 전달
    db.update_job(conn, t1["job_id"], status="done",
                  output="생성 완료\n/tmp/art/1_img.png")
    orchestrator._advance(conn, db.get_project(conn, pid))
    t1 = db.list_tasks(conn, pid)[0]
    assert t1["artifact_path"] == "/tmp/art/1_img.png"  # 마지막 줄 = 경로 계약


def test_cancel_project_cancels_jobs(tmp_env):
    conn = _conn(tmp_env)
    pid = _running_project(conn, [(1, [], "claude", "text")])
    orchestrator._advance(conn, db.get_project(conn, pid))
    t1 = db.list_tasks(conn, pid)[0]
    orchestrator.cancel_project(conn, pid)
    assert db.get_project(conn, pid)["status"] == "cancelled"
    job = db.get_job(conn, t1["job_id"])
    assert job["status"] == "failed" and job["error"] == "cancelled"


def test_replan_keeps_done_discards_rest(tmp_env):
    conn = _conn(tmp_env)
    pid = _running_project(conn, [(1, [], "claude", "text"),
                                  (2, [1], "grok", "text")])
    db.update_task(conn, db.list_tasks(conn, pid)[0]["id"], status="done",
                   output="완료된 결과")
    orchestrator.replan(conn, pid)
    project = db.get_project(conn, pid)
    assert project["status"] == "planning"
    tasks = db.list_tasks(conn, pid)
    assert len(tasks) == 1 and tasks[0]["status"] == "done"
    plan_job = db.get_job(conn, project["plan_job_id"])
    assert "완료된 결과" in plan_job["prompt"]


def test_approve_only_from_plan_ready(tmp_env):
    conn = _conn(tmp_env)
    pid = db.create_project(conn, "목표")
    with pytest.raises(ValueError):
        orchestrator.approve(conn, pid)
    db.update_project(conn, pid, status="plan_ready")
    orchestrator.approve(conn, pid)
    assert db.get_project(conn, pid)["status"] == "running"


# --- layout_graph -------------------------------------------------------------

def _rows(specs):
    """specs: [(seq, deps, status)] → layout_graph 입력용 dict 목록"""
    return [{"seq": s, "depends_on": ",".join(str(d) for d in deps),
             "status": status, "id": s, "title": f"t{s}", "provider": "claude",
             "task_type": "text", "artifact_path": None}
            for s, deps, status in specs]


def test_layout_linear_chain():
    g = layout_graph(_rows([(1, [], "done"), (2, [1], "running"),
                            (3, [2], "pending")]))
    xs = {n["seq"]: n["x"] for n in g["nodes"]}
    assert xs[1] < xs[2] < xs[3]
    assert len(g["edges"]) == 2
    done_edge = next(e for e in g["edges"] if e["from"] == 1)
    assert done_edge["done"] is True


def test_layout_diamond():
    g = layout_graph(_rows([(1, [], "done"), (2, [1], "pending"),
                            (3, [1], "pending"), (4, [2, 3], "pending")]))
    nodes = {n["seq"]: n for n in g["nodes"]}
    assert nodes[2]["x"] == nodes[3]["x"]          # 같은 레이어
    assert nodes[2]["y"] != nodes[3]["y"]          # 세로로 분리
    assert nodes[4]["x"] > nodes[2]["x"]
    assert len(g["edges"]) == 4
    assert g["width"] > 0 and g["height"] > 0


def test_layout_empty():
    g = layout_graph([])
    assert g["nodes"] == [] and g["edges"] == []


# --- E2E: 오케스트레이터 루프 + 워커 루프 (mock provider) ----------------------

class FakeProvider:
    """실제 CLI 대신 셸 명령을 실행하는 테스트용 어댑터."""
    def __init__(self, name, cmd):
        self.name = name
        self.cmd = cmd

    def build_command(self, prompt, session_id=None, model=None):
        return self.cmd

    def parse_output(self, stdout, stderr, exit_code):
        return ParseResult(text=stdout.strip())

    def detect_rate_limit(self, output, exit_code, now=None):
        return None


async def _wait_for(cond, timeout=10.0):
    for _ in range(int(timeout / 0.05)):
        if cond():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("조건이 제한 시간 안에 충족되지 않았습니다")


async def test_e2e_project_runs_to_completion(tmp_env, tmp_path):
    """계획 → 승인 → 의존성 순서 실행 → 완료까지, 두 루프가 실제로 굴린다."""
    plan = _plan_text([_task(1, agent="hermes"),
                       _task(2, deps=[1], agent="hermes"),
                       _task(3, deps=[2], agent="hermes")])
    plan_file = tmp_path / "plan.txt"
    plan_file.write_text(plan, encoding="utf-8")
    providers = {
        # 잔여 정보가 없으면 rank_cloud가 claude를 플래너로 뽑는다
        "claude": FakeProvider("claude", ["cat", str(plan_file)]),
        "hermes": FakeProvider("hermes", ["sh", "-c", "echo 태스크 결과"]),
    }
    conn = db.get_conn(config.DB_PATH)
    pid = orchestrator.start_project(conn, "3단계 텍스트 프로젝트")

    stop = asyncio.Event()
    loops = [
        asyncio.create_task(worker.worker_loop(stop, providers=providers,
                                               save=False, poll_sec=0.05)),
        asyncio.create_task(orchestrator.orchestrator_loop(stop, poll_sec=0.05)),
    ]
    try:
        await _wait_for(
            lambda: db.get_project(conn, pid)["status"] == "plan_ready")
        orchestrator.approve(conn, pid)
        await _wait_for(lambda: db.get_project(conn, pid)["status"] == "done")
    finally:
        stop.set()
        await asyncio.gather(*loops, return_exceptions=True)

    tasks = db.list_tasks(conn, pid)
    assert [t["status"] for t in tasks] == ["done"] * 3
    assert all(t["output"] == "태스크 결과" for t in tasks)
    # 의존성 순서: 1 → 2 → 3 (완료 시각이 단조 증가)
    finished = [t["finished_at"] for t in tasks]
    assert finished == sorted(finished)
    job3 = db.get_job(conn, tasks[2]["job_id"])
    assert "태스크 결과" in job3["prompt"]  # 상류 출력이 하류 프롬프트에 전달됨


# --- 다이어그램 편집 ------------------------------------------------------------

def _editable_project(conn, tasks_spec, status="plan_ready"):
    """tasks_spec: [(seq, deps)] — 편집 가능한 상태의 프로젝트를 만든다."""
    pid = db.create_project(conn, "목표")
    db.update_project(conn, pid, status=status)
    for seq, deps in tasks_spec:
        db.create_task(conn, pid, seq, f"태스크{seq}", f"설명{seq}", "text",
                       "claude", depends_on=",".join(str(d) for d in deps))
    return pid


def _task_id(conn, pid, seq):
    return next(t["id"] for t in db.list_tasks(conn, pid) if t["seq"] == seq)


def test_edit_guard_rejects_running_project(tmp_env):
    conn = _conn(tmp_env)
    pid = _editable_project(conn, [(1, [])], status="running")
    tid = _task_id(conn, pid, 1)
    with pytest.raises(ValueError):
        orchestrator.update_task_fields(conn, tid, title="새 제목",
                                        description="새 설명",
                                        task_type="text", agent="claude")


def test_edit_guard_rejects_finished_task(tmp_env):
    conn = _conn(tmp_env)
    pid = _editable_project(conn, [(1, [])], status="paused")
    tid = _task_id(conn, pid, 1)
    db.update_task(conn, tid, status="done")
    with pytest.raises(ValueError):
        orchestrator.delete_task(conn, tid)


def test_update_task_fields(tmp_env):
    conn = _conn(tmp_env)
    pid = _editable_project(conn, [(1, [])])
    tid = _task_id(conn, pid, 1)
    orchestrator.update_task_fields(conn, tid, title="고친 제목",
                                    description="고친 설명", task_type="text",
                                    agent="hermes")
    task = db.get_task(conn, tid)
    assert task["title"] == "고친 제목"
    assert task["provider"] == "hermes"


def test_update_task_fields_media_type_forces_media_provider(tmp_env):
    conn = _conn(tmp_env)
    pid = _editable_project(conn, [(1, [])])
    tid = _task_id(conn, pid, 1)
    orchestrator.update_task_fields(conn, tid, title="포스터", description="포스터 생성",
                                    task_type="image", agent="claude")
    assert db.get_task(conn, tid)["provider"] == MEDIA


def test_update_task_fields_rejects_unknown_type_and_agent(tmp_env):
    conn = _conn(tmp_env)
    pid = _editable_project(conn, [(1, [])])
    tid = _task_id(conn, pid, 1)
    with pytest.raises(ValueError):
        orchestrator.update_task_fields(conn, tid, title="t", description="d",
                                        task_type="hologram", agent="claude")
    with pytest.raises(ValueError):
        orchestrator.update_task_fields(conn, tid, title="t", description="d",
                                        task_type="text", agent="gpt")


def test_update_task_fields_rejects_empty(tmp_env):
    conn = _conn(tmp_env)
    pid = _editable_project(conn, [(1, [])])
    tid = _task_id(conn, pid, 1)
    with pytest.raises(ValueError):
        orchestrator.update_task_fields(conn, tid, title="  ", description="d",
                                        task_type="text", agent="claude")


def test_set_task_deps_replaces_whole_list(tmp_env):
    conn = _conn(tmp_env)
    pid = _editable_project(conn, [(1, []), (2, []), (3, [1])])
    tid = _task_id(conn, pid, 3)
    orchestrator.set_task_deps(conn, tid, [1, 2])
    assert orchestrator.task_deps(db.get_task(conn, tid)) == [1, 2]
    # 같은 엔드포인트로 엣지를 뗀다
    orchestrator.set_task_deps(conn, tid, [2])
    assert orchestrator.task_deps(db.get_task(conn, tid)) == [2]
    orchestrator.set_task_deps(conn, tid, [])
    assert orchestrator.task_deps(db.get_task(conn, tid)) == []


def test_set_task_deps_rejects_self_unknown_and_cycle(tmp_env):
    conn = _conn(tmp_env)
    pid = _editable_project(conn, [(1, []), (2, [1])])
    t1, t2 = _task_id(conn, pid, 1), _task_id(conn, pid, 2)
    with pytest.raises(ValueError):
        orchestrator.set_task_deps(conn, t2, [2])          # 자기 참조
    with pytest.raises(ValueError):
        orchestrator.set_task_deps(conn, t2, [99])         # 없는 seq
    with pytest.raises(ValueError):
        orchestrator.set_task_deps(conn, t1, [2])          # 1→2→1 순환
    assert orchestrator.task_deps(db.get_task(conn, t1)) == []


def test_add_task_appends_with_new_seq(tmp_env):
    conn = _conn(tmp_env)
    pid = _editable_project(conn, [(1, []), (2, [1])])
    tid = orchestrator.add_task(conn, pid, "새 태스크", description="새 설명",
                                task_type="text", agent="hermes", pos=(300, 40))
    task = db.get_task(conn, tid)
    assert task["seq"] == 3
    assert task["status"] == "pending"
    assert task["provider"] == "hermes"
    assert (task["pos_x"], task["pos_y"]) == (300.0, 40.0)


def test_add_task_defaults_description_to_title(tmp_env):
    conn = _conn(tmp_env)
    pid = _editable_project(conn, [(1, [])])
    tid = orchestrator.add_task(conn, pid, "제목만 있는 태스크")
    assert db.get_task(conn, tid)["description"] == "제목만 있는 태스크"


def test_add_task_respects_max_tasks(tmp_env, monkeypatch):
    conn = _conn(tmp_env)
    monkeypatch.setattr(config, "ORCH_MAX_TASKS", 2)
    pid = _editable_project(conn, [(1, []), (2, [])])
    with pytest.raises(ValueError):
        orchestrator.add_task(conn, pid, "세 번째")


def test_add_task_with_source_message_fills_created_task_id(tmp_env):
    """채팅→탭 자동 오픈 연결고리 — source_message_id가 있으면 그 메시지의
    created_task_id를 채워, 클라이언트가 메시지 응답만 보고 탭을 열 수 있다."""
    conn = _conn(tmp_env)
    pid = _editable_project(conn, [(1, [])])
    channel_id = db.create_channel(conn, "테스트 채널")
    message_id = db.create_message(conn, channel_id, role="user", body="새 태스크 만들어줘")
    assert db.get_message(conn, message_id)["created_task_id"] is None

    tid = orchestrator.add_task(conn, pid, "채팅에서 생긴 태스크",
                                source_message_id=message_id)

    assert db.get_message(conn, message_id)["created_task_id"] == tid


def test_add_task_without_source_message_leaves_created_task_id_null(tmp_env):
    conn = _conn(tmp_env)
    pid = _editable_project(conn, [(1, [])])
    channel_id = db.create_channel(conn, "테스트 채널")
    message_id = db.create_message(conn, channel_id, role="user", body="상관없는 메시지")
    orchestrator.add_task(conn, pid, "수동 추가 태스크")
    assert db.get_message(conn, message_id)["created_task_id"] is None


def test_delete_task_strips_dependency_from_siblings(tmp_env):
    conn = _conn(tmp_env)
    pid = _editable_project(conn, [(1, []), (2, [1]), (3, [1, 2])])
    orchestrator.delete_task(conn, _task_id(conn, pid, 1))
    remaining = {t["seq"]: orchestrator.task_deps(t) for t in db.list_tasks(conn, pid)}
    assert remaining == {2: [], 3: [2]}


def test_move_and_reset_layout(tmp_env):
    conn = _conn(tmp_env)
    pid = _editable_project(conn, [(1, [])])
    tid = _task_id(conn, pid, 1)
    orchestrator.move_task(conn, tid, 420, 88)
    assert (db.get_task(conn, tid)["pos_x"], db.get_task(conn, tid)["pos_y"]) == (420.0, 88.0)
    # 캔버스 밖(음수)으로는 나가지 않는다
    orchestrator.move_task(conn, tid, -50, -10)
    assert (db.get_task(conn, tid)["pos_x"], db.get_task(conn, tid)["pos_y"]) == (0.0, 0.0)
    orchestrator.reset_layout(conn, pid)
    assert db.get_task(conn, tid)["pos_x"] is None


def test_move_task_allowed_while_running(tmp_env):
    """배치는 순수 시각 요소 — 실행 중에도 막지 않는다."""
    conn = _conn(tmp_env)
    pid = _editable_project(conn, [(1, [])], status="running")
    tid = _task_id(conn, pid, 1)
    db.update_task(conn, tid, status="running")
    orchestrator.move_task(conn, tid, 10, 20)
    assert db.get_task(conn, tid)["pos_x"] == 10.0


# --- layout_graph: 저장된 좌표 + 모바일 세로 배치 --------------------------------

def test_layout_honors_saved_positions(tmp_env):
    rows = _rows([(1, [], "pending"), (2, [1], "pending")])
    rows[1]["pos_x"], rows[1]["pos_y"] = 500.0, 300.0
    g = layout_graph(rows)
    nodes = {n["seq"]: n for n in g["nodes"]}
    assert (nodes[2]["x"], nodes[2]["y"]) == (500.0, 300.0)
    # 캔버스는 실제 노드 경계까지 넓어진다
    assert g["width"] >= 500 + nodes[2]["w"]
    assert g["height"] >= 300 + nodes[2]["h"]


def test_layout_vertical_for_mobile(tmp_env):
    rows = _rows([(1, [], "done"), (2, [1], "pending"), (3, [1], "pending")])
    g = layout_graph(rows, orientation="tb")
    nodes = {n["seq"]: n for n in g["nodes"]}
    assert g["orientation"] == "tb"
    # 한 줄 세로 스택 — 가로 스크롤이 생길 여지가 없다
    assert nodes[1]["x"] == nodes[2]["x"] == nodes[3]["x"]
    assert nodes[1]["y"] < nodes[2]["y"] < nodes[3]["y"]
    assert g["width"] <= 400
    # 세로 흐름에서 출력 포트는 노드 아래쪽
    assert nodes[1]["out_y"] == nodes[1]["y"] + nodes[1]["h"]
    # 노드는 데스크톱보다 넓고 높다(제목 세 줄 + 큰 터치 타깃)
    assert nodes[1]["w"] > NODE_W and nodes[1]["h"] > NODE_H


def test_layout_vertical_routes_skipping_edges_through_a_left_rail(tmp_env):
    """바로 아래가 아닌 노드로 가는 선은 왼쪽 레일로 빼서 노드를 관통하지 않는다."""
    rows = _rows([(1, [], "done"), (2, [1], "done"), (3, [1, 2], "pending")])
    g = layout_graph(rows, orientation="tb")
    nodes = {n["seq"]: n for n in g["nodes"]}
    paths = {(e["from"], e["to"]): e["path"] for e in g["edges"]}
    left = nodes[1]["x"]
    # 1→3은 2를 건너뛴다 — 노드 왼쪽 바깥의 레일 x를 지난다
    rail_xs = [float(tok) for tok in re.findall(r"L ([\d.]+) [\d.]+", paths[(1, 3)])]
    assert rail_xs and all(x < left for x in rail_xs)
    # 붙어 있는 1→2, 2→3은 그냥 내려 긋는다(레일 구간 없음)
    assert "L " not in paths[(1, 2)] and "L " not in paths[(2, 3)]


def test_layout_vertical_separates_overlapping_rails(tmp_env):
    """세로 구간이 겹치는 우회선끼리는 레인을 나눠 포개지지 않게 한다."""
    rows = _rows([(1, [], "done"), (2, [1], "done"), (3, [2], "pending"),
                  (4, [1, 2, 3], "pending")])
    g = layout_graph(rows, orientation="tb")
    paths = {(e["from"], e["to"]): e["path"] for e in g["edges"]}
    rail_x = {k: re.search(r"L ([\d.]+) ", v).group(1)
              for k, v in paths.items() if "L " in v}
    assert set(rail_x) == {(1, 4), (2, 4)}
    assert rail_x[(1, 4)] != rail_x[(2, 4)]   # 두 우회선은 구간이 겹친다


def test_layout_vertical_ignores_saved_positions(tmp_env):
    """모바일은 '잘 보이도록 재정렬'이 목적 — 데스크톱 배치를 따라가지 않는다."""
    rows = _rows([(1, [], "pending"), (2, [1], "pending")])
    rows[1]["pos_x"], rows[1]["pos_y"] = 900.0, 700.0
    g = layout_graph(rows, orientation="tb")
    nodes = {n["seq"]: n for n in g["nodes"]}
    assert nodes[2]["x"] != 900.0 and nodes[2]["y"] != 700.0


def test_layout_ports_match_edge_endpoints():
    g = layout_graph(_rows([(1, [], "done"), (2, [1], "pending")]))
    nodes = {n["seq"]: n for n in g["nodes"]}
    edge = g["edges"][0]
    assert edge["path"].startswith(f"M {nodes[1]['out_x']} {nodes[1]['out_y']}")
    assert edge["path"].endswith(f"{nodes[2]['in_x']} {nodes[2]['in_y']}")
