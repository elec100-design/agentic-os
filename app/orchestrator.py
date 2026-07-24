"""비전 보드 오케스트레이터 — 메인 에이전트가 프로젝트 목표를 태스크로
분해하고(계획), 승인 후 의존성이 풀린 태스크를 잡으로 디스패치한다(실행).

실행은 전부 기존 worker_loop/run_job이 담당한다. 이 모듈은 북키핑만 한다:
계획 잡 결과 파싱 → 태스크 생성 → ready 태스크에 잡 생성 → 결과 회수 →
프로젝트 상태 전진. 모든 상태가 SQLite에 있어 재시작에 안전하고(멱등),
에이전트 간 컨텍스트는 세션이 아니라 프롬프트 텍스트로 전달한다(council 방식).
"""
from __future__ import annotations

import asyncio
import json
import re

from app import config, council, db
from app.providers import MEDIA, PROVIDERS, rank_cloud, route_auto

TASK_TYPES = ("text", "image", "video", "audio")
# 계획 파싱 실패로 자동 재시도했음을 표시하는 마커 (재재시도 방지)
_RETRY_MARKER = "plan_parse_retry"

PLAN_PROMPT = """당신은 프로젝트를 완성 가능한 태스크들로 분해하는 오케스트레이터입니다.

## 프로젝트 목표

{goal}

## 지시

목표를 달성하기 위한 태스크 계획을 세우세요. 규칙:

- 태스크는 1~{max_tasks}개. 각 태스크는 한 에이전트가 독립적으로 완수할 수 있는 단위.
- type: "text"(글·코드·분석 등 텍스트 산출물), "image"(이미지 생성), "video"(비디오 생성), "audio"(음성/오디오 생성)
- agent: 텍스트 태스크를 맡길 에이전트 — {agents} 중 하나, 또는 "auto"(자동 배정). 미디어 태스크(type != "text")의 agent는 무시됩니다.
- depends_on: 이 태스크가 결과를 참고해야 하는 선행 태스크 id 목록(없으면 []). 순환 금지.
- description은 선행 결과 없이도 이해되도록 구체적으로. 미디어 태스크의 description은 생성 프롬프트로 그대로 쓰입니다.

아래 형식의 JSON 하나만 ```json 코드블록으로 출력하세요. 다른 설명은 쓰지 마세요.

```json
{{"title": "프로젝트 한줄 제목", "tasks": [{{"id": 1, "title": "태스크 제목", "description": "구체적 작업 지시", "type": "text", "agent": "auto", "depends_on": []}}]}}
```"""

TASK_PROMPT = """당신은 프로젝트의 태스크 하나를 수행하는 에이전트입니다.

## 프로젝트 목표

{goal}

## 당신의 태스크: {title}

{description}
{upstream}
## 지시

태스크 설명에 충실하게 최종 결과물만 출력하세요. 계획이나 사족 없이."""

REPLAN_CONTEXT = """
## 이미 완료된 태스크와 결과 (다시 계획하지 말 것 — 남은 일만 계획)

{completed}
"""


class PlanError(ValueError):
    """계획 JSON을 파싱/검증할 수 없음."""


# --- 계획 (Phase A) ---------------------------------------------------------

def _pick_planner(usage_state=None, enabled=None):
    """계획을 세울 메인 에이전트 — 잔여 사용량 순 클라우드(claude 우선)."""
    ranked = rank_cloud(usage_state or council.usage_snapshot(), enabled=enabled)
    return ranked[0][0] if ranked else "claude"


def build_plan_prompt(goal, enabled=None):
    agents = [p for p in PROVIDERS if enabled is None or p in enabled]
    return PLAN_PROMPT.format(goal=goal, max_tasks=config.ORCH_MAX_TASKS,
                              agents=", ".join(f'"{a}"' for a in agents))


def start_project(conn, goal, workdir=None, enabled=None):
    """프로젝트 생성 + 계획 잡 큐잉. project_id 반환."""
    project_id = db.create_project(conn, goal, workdir=workdir)
    planner = _pick_planner(enabled=enabled)
    job_id = db.create_job(conn, build_plan_prompt(goal, enabled=enabled),
                           planner, route_reason="비전 보드 계획")
    db.update_project(conn, project_id, plan_job_id=job_id, planner=planner)
    return project_id


_FENCE_RE = re.compile(r"```json\s*(.*?)```", re.S)


