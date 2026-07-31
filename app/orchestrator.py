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

from app import config, council, db, models
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
{upstream}{extra}
## 지시

태스크 설명에 충실하게 최종 결과물만 출력하세요. 계획이나 사족 없이."""

EXTRA_INSTRUCTION_SECTION = """
## 추가 지시 (사용자가 직접 덧붙임 — 반드시 지킬 것)

{instruction}
"""

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


def start_project(conn, goal, workdir=None, enabled=None, planner=None, model=None,
                  timeout_sec=None, extra_context=None):
    """프로젝트 생성 + 계획 잡 큐잉. project_id 반환.

    planner가 유효 provider면 그대로 쓰고, 없거나 "auto"면 자동 선택한다.
    model은 계획 잡에만 적용된다(태스크 잡의 provider/model은 계획 결과로 정해짐).
    extra_context(메모리/첨부 파일 등)는 계획 프롬프트에만 붙고 project.goal에는
    남지 않는다 — 보드에는 사용자가 입력한 원래 목표만 표시한다.
    """
    project_id = db.create_project(conn, goal, workdir=workdir)
    # 프로젝트 자체는 워크플로 탭으로 열어 두고, 계획에서 생긴 각 실행 단위는
    # 아래 _instantiate_plan에서 task 탭으로 추가한다.
    db.get_or_create_board_tab(conn, project_id, goal.strip()[:80] or "Workflow",
                               "workflow", project_id, status="pending")
    if planner and planner != "auto" and planner in PROVIDERS:
        picked = planner
    else:
        picked = _pick_planner(enabled=enabled)
    if not models.is_valid_model(picked, model or ""):
        model = None
    plan_goal = f"{extra_context}{goal}" if extra_context else goal
    job_id = db.create_job(conn, build_plan_prompt(plan_goal, enabled=enabled),
                           picked, model=model or None, timeout_sec=timeout_sec,
                           route_reason="비전 보드 계획")
    db.update_project(conn, project_id, plan_job_id=job_id, planner=picked,
                      planner_model=model or None)
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


def resolve_provider(task_type, agent, description, usage_state=None,
                     enabled=None, label=""):
    """(타입, 에이전트 선택) → 실제 provider. 계획 파싱과 다이어그램 편집 공용.

    미디어 태스크의 에이전트 선택은 무시되고 media로 강제된다(media.py가 처리).
    """
    if task_type != "text":
        return MEDIA
    if agent == "auto":
        provider, _ = route_auto(description, usage_state=usage_state,
                                 enabled=enabled)
        return provider
    if agent in PROVIDERS:
        return agent
    raise PlanError(f"{label}알 수 없는 agent {agent!r}")


def _assert_acyclic(deps_by_id):
    """위상정렬(Kahn)로 순환 검증. 계획 파싱과 다이어그램 편집이 함께 쓴다."""
    remaining = {k: set(v) for k, v in deps_by_id.items()}
    while remaining:
        ready = [k for k, deps in remaining.items() if not deps]
        if not ready:
            raise PlanError(f"태스크 의존성에 순환이 있습니다: {sorted(remaining)}")
        for k in ready:
            del remaining[k]
        for deps in remaining.values():
            deps.difference_update(ready)


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
        provider = resolve_provider(ttype, t.get("agent", "auto"), desc,
                                    usage_state=usage_state, enabled=enabled,
                                    label=f"태스크 {tid}: ")
        tasks.append({"id": tid, "title": title, "description": desc,
                      "type": ttype, "provider": provider, "depends_on": deps})

    for t in tasks:
        for d in t["depends_on"]:
            if d not in ids:
                raise PlanError(
                    f"태스크 {t['id']}: 존재하지 않는 선행 태스크 {d}에 의존합니다")

    _assert_acyclic({t["id"]: t["depends_on"] for t in tasks})

    return {"title": str(data.get("title") or "").strip(), "tasks": tasks}


def _instantiate_plan(conn, project, plan):
    for t in plan["tasks"]:
        task_id = db.create_task(conn, project["id"], t["id"], t["title"],
                                 t["description"], t["type"], t["provider"],
                                 depends_on=",".join(str(d) for d in t["depends_on"]))
        db.get_or_create_board_tab(conn, project["id"], t["title"], "task",
                                   task_id, status="pending", activate=False)
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


def task_deps(task):
    """선행 태스크 seq 목록. 모듈 안에서는 짧은 별칭 _deps로 쓴다."""
    return [int(d) for d in task["depends_on"].split(",") if d.strip()]


_deps = task_deps


def _opt(row, key):
    """Row/dict에서 없을 수도 있는 컬럼을 안전하게 읽는다 (구 DB·테스트용 dict)."""
    try:
        return row[key]
    except (KeyError, IndexError):
        return None


def build_task_prompt(project, task, upstream_tasks):
    """태스크 실행 프롬프트 — 목표 + 태스크 설명 + 선행 결과(stateless 핸드오프).

    오류 조치로 추가 지시(extra_instruction)가 붙어 있으면 프롬프트 끝에 싣는다.
    """
    extra_raw = (_opt(task, "extra_instruction") or "").strip()
    if task["provider"] == MEDIA:
        # 미디어 생성 프롬프트는 description 자체. 선행 텍스트가 있으면 참고로 덧붙인다.
        parts = [task["description"]]
        if extra_raw:
            parts.append(f"\n\n{extra_raw}")
        for up in upstream_tasks:
            parts.append(f"\n\n참고 자료 ({up['title']}):\n{_clip(up['output'])}")
        return "".join(parts)
    upstream = ""
    if upstream_tasks:
        sections = [f"### 선행 태스크 [{up['seq']}] {up['title']}\n\n{_clip(up['output'])}"
                    for up in upstream_tasks]
        upstream = "\n## 선행 태스크 결과\n\n" + "\n\n".join(sections) + "\n"
    extra = (EXTRA_INSTRUCTION_SECTION.format(instruction=extra_raw)
             if extra_raw else "")
    return TASK_PROMPT.format(goal=project["goal"], title=task["title"],
                              description=task["description"],
                              upstream=upstream, extra=extra)


def build_task_chat_prompt(task, history, message):
    """사이드바 태스크 채팅 프롬프트 — 현재 설명 + 대화 맥락 + 새 사용자 메시지.

    에이전트가 설명을 새로 쓰는 게 낫다고 판단하면 JSON으로 제안을 실어
    보내도록 요청한다(parse_task_chat_reply가 그 형식을 기대한다).
    """
    convo = ""
    if history:
        lines = [f"- {h['role']}: {h['content']}" for h in history]
        convo = "## 이전 대화\n" + "\n".join(lines) + "\n\n"
    return (
        "당신은 태스크 편집을 돕는 어시스턴트입니다. 사용자가 태스크 하나를 두고 "
        "대화하며 설명을 다듬으려 합니다.\n\n"
        f"## 태스크 제목\n{task['title']}\n\n"
        f"## 현재 설명\n{task['description']}\n\n"
        f"{convo}"
        f"## 사용자 메시지\n{message}\n\n"
        "## 지시\n"
        "사용자 메시지에 답하세요. 설명을 새로 쓰는 게 좋다면 아래 JSON 코드블록에 "
        "새 description 전체를 담아 제안하세요. 제안이 필요 없으면 description 없이 "
        "답변만 하세요.\n\n"
        "```json\n"
        '{"reply": "사용자에게 보여줄 답변", '
        '"description": "새 설명 전체(제안이 있을 때만 포함)"}\n'
        "```"
    )


def parse_task_chat_reply(text):
    """에이전트 출력에서 (답변, 제안된 새 description|None)을 뽑는다.

    JSON 블록이 없거나 파싱에 실패하면 원문 전체를 답변으로 쓰고 제안 없음으로
    처리한다 — 채팅은 자유 형식 응답도 허용해야 하므로 계획 파싱과 달리
    실패를 예외로 올리지 않는다.
    """
    stripped = (text or "").strip()
    try:
        data = json.loads(_extract_json(text))
    except (PlanError, json.JSONDecodeError, TypeError):
        return stripped, None
    if not isinstance(data, dict):
        return stripped, None
    reply = str(data.get("reply") or "").strip() or stripped
    description = data.get("description")
    description = description.strip() if isinstance(description, str) else None
    return reply, (description or None)


# 잡 상태 → 태스크 상태 매핑 (rate_limited는 재개 대기 = 큐 대기로 표시)
_JOB_TO_TASK = {"queued": "queued", "running": "running",
                "rate_limited": "queued", "done": "done", "failed": "failed"}


# --- 실행 세션(workflow_runs) ------------------------------------------------
# 프로젝트 한 번의 "전체 실행"이 세션 하나다. 세션 안의 task_runs가 태스크별
# 시도 이력(부분 출력·에이전트·에러)을 남겨, 서버가 죽거나 사용량 한도로 끊겨도
# 완료된 태스크는 건너뛰고 미완료 태스크부터 이어서 실행할 수 있다.

def current_run_id(conn, project_id):
    """진행 중인 실행 세션 id — 없으면 새로 연다.

    서버 재시작으로 'interrupted'가 된 세션은 새로 만들지 않고 그대로 이어
    쓴다(그 세션에 남은 done 기록 덕분에 완료 태스크를 다시 돌리지 않는다).
    """
    run = db.get_resumable_run(conn, project_id)
    if run is None:
        return db.create_workflow_run(conn, project_id)
    if run["status"] != "running":
        db.update_workflow_run(conn, run["id"], status="running")
    return run["id"]


def _close_run(conn, project_id, status):
    run = db.get_resumable_run(conn, project_id)
    if run is not None:
        db.update_workflow_run(conn, run["id"], status=status)


def _open_task_run(conn, project, task):
    """이 태스크의 새 시도를 현재 세션에 기록한다(attempt는 이력에서 이어진다)."""
    run_id = current_run_id(conn, project["id"])
    return db.create_task_run(conn, task["id"], run_id, agent=task["provider"])


def _running_task_run(conn, project_id, task_id):
    run = db.get_resumable_run(conn, project_id)
    if run is None:
        return None
    task_run = db.get_active_task_run(conn, task_id, run["id"])
    if task_run is None or task_run["status"] != "running":
        return None
    return task_run


def _close_task_run(conn, project, task, *, status, output=None, error=None):
    """진행 중인 시도를 마감하며 그 시점의 출력을 스냅샷으로 보존한다."""
    task_run = _running_task_run(conn, project["id"], task["id"])
    if task_run is None:
        return
    if output:
        db.append_task_run_output(conn, task_run["id"], output)
    if status == "done":
        db.mark_task_run_done(conn, task_run["id"])
    else:
        db.mark_task_run_failed(conn, task_run["id"], error=error)


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
            _close_task_run(conn, project, task, status="failed",
                            error="잡이 삭제되었습니다")
            failed = True
            continue
        new_status = _JOB_TO_TASK.get(job["status"], task["status"])
        if new_status == task["status"]:
            continue
        fields = {"status": new_status}
        if new_status == "running":
            if not task["started_at"]:
                fields["started_at"] = job["started_at"] or db.now_iso()
            # 중단(서버 재시작 등)으로 이전 시도가 마감됐다면 재개된 이번 실행을
            # 새 시도로 기록한다 — 디스패치 때 연 시도가 살아 있으면 그대로 쓴다.
            if _running_task_run(conn, project["id"], task["id"]) is None:
                _open_task_run(conn, project, task)
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
        if new_status in ("done", "failed"):
            _close_task_run(conn, project, task, status=new_status,
                            output=job["output"], error=fields.get("error"))
        tab_status = {"queued": "pending", "running": "running",
                      "done": "done", "failed": "error"}.get(new_status)
        if tab_status:
            db.get_or_create_board_tab(conn, project["id"], task["title"],
                                       "task", task["id"], status=tab_status,
                                       activate=False)
    return failed


def _advance_running(conn, project):
    """실행 중 프로젝트 1틱: 동기화 → 실패 시 일시정지 → ready 태스크 디스패치
    → 전부 완료면 done. 멱등 — 언제든 다시 불러도 안전하다.

    수동 일시정지(pause_reason="manual")가 걸려 있으면 동기화는 계속하되(진행
    중이던 태스크는 끝까지 두어야 하므로) 새 태스크 디스패치만 건너뛴다. 인플라이트
    태스크가 다 빠지면 그 틱에 status를 paused로 내린다.
    """
    tasks = db.list_tasks(conn, project["id"])
    if _sync_tasks(conn, project, tasks):
        db.update_project(conn, project["id"], status="paused",
                          pause_reason="task_failed",
                          error="태스크 실패 — 재시도하거나 재계획하세요")
        _close_run(conn, project["id"], "paused")
        return
    tasks = db.list_tasks(conn, project["id"])
    by_seq = {t["seq"]: t for t in tasks}

    if all(t["status"] == "done" for t in tasks):
        db.update_project(conn, project["id"], status="done",
                          pause_reason=None, error=None)
        _close_run(conn, project["id"], "completed")
        return

    inflight = sum(1 for t in tasks if t["status"] in ("queued", "running"))
    if _opt(project, "pause_reason") == "manual":
        if inflight == 0:
            db.update_project(conn, project["id"], status="paused")
            _close_run(conn, project["id"], "paused")
        return

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
        # 미디어 잡은 model 컬럼에 종류(image|video|audio)를 실어 보낸다(media.py 계약).
        # 텍스트 태스크는 사용자가 교체한 모델이 있으면 그걸 쓴다.
        job_id = db.create_job(
            conn, prompt, task["provider"],
            model=(task["task_type"] if task["provider"] == MEDIA
                   else _opt(task, "model") or None),
            workdir=project["workdir"],
            route_reason=f"비전 보드 태스크 #{task['seq']}")
        db.update_task(conn, task["id"], status="queued", job_id=job_id)
        _open_task_run(conn, project, task)
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


def pause_project(conn, project_id):
    """실행 중 프로젝트에 수동 일시정지를 건다.

    status는 running으로 둔 채 pause_reason만 세운다 — db.active_projects()가
    running만 보므로, 그래야 오케스트레이터가 계속 돌며 이미 실행 중인 태스크를
    끝까지 동기화하고 인플라이트가 다 빠진 뒤에 스스로 paused로 내린다
    (_advance_running). 즉시 paused로 내리면 진행 중 태스크의 결과 회수가 멈춘다.
    """
    project = db.get_project(conn, project_id)
    if project is None or project["status"] != "running":
        raise ValueError("실행 중인 프로젝트만 일시정지할 수 있습니다")
    db.update_project(conn, project_id, pause_reason="manual")


def resume_project(conn, project_id):
    """일시정지된 프로젝트를 이어서 실행한다(완료된 태스크는 다시 돌지 않는다)."""
    project = db.get_project(conn, project_id)
    if project is None or project["status"] != "paused":
        raise ValueError("일시정지 상태의 프로젝트만 재개할 수 있습니다")
    tasks = db.list_tasks(conn, project_id)
    if any(t["status"] == "failed" for t in tasks):
        raise ValueError("실패한 태스크를 먼저 재시도하거나 편집한 뒤 재개하세요")
    current_run_id(conn, project_id)  # 중단된 세션을 이어 열거나 새로 연다
    db.update_project(conn, project_id, status="running", pause_reason=None,
                      error=None)


# 결과가 잘못된 '완료' 태스크도 다시 돌릴 수 있어야 한다 — 잡이 exit 0으로 끝나도
# 에이전트가 오류 안내문만 남기는 경우가 있다(권한 거부·모델 한도 등).
RETRYABLE_TASK_STATUSES = ("failed", "done")

# 결과 본문에 이게 있으면 '완료'라도 사실 실패다. CLI들이 오류를 stdout에 안내문으로
# 흘리고 exit 0으로 끝내기 때문 — 상태 배지만 보면 원인을 알 수 없다.
OUTPUT_ERROR_MARKERS = (
    "no output produced",
    "auto-denied",
    "dangerously-skip-permissions",
    "permission denied",
    "not logged in",
    "authentication failed",
    "usage limit reached",
    "command not found",
)
# 긴 산출물 안에 우연히 섞인 문구는 오탐이다 — 짧은 응답(=안내문)만 검사한다.
_OUTPUT_ERROR_SCAN_CHARS = 1500


def output_error_hint(task):
    """'완료'로 기록됐지만 결과가 사실 오류일 때의 짧은 설명. 정상이면 None."""
    if task["status"] != "done" or task["task_type"] != "text":
        return None
    out = (task["output"] or "").strip()
    if not out:
        return "완료로 기록됐지만 에이전트가 결과를 내놓지 않았습니다"
    if len(out) > _OUTPUT_ERROR_SCAN_CHARS:
        return None
    low = out.lower()
    # 어떤 문구에 걸렸는지는 아래에 원문이 그대로 보이므로 메시지에 끼우지 않는다
    # (값이 끼면 i18n 카탈로그가 못 잡아 영어 UI에서 한국어가 튀어나온다).
    if any(marker in low for marker in OUTPUT_ERROR_MARKERS):
        return "완료로 기록됐지만 결과가 오류 안내문입니다"
    return None


def task_recoverable(project, task):
    """'모델 교체·지시 추가 후 다시 실행' UI를 띄울 수 있는 태스크인지."""
    return (project is not None
            and project["status"] not in ("planning", "plan_failed")
            and task["status"] in RETRYABLE_TASK_STATUSES)

# 되돌릴 때 지워야 하는 실행 흔적 — 재시도와 후속 태스크 무효화가 함께 쓴다.
_RESET_FIELDS = {"status": "pending", "job_id": None, "error": None,
                 "output": "", "artifact_path": None,
                 "started_at": None, "finished_at": None}


def dependent_seqs(tasks, seq):
    """seq의 결과에 (간접적으로) 의존하는 태스크 seq 집합."""
    found, frontier = set(), {seq}
    while frontier:
        nxt = {t["seq"] for t in tasks
               if t["seq"] not in found and t["seq"] != seq
               and set(_deps(t)) & frontier}
        found |= nxt
        frontier = nxt
    return found


def retry_task(conn, task_id, *, agent=None, model=None, instruction=None,
               cascade=False, usage_state=None, enabled=None):
    """태스크를 되돌려 다시 실행한다. 실패한 태스크와, 결과가 잘못된 완료 태스크 모두.

    agent/model  — 모델 교체(빈 값이면 현재 설정을 유지).
    instruction  — 프롬프트에 덧붙일 추가 지시(빈 문자열이면 기존 지시를 지운다).
    cascade      — 이 태스크에 의존하는 후속 태스크도 함께 되돌린다. 낡은 결과를
                   그대로 두면 프로젝트가 '완료'인 채 잘못된 산출물이 남는다.
    """
    task = db.get_task(conn, task_id)
    if task is None:
        raise ValueError("태스크를 찾을 수 없습니다")
    if task["status"] not in RETRYABLE_TASK_STATUSES:
        raise ValueError("완료 또는 실패한 태스크만 다시 실행할 수 있습니다")
    fields = dict(_RESET_FIELDS)
    if agent:
        provider = _resolve_or_400(task["task_type"], agent, task["description"],
                                   usage_state=usage_state, enabled=enabled)
        fields["provider"] = provider
    else:
        provider = task["provider"]
    if model is not None:
        # 다른 프로바이더의 모델 id가 넘어오면(선택 UI 지연) 기본값으로 되돌린다
        fields["model"] = model if model and models.is_valid_model(
            provider, model) else None
    elif agent:
        fields["model"] = None  # 에이전트를 바꿨으면 이전 모델은 무효
    if instruction is not None:
        fields["extra_instruction"] = instruction.strip() or None
    db.update_task(conn, task_id, **fields)

    if cascade:
        siblings = db.list_tasks(conn, task["project_id"])
        stale = dependent_seqs(siblings, task["seq"])
        for sib in siblings:
            if sib["seq"] in stale and sib["status"] in RETRYABLE_TASK_STATUSES:
                db.update_task(conn, sib["id"], **_RESET_FIELDS)

    project = db.get_project(conn, task["project_id"])
    if project and project["status"] in ("paused", "done", "cancelled"):
        db.update_project(conn, project["id"], status="running",
                          pause_reason=None, error=None)


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
    # 실행 세션도 함께 마감한다 — 재개 대상으로 남으면 안 된다(재계획도 마찬가지).
    _close_run(conn, project_id, "cancelled")
    db.update_project(conn, project_id, status=_status)


# --- 다이어그램 편집 --------------------------------------------------------
# 구조 편집을 plan_ready/paused로 제한하는 이유: db.active_projects()가
# planning/running만 반환하므로, 이 두 상태에서는 오케스트레이터 루프가 프로젝트를
# 아예 전진시키지 않는다 — 편집과 잡 디스패치가 경합할 수 없다.

EDITABLE_PROJECT_STATUSES = ("plan_ready", "paused")
# 이미 실행됐거나 실행 중인 태스크는 건드리지 않는다(결과 보존 + 잡과의 정합성).
EDITABLE_TASK_STATUSES = ("pending", "failed")


def project_editable(project):
    """템플릿이 편집 UI를 그릴지 판단할 때 쓰는 술어."""
    return project is not None and project["status"] in EDITABLE_PROJECT_STATUSES


def task_editable(project, task):
    return project_editable(project) and task["status"] in EDITABLE_TASK_STATUSES


def _editable_project(conn, project_id):
    project = db.get_project(conn, project_id)
    if project is None:
        raise ValueError("프로젝트를 찾을 수 없습니다")
    if not project_editable(project):
        raise ValueError("계획 검토 또는 일시정지 상태에서만 그래프를 편집할 수 있습니다")
    return project


def _editable_task(conn, task_id):
    task = db.get_task(conn, task_id)
    if task is None:
        raise ValueError("태스크를 찾을 수 없습니다")
    project = _editable_project(conn, task["project_id"])
    if task["status"] not in EDITABLE_TASK_STATUSES:
        raise ValueError("이미 실행된 태스크는 편집할 수 없습니다")
    return project, task


def _resolve_or_400(task_type, agent, description, usage_state=None, enabled=None):
    try:
        return resolve_provider(task_type, agent or "auto", description,
                                usage_state=usage_state, enabled=enabled)
    except PlanError as e:
        raise ValueError(str(e)) from e


def update_task_fields(conn, task_id, *, title, description, task_type, agent,
                       model="", extra_instruction="",
                       usage_state=None, enabled=None):
    """편집 폼이 보낸 필드로 태스크를 갱신한다. 검증 규칙은 계획 파싱과 동일."""
    _project, task = _editable_task(conn, task_id)
    title = (title or "").strip()
    description = (description or "").strip()
    if not title or not description:
        raise ValueError("제목과 설명은 비울 수 없습니다")
    if task_type not in TASK_TYPES:
        raise ValueError(f"알 수 없는 type {task_type!r}")
    provider = _resolve_or_400(task_type, agent, description,
                               usage_state=usage_state, enabled=enabled)
    fields = {"title": title, "description": description,
              "task_type": task_type, "provider": provider}
    # retry_task와 동일한 규칙: 에이전트가 바뀌면 이전 모델은 무효
    if provider != task["provider"]:
        fields["model"] = None
    elif model:
        if not models.is_valid_model(provider, model):
            raise ValueError(f"알 수 없는 model {model!r}")
        fields["model"] = model
    else:
        # 편집 UI의 "기본값"은 빈 값으로 제출된다. 이전에 고른 모델을
        # 그대로 남기면 사용자가 기본 모델로 되돌릴 방법이 없어진다.
        fields["model"] = None
    fields["extra_instruction"] = extra_instruction.strip() or None
    db.update_task(conn, task["id"], **fields)


def set_task_deps(conn, task_id, deps):
    """태스크의 선행 목록을 통째로 교체 — 엣지 추가와 삭제를 한 연산으로 다룬다.

    검증: 같은 프로젝트에 있는 seq만, 자기 참조 금지, 그래프 전체에 순환 금지.
    """
    _project, task = _editable_task(conn, task_id)
    siblings = db.list_tasks(conn, task["project_id"])
    known = {t["seq"] for t in siblings}
    deps = sorted({int(d) for d in deps})
    if task["seq"] in deps:
        raise ValueError("자기 자신에 의존할 수 없습니다")
    unknown = [d for d in deps if d not in known]
    if unknown:
        raise ValueError(f"존재하지 않는 선행 태스크: {unknown}")
    graph = {t["seq"]: (deps if t["seq"] == task["seq"] else _deps(t))
             for t in siblings}
    try:
        _assert_acyclic(graph)
    except PlanError as e:
        raise ValueError(str(e)) from e
    db.update_task(conn, task["id"], depends_on=",".join(str(d) for d in deps))


def add_task(conn, project_id, title, description="", task_type="text",
             agent="auto", pos=None, usage_state=None, enabled=None,
             source_message_id=None):
    """팔레트에서 새 노드 추가. 선행 없이 만들어지고 연결은 사용자가 잇는다.

    source_message_id가 주어지면(채팅에서 태스크가 생성된 경우) 그 메시지의
    created_task_id를 채워, 클라이언트가 메시지 응답만 보고 탭을 자동으로
    열 수 있게 한다.
    """
    _editable_project(conn, project_id)
    if len(db.list_tasks(conn, project_id)) >= config.ORCH_MAX_TASKS:
        raise ValueError(f"태스크는 최대 {config.ORCH_MAX_TASKS}개까지입니다")
    title = (title or "").strip()
    if not title:
        raise ValueError("제목은 비울 수 없습니다")
    # 설명을 비워두면 제목을 그대로 쓴다 — 미디어 태스크는 설명이 곧 생성 프롬프트다.
    description = (description or "").strip() or title
    if task_type not in TASK_TYPES:
        raise ValueError(f"알 수 없는 type {task_type!r}")
    provider = _resolve_or_400(task_type, agent, description,
                               usage_state=usage_state, enabled=enabled)
    task_id = db.create_task(conn, project_id, db.next_task_seq(conn, project_id),
                             title, description, task_type, provider)
    db.get_or_create_board_tab(conn, project_id, title, "task", task_id,
                               status="pending", activate=False)
    if source_message_id is not None:
        db.update_message(conn, source_message_id, created_task_id=task_id)
    if pos is not None:
        move_task(conn, task_id, pos[0], pos[1])
    return task_id


def delete_task(conn, task_id):
    """태스크를 지우고, 형제들의 depends_on에서 그 seq를 떼어낸다."""
    _project, task = _editable_task(conn, task_id)
    project_id, seq = task["project_id"], task["seq"]
    db.delete_task(conn, task_id)
    for sib in db.list_tasks(conn, project_id):
        deps = _deps(sib)
        if seq in deps:
            db.update_task(conn, sib["id"],
                           depends_on=",".join(str(d) for d in deps if d != seq))


def move_task(conn, task_id, x, y):
    """노드 좌표만 기록한다. 실행에 영향이 없어 어떤 상태에서도 허용한다."""
    if db.get_task(conn, task_id) is None:
        raise ValueError("태스크를 찾을 수 없습니다")
    # 캔버스 원점은 (0,0) — 음수로 끌면 뷰박스 밖으로 사라지므로 잘라낸다.
    db.update_task(conn, task_id, pos_x=max(0.0, float(x)),
                   pos_y=max(0.0, float(y)))


def reset_layout(conn, project_id):
    """저장된 좌표를 지워 자동 배치로 되돌린다 (n8n의 'tidy up')."""
    if db.get_project(conn, project_id) is None:
        raise ValueError("프로젝트를 찾을 수 없습니다")
    for task in db.list_tasks(conn, project_id):
        db.update_task(conn, task["id"], pos_x=None, pos_y=None)


# --- 그래프 레이아웃 (n8n식 DAG, 서버 렌더링 SVG용) --------------------------

# 노드 글자를 키우고 제목을 두 줄까지 보여주기 위해 박스도 그만큼 키웠다
# (텍스트 좌표는 templates/partials/board.html이 이 크기에 맞춰 둔다).
NODE_W, NODE_H = 232, 86
GAP_X, GAP_Y = 80, 28
PAD = 24
ORIENTATIONS = ("lr", "tb")

# 모바일(tb)은 데스크톱 배치를 세로로 돌린 게 아니라 아예 다른 그림이다.
# 노드를 한 줄 세로 스택으로 쌓고(가로 스크롤 0), 캔버스 폭을 폰 화면 폭에
# 맞춰 두므로 화면 맞춤만 해도 노드가 1:1 크기로 읽힌다. 대신 폭이 남으므로
# 박스를 넓혀 제목을 세 줄까지 흘리고, 한 칸 건너뛰는 연결선은 왼쪽 레일로
# 빼서 노드 위를 지나가지 않게 한다.
MOBILE_NODE_W, MOBILE_NODE_H = 272, 108
MOBILE_GAP_Y = 34
LANE_W = 16   # 레일 한 칸 폭 (레일 4칸까지는 폰 화면 폭 안에 들어간다)
# 레일로 꺾일 때의 곡선 반경. 꺾임 한 번이 노드 사이 간격(2r) 안에서 끝나야
# 곡선이 아래 노드 박스 위를 지나가지 않는다.
LANE_R = MOBILE_GAP_Y // 2

# 제목 줄바꿈 — (첫 줄, 나머지 줄, 최대 줄 수). 첫 줄은 아이콘과 오른쪽 ⚠
# 자리를 비워 두므로 짧다. 글자 수는 .node-title(15px)과 박스 폭에서 왔다.
TITLE_WRAP = {"lr": (10, 12, 2), "tb": (13, 15, 3)}


def _saved_pos(task):
    """사용자가 옮겨 저장된 좌표. 없으면 None (dict/Row 둘 다 안전)."""
    try:
        x, y = task["pos_x"], task["pos_y"]
    except (KeyError, IndexError):
        return None
    return None if x is None or y is None else (float(x), float(y))


def _title_lines(title, orientation):
    """제목을 노드 박스 폭에 맞춰 잘라 줄 목록으로. 넘치면 마지막 줄에 말줄임."""
    first, rest, max_lines = TITLE_WRAP[orientation]
    lines, i = [], 0
    for k in range(max_lines):
        chunk = title[i:i + (first if k == 0 else rest)]
        if not chunk:
            break
        i += len(chunk)
        lines.append(chunk)
    if lines and len(title) > i:
        lines[-1] += "…"
    return lines or [""]


def _layers(tasks):
    """의존성 위상 깊이 → 그 깊이의 태스크들(seq 순)."""
    by_seq = {t["seq"]: t for t in tasks}
    depth_cache = {}

    def depth(seq, trail=()):
        if seq in depth_cache:
            return depth_cache[seq]
        if seq in trail:  # 순환은 parse_plan/set_task_deps에서 걸러지지만 방어
            return 0
        deps = [d for d in _deps(by_seq[seq]) if d in by_seq]
        d = 0 if not deps else 1 + max(depth(p, trail + (seq,)) for p in deps)
        depth_cache[seq] = d
        return d

    layers = {}
    for t in tasks:
        layers.setdefault(depth(t["seq"]), []).append(t)
    return {k: sorted(v, key=lambda t: t["seq"]) for k, v in sorted(layers.items())}


def _node(task, x, y, w, h, orientation):
    node = {**dict(task), "x": x, "y": y, "w": w, "h": h,
            "lines": _title_lines(task["title"], orientation)}
    # 연결 핸들(포트) 좌표 — 흐름 방향에 따라 좌↔우 또는 위↔아래
    if orientation == "tb":
        node["in_x"], node["in_y"] = x + w / 2, y
        node["out_x"], node["out_y"] = x + w / 2, y + h
    else:
        node["in_x"], node["in_y"] = x, y + h / 2
        node["out_x"], node["out_y"] = x + w, y + h / 2
    return node


def _layout_desktop(tasks):
    nodes = {}
    for layer_idx, line in _layers(tasks).items():
        for row_idx, t in enumerate(line):
            x = PAD + layer_idx * (NODE_W + GAP_X)
            y = PAD + row_idx * (NODE_H + GAP_Y)
            saved = _saved_pos(t)
            if saved:
                x, y = saved
            nodes[t["seq"]] = _node(t, x, y, NODE_W, NODE_H, "lr")

    edges = []
    for seq, child in nodes.items():
        for d in _deps(nodes[seq]):
            parent = nodes.get(d)
            if not parent:
                continue
            x1, y1 = parent["out_x"], parent["out_y"]
            x2, y2 = child["in_x"], child["in_y"]
            mx = (x1 + x2) / 2
            edges.append({"from": d, "to": seq, "done": parent["status"] == "done",
                          "path": f"M {x1} {y1} C {mx} {y1}, {mx} {y2}, {x2} {y2}"})
    return nodes, edges


def _rail_path(x1, y1, x2, y2, gx):
    """부모 아래 → 왼쪽 레일 → 자식 위로 도는 연결선(모서리는 둥글게)."""
    r = min(LANE_R, (y2 - y1) / 4)
    return (f"M {x1} {y1} "
            f"C {x1} {y1 + r}, {gx} {y1 + r}, {gx} {y1 + 2 * r} "
            f"L {gx} {y2 - 2 * r} "
            f"C {gx} {y2 - r}, {x2} {y2 - r}, {x2} {y2}")


def _layout_mobile(tasks):
    """한 줄 세로 스택 + 왼쪽 레일 배선. 저장된 좌표는 무시한다 —
    폰에서는 '읽고 따라가는 순서'가 '어디에 놓였는지'보다 중요하다."""
    order = [t for line in _layers(tasks).values() for t in line]
    rank = {t["seq"]: i for i, t in enumerate(order)}
    ys = {t["seq"]: PAD + i * (MOBILE_NODE_H + MOBILE_GAP_Y)
          for i, t in enumerate(order)}

    # 바로 아래 노드로 잇는 선은 그냥 내려 긋는다. 한 칸 이상 건너뛰는 선만
    # 왼쪽 레일로 빼고, 세로 구간이 겹치는 것끼리는 레인을 한 칸씩 더 왼쪽으로
    # 밀어 선이 포개지지 않게 한다.
    jumps, lane_of, lane_end = [], {}, []
    for t in tasks:
        for d in _deps(t):
            if d in rank and rank[t["seq"]] - rank[d] > 1:
                jumps.append((d, t["seq"]))
    for e in sorted(jumps, key=lambda e: ys[e[0]]):
        top, bottom = ys[e[0]] + MOBILE_NODE_H, ys[e[1]]
        lane = next((i for i, end in enumerate(lane_end) if end <= top),
                    len(lane_end))
        if lane == len(lane_end):
            lane_end.append(bottom)
        else:
            lane_end[lane] = bottom
        lane_of[e] = lane
    x = PAD + len(lane_end) * LANE_W
    # 레인 0이 노드에 가장 가깝고, 번호가 커질수록 왼쪽으로 나간다
    lane_x = {e: x - (lane_of[e] + 0.5) * LANE_W for e in lane_of}

    nodes = {t["seq"]: _node(t, x, ys[t["seq"]], MOBILE_NODE_W, MOBILE_NODE_H, "tb")
             for t in order}
    edges = []
    for t in tasks:
        child = nodes[t["seq"]]
        for d in _deps(t):
            parent = nodes.get(d)
            if not parent:
                continue
            x1, y1 = parent["out_x"], parent["out_y"]
            x2, y2 = child["in_x"], child["in_y"]
            e = (d, t["seq"])
            if e in lane_x:
                path = _rail_path(x1, y1, x2, y2, lane_x[e])
            else:
                my = (y1 + y2) / 2
                path = f"M {x1} {y1} C {x1} {my}, {x2} {my}, {x2} {y2}"
            edges.append({"from": d, "to": t["seq"], "path": path,
                          "done": parent["status"] == "done"})
    return nodes, edges


def layout_graph(tasks, orientation="lr"):
    """태스크 목록 → SVG 좌표. 순수 함수.

    orientation="lr" (데스크톱): 레이어 = 의존성 위상 깊이(왼→오), 레이어 안에서는
    seq 순 세로 배치. 사용자가 옮긴 노드(pos_x/pos_y)는 그 좌표를 그대로 쓴다.
    orientation="tb" (모바일): 노드 한 줄 세로 스택 + 왼쪽 레일 배선. 폰 화면
    폭에 맞춘 캔버스라 확대 없이 읽히고, 저장된 좌표는 무시한다.

    반환: {"nodes": [{...task fields, x, y, w, h, lines, in_x, in_y, out_x, out_y}],
           "edges": [{"from": seq, "to": seq, "path": "M..C..", "done": bool}],
           "width": int, "height": int, "orientation": str}
    """
    vertical = orientation == "tb"
    nodes, edges = _layout_mobile(tasks) if vertical else _layout_desktop(tasks)

    # 캔버스는 자동 격자가 아니라 실제 노드 경계에서 잰다 — 사용자가 노드를
    # 바깥으로 끌면 캔버스도 따라 넓어져야 한다.
    right = max((n["x"] + n["w"] for n in nodes.values()), default=0)
    bottom = max((n["y"] + n["h"] for n in nodes.values()), default=0)
    return {"nodes": list(nodes.values()), "edges": edges,
            "width": max(int(right + PAD), 300),
            "height": max(int(bottom + PAD), 160),
            "orientation": "tb" if vertical else "lr"}
