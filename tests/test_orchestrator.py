import asyncio
import json

import pytest

from app import config, db, orchestrator, worker
from app.orchestrator import PlanError, layout_graph, parse_plan
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