def _extract_json(text):
    """마지막 ```json 펜스 블록, 없으면 첫 중괄호 밸런스 블록."""
    blocks = _FENCE_RE.findall(text or "")
    if blocks:
        return blocks[-1].strip()
    start = (text or "").find("{")
    if start < 0:
        raise PlanError("응답에서 JSON을 찾지 못했습니다")
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    raise PlanError("JSON 중괄호가 닫히지 않았습니다")


def parse_plan(text, usage_state=None, enabled=None):
    """계획 응답 → {"title": str, "tasks": [{...}]}. 실패 시 PlanError.

    검증: 개수 상한, id 유일, 알려진 agent/type, deps 유효성, 순환 없음.
    type != text → provider는 media로 강제. agent == auto → route_auto로 확정.
    """
    try:
        data = json.loads(_extract_json(text))
    except json.JSONDecodeError as e:
        raise PlanError(f"JSON 파싱 실패: {e}")
    if not isinstance(data, dict) or not isinstance(data.get("tasks"), list):
        raise PlanError('최상위에 "tasks" 배열이 필요합니다')
    raw_tasks = data["tasks"]
    if not 1 <= len(raw_tasks) <= config.ORCH_MAX_TASKS:
        raise PlanError(
            f"태스크는 1~{config.ORCH_MAX_TASKS}개여야 합니다 (현재 {len(raw_tasks)}개)")

    tasks, ids = [], set()
    for t in raw_tasks:
        if not isinstance(t, dict):
            raise PlanError("태스크는 객체여야 합니다")
        tid = t.get("id")
        if not isinstance(tid, int) or tid in ids:
            raise PlanError(f"태스크 id가 없거나 중복입니다: {tid!r}")
        ids.add(tid)
        title = str(t.get("title") or "").strip()
        desc = str(t.get("description") or "").strip()
        if not title or not desc:
            raise PlanError(f"태스크 {tid}: title/description 필수")
        ttype = t.get("type", "text")
        if ttype not in TASK_TYPES:
            raise PlanError(f"태스크 {tid}: 알 수 없는 type {ttype!r}")
        deps = t.get("depends_on", [])
        if not isinstance(deps, list) or not all(isinstance(d, int) for d in deps):
            raise PlanError(f"태스크 {tid}: depends_on은 정수 배열이어야 합니다")
        if tid in deps:
            raise PlanError(f"태스크 {tid}: 자기 자신에 의존할 수 없습니다")
        if ttype != "text":
            provider = MEDIA
        else:
            agent = t.get("agent", "auto")
            if agent == "auto":
                provider, _ = route_auto(desc, usage_state=usage_state,
                                         enabled=enabled)
            elif agent in PROVIDERS:
                provider = agent
            else:
                raise PlanError(f"태스크 {tid}: 알 수 없는 agent {agent!r}")
        tasks.append({"id": tid, "title": title, "description": desc,
                      "type": ttype, "provider": provider, "depends_on": deps})

    for t in tasks:
        for d in t["depends_on"]:
            if d not in ids:
                raise PlanError(
                    f"태스크 {t['id']}: 존재하지 않는 선행 태스크 {d}에 의존합니다")

    # 위상정렬로 순환 검증 (Kahn)
    remaining = {t["id"]: set(t["depends_on"]) for t in tasks}
    while remaining:
        ready = [tid for tid, deps in remaining.items() if not deps]
        if not ready:
            raise PlanError(f"태스크 의존성에 순환이 있습니다: {sorted(remaining)}")
        for tid in ready:
            del remaining[tid]
        for deps in remaining.values():
            deps.difference_update(ready)

    return {"title": str(data.get("title") or "").strip(), "tasks": tasks}


def _instantiate_plan(conn, project, plan):
    for t in plan["tasks"]:
        db.create_task(conn, project["id"], t["id"], t["title"],
                       t["description"], t["type"], t["provider"],
                       depends_on=",".join(str(d) for d in t["depends_on"]))
    db.update_project(conn, project["id"], status="plan_ready",
                      title=plan["title"] or None, error=None)


def _advance_planning(conn, project, usage_state=None, enabled=None):
    """계획 잡의 결과를 회수해 태스크를 생성한다. 파싱 실패 시 에러를 프롬프트에
    붙여 자동 1회 재시도, 그래도 실패면 plan_failed."""
    job = db.get_job(conn, project["plan_job_id"]) if project["plan_job_id"] else None
    if job is None:
        db.update_project(conn, project["id"], status="plan_failed",
                          error="계획 잡이 없습니다")
        return
    if job["status"] == "failed":
        db.update_project(conn, project["id"], status="plan_failed",
                          error=f"계획 실패: {job['error'] or '알 수 없음'}")
        return
    if job["status"] != "done":
        return  # 아직 실행 중/대기
    try:
        plan = parse_plan(job["output"], usage_state=usage_state, enabled=enabled)
    except PlanError as e:
        already_retried = (project["error"] or "").startswith(_RETRY_MARKER)
        if already_retried:
            db.update_project(conn, project["id"], status="plan_failed",
                              error=f"계획 파싱 실패: {e}")
            return
        retry_prompt = (
            job["prompt"]
            + f"\n\n## 이전 시도 오류\n\n이전 응답이 검증에 실패했습니다: {e}\n"
              "오류를 고쳐 올바른 JSON 하나만 다시 출력하세요.")
        new_job = db.create_job(conn, retry_prompt, job["provider"],
                                route_reason="비전 보드 계획 재시도")
        db.update_project(conn, project["id"], plan_job_id=new_job,
                          error=f"{_RETRY_MARKER}: {e}")
        return
    _instantiate_plan(conn, project, plan)


# --- 실행 (Phase B) ---------------------------------------------------------

def _clip(text):
    limit = config.ORCH_UPSTREAM_CLIP_CHARS
    if len(text) <= limit:
        return text
    return text[:limit] + "\n\n...[길이 제한으로 생략]"


def _deps(task):
    return [int(d) for d in task["depends_on"].split(",") if d.strip()]


def build_task_prompt(project, task, upstream_tasks):
    """태스크 실행 프롬프트 — 목표 + 태스크 설명 + 선행 결과(stateless 핸드오프)."""
    if task["provider"] == MEDIA:
        # 미디어 생성 프롬프트는 description 자체. 선행 텍스트가 있으면 참고로 덧붙인다.
        parts = [task["description"]]
        for up in upstream_tasks:
            parts.append(f"\n\n참고 자료 ({up['title']}):\n{_clip(up['output'])}")
        return "".join(parts)
    upstream = ""
    if upstream_tasks:
        sections = [f"### 선행 태스크 [{up['seq']}] {up['title']}\n\n{_clip(up['output'])}"
                    for up in upstream_tasks]
        upstream = "\n## 선행 태스크 결과\n\n" + "\n\n".join(sections) + "\n"
    return TASK_PROMPT.format(goal=project["goal"], title=task["title"],
                              description=task["description"], upstream=upstream)


# 잡 상태 → 태스크 상태 매핑 (rate_limited는 재개 대기 = 큐 대기로 표시)
_JOB_TO_TASK = {"queued": "queued", "running": "running",
                "rate_limited": "queued", "done": "done", "failed": "failed"}


def _sync_tasks(conn, project, tasks):
    """디스패치된 태스크의 잡 상태를 태스크 행으로 반영한다. 실패 발견 시 True."""
    failed = False
    for task in tasks:
        if task["job_id"] is None or task["status"] in ("done", "failed"):
            continue
        job = db.get_job(conn, task["job_id"])
        if job is None:  # 잡이 지워짐 — 태스크 실패 처리
            db.update_task(conn, task["id"], status="failed",
                           error="잡이 삭제되었습니다", finished_at=db.now_iso())
            failed = True
            continue
        new_status = _JOB_TO_TASK.get(job["status"], task["status"])
        if new_status == task["status"]:
            continue
        fields = {"status": new_status}
        if new_status == "running" and not task["started_at"]:
            fields["started_at"] = job["started_at"] or db.now_iso()
        if new_status == "done":
            fields["output"] = job["output"]
            fields["finished_at"] = job["finished_at"] or db.now_iso()
            if task["provider"] == MEDIA:
                # 미디어 잡의 output 마지막 줄 = 산출물 절대 경로 (media.py 계약)
                lines = [ln for ln in job["output"].strip().splitlines() if ln.strip()]
                fields["artifact_path"] = lines[-1].strip() if lines else None
        if new_status == "failed":
            fields["error"] = job["error"] or "실행 실패"
            fields["finished_at"] = job["finished_at"] or db.now_iso()
            failed = True
        db.update_task(conn, task["id"], **fields)
    return failed


def _advance_running(conn, project):
    """실행 중 프로젝트 1틱: 동기화 → 실패 시 일시정지 → ready 태스크 디스패치
    → 전부 완료면 done. 멱등 — 언제든 다시 불러도 안전하다."""
    tasks = db.list_tasks(conn, project["id"])
    if _sync_tasks(conn, project, tasks):
        db.update_project(conn, project["id"], status="paused",
                          error="태스크 실패 — 재시도하거나 재계획하세요")
        return
    tasks = db.list_tasks(conn, project["id"])
    by_seq = {t["seq"]: t for t in tasks}

    if all(t["status"] == "done" for t in tasks):
        db.update_project(conn, project["id"], status="done", error=None)
        return

    inflight = sum(1 for t in tasks if t["status"] in ("queued", "running"))
    for task in tasks:
        if inflight >= config.ORCH_MAX_INFLIGHT:
            break
        if task["status"] != "pending":
            continue
        deps = _deps(task)
        if any(by_seq[d]["status"] != "done" for d in deps if d in by_seq):
            continue
        upstream = [by_seq[d] for d in deps if d in by_seq]
        prompt = build_task_prompt(project, task, upstream)
        job_id = db.create_job(
            conn, prompt, task["provider"],
            model=task["task_type"] if task["provider"] == MEDIA else None,
            workdir=project["workdir"],
            route_reason=f"비전 보드 태스크 #{task['seq']}")
        db.update_task(conn, task["id"], status="queued", job_id=job_id)
        inflight += 1


def _advance(conn, project, usage_state=None, enabled=None):
    if project["status"] == "planning":
        _advance_planning(conn, project, usage_state=usage_state, enabled=enabled)
    elif project["status"] == "running":
        _advance_running(conn, project)


async def orchestrator_loop(stop_event=None, poll_sec=None):
    """활성 프로젝트를 주기적으로 전진시키는 루프 (lifespan에 등록)."""
    conn = db.get_conn()
    poll = poll_sec or config.ORCH_POLL_SEC
    while not (stop_event and stop_event.is_set()):
        for project in db.active_projects(conn):
            try:
                _advance(conn, project)
            except Exception as e:
                # 루프는 살아야 한다 — 프로젝트에 에러를 남기고 계속
                db.update_project(conn, project["id"], status="paused",
                                  error=f"orchestrator error: {e!r}")
        await asyncio.sleep(poll)


# --- 사용자 조작 ------------------------------------------------------------

def approve(conn, project_id):
    project = db.get_project(conn, project_id)
    if project is None or project["status"] != "plan_ready":
        raise ValueError("승인 대기 상태의 프로젝트가 아닙니다")
    db.update_project(conn, project_id, status="running", error=None)


def retry_task(conn, task_id):
    """실패 태스크를 pending으로 리셋하고 프로젝트를 재개한다."""
    task = db.get_task(conn, task_id)
    if task is None or task["status"] != "failed":
        raise ValueError("실패 상태의 태스크가 아닙니다")
    db.update_task(conn, task_id, status="pending", job_id=None, error=None,
                   started_at=None, finished_at=None)
    project = db.get_project(conn, task["project_id"])
    if project and project["status"] == "paused":
        db.update_project(conn, project["id"], status="running", error=None)


def retry_plan(conn, project_id):
    """계획 실패 프로젝트의 계획을 처음부터 다시 돌린다."""
    project = db.get_project(conn, project_id)
    if project is None or project["status"] != "plan_failed":
        raise ValueError("계획 실패 상태의 프로젝트가 아닙니다")
    planner = _pick_planner()
    job_id = db.create_job(conn, build_plan_prompt(project["goal"]), planner,
                           route_reason="비전 보드 계획 재시도")
    db.update_project(conn, project_id, status="planning", plan_job_id=job_id,
                      planner=planner, error=None)


def replan(conn, project_id):
    """미완료 태스크를 폐기하고, 완료 결과를 컨텍스트로 계획을 다시 세운다."""
    project = db.get_project(conn, project_id)
    if project is None or project["status"] not in ("plan_ready", "paused", "running"):
        raise ValueError("재계획할 수 없는 상태입니다")
    # 중간 상태는 paused — 루프가 건드리지 않는다. planning으로 먼저 바꾸면
    # 새 계획 잡을 걸기 전에 루프가 '이전 계획 잡'(done)을 다시 파싱해 태스크를
    # 재생성하는 경합이 생긴다. 최종 planning 전환은 아래 단일 update로 원자적.
    cancel_project(conn, project_id, _status="paused")
    db.delete_tasks(conn, project_id,
                    statuses=("pending", "queued", "running", "failed"))
    done = [t for t in db.list_tasks(conn, project_id) if t["status"] == "done"]
    prompt = build_plan_prompt(project["goal"])
    if done:
        completed = "\n\n".join(
            f"### [{t['seq']}] {t['title']}\n\n{_clip(t['output'])}" for t in done)
        prompt += REPLAN_CONTEXT.format(completed=completed)
    planner = _pick_planner()
    job_id = db.create_job(conn, prompt, planner, route_reason="비전 보드 재계획")
    db.update_project(conn, project_id, status="planning", plan_job_id=job_id,
                      planner=planner, error=None)


def cancel_project(conn, project_id, _status="cancelled"):
    """실행 중인 태스크 잡을 중단하고 프로젝트를 취소(또는 재계획 전 정리)한다."""
    from app import worker  # 순환 임포트 방지
    project = db.get_project(conn, project_id)
    if project is None:
        return
    for task in db.list_tasks(conn, project_id):
        if task["job_id"] and task["status"] in ("queued", "running"):
            job = db.get_job(conn, task["job_id"])
            if job and job["status"] in ("queued", "running", "rate_limited"):
                db.update_job(conn, task["job_id"], status="failed",
                              error="cancelled", finished_at=db.now_iso())
                worker.terminate_job_procs(task["job_id"])
    plan_job = (db.get_job(conn, project["plan_job_id"])
                if project["plan_job_id"] else None)
    if plan_job and plan_job["status"] in ("queued", "running", "rate_limited"):
        db.update_job(conn, plan_job["id"], status="failed", error="cancelled",
                      finished_at=db.now_iso())
        worker.terminate_job_procs(plan_job["id"])
    db.update_project(conn, project_id, status=_status)


# --- 그래프 레이아웃 (n8n식 DAG, 서버 렌더링 SVG용) --------------------------

NODE_W, NODE_H = 190, 72
GAP_X, GAP_Y = 80, 28
PAD = 24


def layout_graph(tasks):
    """태스크 목록 → SVG 좌표. 순수 함수.

    레이어 = 의존성 위상 깊이(왼→오), 레이어 안에서는 seq 순 세로 배치.
    반환: {"nodes": [{...task fields, x, y, w, h}],
           "edges": [{"from": seq, "to": seq, "path": "M..C..", "status": ...}],
           "width": int, "height": int}
    """
    by_seq = {t["seq"]: t for t in tasks}

    depth_cache = {}

    def depth(seq, trail=()):
        if seq in depth_cache:
            return depth_cache[seq]
        if seq in trail:  # 순환은 parse_plan에서 걸러지지만 방어
            return 0
        task = by_seq[seq]
        deps = [d for d in _deps(task) if d in by_seq]
        d = 0 if not deps else 1 + max(depth(p, trail + (seq,)) for p in deps)
        depth_cache[seq] = d
        return d

    layers = {}
    for t in tasks:
        layers.setdefault(depth(t["seq"]), []).append(t)

    nodes = {}
    max_rows = 0
    for layer_idx in sorted(layers):
        col = sorted(layers[layer_idx], key=lambda t: t["seq"])
        max_rows = max(max_rows, len(col))
        for row_idx, t in enumerate(col):
            nodes[t["seq"]] = {
                **dict(t),
                "x": PAD + layer_idx * (NODE_W + GAP_X),
                "y": PAD + row_idx * (NODE_H + GAP_Y),
                "w": NODE_W, "h": NODE_H,
            }

    edges = []
    for t in tasks:
        child = nodes[t["seq"]]
        for d in _deps(t):
            if d not in nodes:
                continue
            parent = nodes[d]
            x1, y1 = parent["x"] + NODE_W, parent["y"] + NODE_H / 2
            x2, y2 = child["x"], child["y"] + NODE_H / 2
            mx = (x1 + x2) / 2
            edges.append({
                "from": d, "to": t["seq"],
                "path": f"M {x1} {y1} C {mx} {y1}, {mx} {y2}, {x2} {y2}",
                "done": parent["status"] == "done",
            })

    n_layers = len(layers)
    width = PAD * 2 + n_layers * NODE_W + max(0, n_layers - 1) * GAP_X
    height = PAD * 2 + max_rows * NODE_H + max(0, max_rows - 1) * GAP_Y
    return {"nodes": list(nodes.values()), "edges": edges,
            "width": max(width, 300), "height": max(height, 160)}
