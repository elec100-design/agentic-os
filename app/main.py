import asyncio
import json
import os
import re
from contextlib import asynccontextmanager

import jinja2
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import (
    HTMLResponse,
    PlainTextResponse,
    RedirectResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from app import (
    codexbar, config, council, db, github_cli, health, i18n, memory, models,
    orchestrator, settings, setup, stream_hub, workspace, worker,
)
from app.providers import COUNCIL, PROVIDERS, route_auto

BASE = Path(__file__).resolve().parent.parent


def _lang_context(request):
    """렌더 직전(같은 스레드)에 요청 언어를 정한다: 쿠키 > Accept-Language > 영어.
    contextvar를 렌더 스레드에서 직접 설정해 `t` 필터·`lang` 글로벌이 일관되게
    같은 값을 본다 (async 미들웨어→스레드풀 전파의 불확실성 회피)."""
    i18n.set_lang(i18n.resolve_lang(
        cookie=request.cookies.get("aos-lang"),
        accept_language=request.headers.get("accept-language"),
    ))
    return {}


templates = Jinja2Templates(directory=str(BASE / "templates"),
                            context_processors=[_lang_context])


# UI 국제화: 템플릿에서 `{{ '한국어' | t }}`로 번역, `{{ lang() }}`로 현재 언어,
# `{{ i18n_map() }}`로 JS에 넘길 번역 카탈로그를 얻는다.
# 주의: Jinja는 상수에 적용된 필터(`'자동' | t`)를 컴파일 타임에 상수로
# 접어버려(constant folding) 최초 컴파일 시점의 언어가 고정된다. pass_context로
# 표시하면 런타임 컨텍스트가 필요해 컴파일 타임에 접을 수 없으므로 매 렌더마다
# 실제로 호출된다(요청 언어가 반영됨).
@jinja2.pass_context
def _t_filter(_ctx, text):
    return i18n.t(text)


templates.env.filters["t"] = _t_filter
templates.env.globals["lang"] = i18n.get_lang
templates.env.globals["i18n_map"] = lambda: i18n.EN


def asset_version():
    """정적 파일(style.css/app.js) 캐시 무효화용 버전 = 최신 수정시각.
    수정 후 서비스 재시작(=배포)마다 값이 바뀌어 브라우저가 새로 받는다."""
    d = BASE / "static"
    try:
        return str(int(max(f.stat().st_mtime for f in d.glob("*.*"))))
    except (ValueError, OSError):
        return "0"


# 템플릿에서 ?v={{ asset_v() }} 로 사용 (모든 템플릿 공통)
templates.env.globals["asset_v"] = asset_version

# same-origin POST는 포트 무관 허용된다. tailscale serve처럼 프록시가 Host를
# 다시 쓰는 경우(예: <host>.<tailnet>.ts.net)를 대비해, 신뢰하는 프록시 호스트는
# AOS_EXTRA_ORIGINS(콤마 구분) 환경변수로 명시 허용한다.
ALLOWED_ORIGINS = {f"localhost:{config.PORT}", f"127.0.0.1:{config.PORT}"}
ALLOWED_ORIGINS |= {
    o.strip() for o in os.environ.get("AOS_EXTRA_ORIGINS", "").split(",") if o.strip()
}


@asynccontextmanager
async def lifespan(app):
    stop = asyncio.Event()
    tasks = []
    if not os.environ.get("AOS_DISABLE_WORKER"):
        # 기동 직후 모델 목록을 한 번 채워 구 하드코딩·빈 캐시를 바로 덮어쓴다.
        try:
            models.write_cache(await models.fetch())
        except Exception:
            pass
        # 그룹 없는 기존 노트를 작업 위치 기준으로 소급 자동 그룹핑 (멱등)
        memory.backfill_auto_groups(db.list_note_workdirs(db.get_conn()))
        tasks.append(asyncio.create_task(worker.worker_loop(stop)))
        tasks.append(asyncio.create_task(orchestrator.orchestrator_loop(stop)))
        tasks.append(asyncio.create_task(codexbar.refresh_loop(stop)))
        tasks.append(asyncio.create_task(models.refresh_loop(stop)))
    yield
    stop.set()
    for task in tasks:
        task.cancel()


class NoCacheStaticFiles(StaticFiles):
    """정적 파일에 no-cache를 붙여 브라우저가 매번 재검증(변경 시 새로 받음)하게 한다."""
    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        resp.headers["Cache-Control"] = "no-cache"
        return resp


app = FastAPI(lifespan=lifespan)
app.mount("/static", NoCacheStaticFiles(directory=str(BASE / "static")), name="static")


@app.middleware("http")
async def set_request_language(request, call_next):
    """요청마다 UI 언어를 정한다: 쿠키(aos-lang) > Accept-Language > 영어."""
    i18n.set_lang(i18n.resolve_lang(
        cookie=request.cookies.get("aos-lang"),
        accept_language=request.headers.get("accept-language"),
    ))
    return await call_next(request)


@app.get("/lang/{code}")
def set_language(code: str, request: Request):
    """언어 전환 — 쿠키에 저장하고 이전 페이지로 돌아간다(JS 없이 동작)."""
    code = code if code in i18n.LANGS else i18n.DEFAULT_LANG
    back = request.headers.get("referer") or "/"
    resp = RedirectResponse(back, status_code=303)
    resp.set_cookie("aos-lang", code, max_age=60 * 60 * 24 * 365,
                    samesite="lax")
    return resp


@app.middleware("http")
async def block_cross_origin_posts(request, call_next):
    if request.method == "POST":
        origin = request.headers.get("origin")
        if origin:
            origin_host = urlparse(origin).netloc
            # 진짜 same-origin(Origin 호스트 == 요청 Host)이면 포트와 무관하게 허용.
            # 명시 허용목록도 함께 인정. 그 외 크로스사이트 POST는 차단.
            same_origin = origin_host == request.headers.get("host", "")
            if not same_origin and origin_host not in ALLOWED_ORIGINS:
                return PlainTextResponse("forbidden", status_code=403)
    return await call_next(request)


def _remaining_str(until, now):
    """until(ISO 문자열 또는 datetime)까지 남은 시간을 '1시간 23분' 형태로."""
    if until is None:
        return None
    if isinstance(until, str):
        try:
            until = datetime.fromisoformat(until.replace("Z", "+00:00"))
        except ValueError:
            return None
    if until.tzinfo is None:
        until = until.replace(tzinfo=timezone.utc)
    secs = (until - now).total_seconds()
    en = i18n.get_lang() == "en"
    if secs <= 0:
        return "soon" if en else "곧"
    hours, mins = int(secs // 3600), int(secs % 3600 // 60)
    if hours:
        return f"{hours}h {mins}m" if en else f"{hours}시간 {mins}분"
    return f"{max(mins, 1)}m" if en else f"{max(mins, 1)}분"


def usage_state(now=None):
    """CodexBar 캐시를 읽어 각 프로바이더의 표시/라우팅용 상태를 만든다.
    hermes는 로컬·무제한. 캐시가 없거나 프로바이더에 에러가 있으면 정보없음 상태."""
    now = now or datetime.now(timezone.utc)
    cache = codexbar.read_cache()
    cached = cache.get("providers", {})
    providers = settings.enabled_providers()
    state = {}
    for p in providers:
        if p == "hermes":
            state[p] = {"source": "local", "used": None, "remaining": None,
                        "available": True, "windows": [], "error": None,
                        "resume_in": None}
            continue
        c = cached.get(p)
        if not c or c.get("error") or c.get("used") is None:
            state[p] = {"source": "none", "used": None, "remaining": None,
                        "available": None, "windows": [],
                        "error": (c or {}).get("error") or "사용량 정보 없음",
                        "resume_in": None}
            continue
        windows = []
        for w in c.get("windows", []):
            windows.append({
                "title": w.get("title"), "used": w.get("used"),
                "resume_in": _remaining_str(w.get("resetsAt"), now),
            })
        state[p] = {
            "source": "codexbar", "used": c.get("used"),
            "remaining": c.get("remaining"), "available": c.get("available"),
            "windows": windows, "error": None,
            "resume_in": _remaining_str(c.get("resetsAt"), now),
        }
    return {"providers": providers, "usage": state,
            "updated_in": _ago_str(cache.get("updatedAt"), now)}


def _ago_str(iso, now):
    en = i18n.get_lang() == "en"
    if not iso:
        return "Awaiting update" if en else "갱신 대기"
    dt = None
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return "Awaiting update" if en else "갱신 대기"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    secs = int((now - dt).total_seconds())
    if secs < 60:
        return "just now" if en else "방금"
    if secs < 3600:
        return f"{secs // 60}m ago" if en else f"{secs // 60}분 전"
    return f"{secs // 3600}h ago" if en else f"{secs // 3600}시간 전"


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    # 첫 실행이면 셋업 위저드로. `/`만 리다이렉트한다 — partials/API/jobs는
    # 그대로 두어 HTMX 폴링·딥링크가 깨지지 않고, 리다이렉트 루프도 없다.
    if not settings.setup_completed():
        return RedirectResponse("/setup")
    return templates.TemplateResponse(
        request, "index.html",
        {"provider_models": models.get_provider_models(),
         "agent_order": settings.enabled_providers(),
         "council_enabled": settings.council_available()},
    )


# --- 첫 실행 셋업 위저드 ---

@app.get("/setup", response_class=HTMLResponse)
def setup_page(request: Request):
    current = settings.load()
    return templates.TemplateResponse(
        request, "setup.html",
        {"current": current,
         "memory_dir": str(config.MEMORY_DIR),
         "vault_set": bool(os.environ.get("AOS_VAULT_PATH", "").strip()),
         "council_members": config.COUNCIL_MEMBERS,
         "council_min": config.COUNCIL_MIN_MEMBERS},
    )


@app.get("/api/health")
def api_health():
    """서버·CLI·설정 진단. `aos doctor`도 이 정보를 사용한다."""
    return health.collect()


@app.get("/api/setup/status")
def api_setup_status():
    """CLI·보조 도구 설치 상태 (재확인 버튼이 수시 호출 — binary 존재만 확인)."""
    return setup.detect()


@app.post("/api/setup/complete")
def api_setup_complete(providers: list[str] = Form(default=[])):
    try:
        saved = settings.save(providers)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "enabled": saved["enabled_providers"], "redirect": "/"}


async def _save_uploads(files):
    saved = []
    max_bytes = config.MAX_UPLOAD_MB * 1024 * 1024
    for f in files or []:
        if not f.filename:
            continue
        data = await f.read()
        if len(data) > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"파일이 너무 큽니다 (최대 {config.MAX_UPLOAD_MB}MB): {f.filename}",
            )
        config.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^\w.\-가-힣]", "_", Path(f.filename).name)
        dest = config.UPLOAD_DIR / f"{datetime.now():%Y%m%d-%H%M%S%f}-{safe}"
        dest.write_bytes(data)
        saved.append(dest)
    return saved


@app.post("/jobs")
async def create_job(
    request: Request,
    prompt: str = Form(...),
    provider: str = Form("auto"),
    model: str = Form(""),
    workdir: str = Form(""),
    attach_memory: bool = Form(False),
    timeout_min: int | None = Form(None),
    session_id: str = Form(""),
    resume_provider: str = Form(""),
    origin_note: str = Form(""),
    context_note: str = Form(""),
    files: list[UploadFile] = File(default=[]),
):
    conn = db.get_conn()
    enabled = settings.enabled_providers()
    route_reason = None
    if provider == "auto":
        # 자동 라우팅으로 정해진 provider와 그 이유를 함께 기록해 둔다
        # (작업 히스토리에서 "왜 이 에이전트로 갔는지" 확인용).
        provider, route_reason = route_auto(
            prompt, usage_state=usage_state()["usage"], enabled=enabled)
    if provider == COUNCIL:
        # 협의 모드: 가용 에이전트가 최소 인원 이상인지 미리 확인.
        # 세션 재개·모델·작업 위치는 지원하지 않는다 (매 호출 stateless,
        # 여러 CLI가 같은 폴더에 동시에 쓰면 충돌할 수 있음).
        try:
            council.select_members(usage_state()["usage"], enabled=enabled)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        model = ""
        workdir = ""
        session_id = ""
    elif provider not in PROVIDERS:
        raise HTTPException(status_code=400, detail="unknown provider")
    elif provider not in enabled:
        raise HTTPException(
            status_code=400,
            detail="비활성화된 에이전트입니다. /setup 에서 활성화하세요")
    # 헤드리스 세션 재개가 불가능한 CLI → 넘어온 session_id를 무시하고 항상
    # 새 대화로 처리(오염 방지, UI 우회 제출 방어).
    # - antigravity(agy): 헤드리스 세션 ID 부재
    # - gemini: --resume 이 latest/index 전용(UUID 재개 없음)
    if provider in ("antigravity", "gemini"):
        session_id = ""
    if not models.is_valid_model(provider, model):
        model = ""
    # 등록된 작업 위치만 cwd로 허용 (임의 경로 실행 방지)
    if not workspace.valid_path(workdir):
        workdir = ""
    # 노트에서 세션 이어가기 중 에이전트를 바꾼 경우: session_id는 원본
    # provider 전용이라 다른 CLI로는 재개할 수 없다. 대신 같은 작업 위치에서
    # 노트를 컨텍스트로 붙여 새 에이전트로 이어간다(멀티에이전트 폴백).
    if session_id.strip() and resume_provider and provider != resume_provider:
        if origin_note and not context_note:
            context_note = origin_note
        session_id = ""
    # 세션 이어가기일 때만 원본 노트를 스레드 노트로 인정 → 결과를 이어 쓴다
    if not (session_id.strip() and memory.is_managed(origin_note)):
        origin_note = ""
    if context_note:
        note = memory.read_note(context_note)
        if note:
            prompt = (f"다음 과거 노트를 참고하세요:\n\n### {note['name']}\n"
                      f"{note['body'][:4000]}\n\n---\n\n") + prompt
    if attach_memory:
        prompt = memory.build_context(prompt) + prompt
    uploads = await _save_uploads(files)
    if uploads:
        prompt += "\n\n첨부 파일 (로컬 경로에서 읽을 것):\n" + "\n".join(
            f"- {p}" for p in uploads
        )
    job_id = db.create_job(
        conn, prompt, provider,
        timeout_sec=timeout_min * 60 if timeout_min else None,
        session_id=session_id.strip() or None,
        model=model or None,
        workdir=workdir or None,
        note_path=str(Path(origin_note).resolve()) if origin_note else None,
        route_reason=route_reason)
    # 탭 안에서 이어 보낸 후속 작업은 페이지를 떠나지 않는다 — 새 작업 id만 받아
    # 그 자리에서 새 탭으로 연다.
    if "application/json" in (request.headers.get("accept") or ""):
        return {"job_id": job_id}
    return RedirectResponse("/", status_code=303)


@app.get("/api/recommend")
def api_recommend(prompt: str = ""):
    """자동 모드 코칭: 지금 이 프롬프트를 자동으로 보내면 어느 에이전트로
    가는지와 그 이유를 JSON으로 돌려준다 (컴포저에서 실시간 표시)."""
    provider, reason = route_auto(prompt or " ", usage_state=usage_state()["usage"],
                                  enabled=settings.enabled_providers())
    return {"provider": provider, "reason": reason}


# --- 채널: 주제별 지속 대화(쓰레드 답장) ------------------------------------
# 사이드바 히스토리의 단발성 잡과 달리, 채널 안 대화는 messages 테이블에
# 쌓이고 답장이 parent_id로 쓰레드를 이룬다. 실제 CLI 실행은 여전히 jobs가
# 담당하며(worker.py), messages.job_id로 연결된다.

class ChannelCreate(BaseModel):
    title: str
    topic: str = ""
    workdir: str | None = None
    default_provider: str | None = None


class ChannelUpdate(BaseModel):
    title: str | None = None
    topic: str | None = None
    default_provider: str | None = None


class MessageCreate(BaseModel):
    body: str
    provider: str = "auto"
    model: str = ""
    parent_id: int | None = None


class TestGoalCreate(BaseModel):
    name: str


class BoardTabCreate(BaseModel):
    title: str
    kind: str = "artifact"
    ref_id: int | None = None
    status: str = "pending"


class BoardTabRename(BaseModel):
    title: str


class BoardTabOrder(BaseModel):
    tab_ids: list[int]


class TaskUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    task_type: str | None = None
    agent: str | None = None
    model: str | None = None
    extra_instruction: str | None = None
    depends_on: list[int] | None = None
    reset_status: bool = False


class TaskRetry(BaseModel):
    agent: str | None = None
    model: str | None = None
    instruction: str | None = None
    cascade: bool = False


class TaskMessageCreate(BaseModel):
    content: str


@app.post("/api/test-goal", status_code=201)
async def api_create_test_goal(payload: TestGoalCreate):
    conn = db.get_conn()
    goal_id = db.create_test_goal(conn, payload.name)
    await worker.run_test_goal(conn, goal_id)
    return {"id": goal_id, "status": "pending"}


@app.get("/api/test-goal/{goal_id}")
def api_get_test_goal(goal_id: int):
    conn = db.get_conn()
    goal = db.get_test_goal(conn, goal_id)
    if goal is None:
        raise HTTPException(status_code=404)
    return {"id": goal["id"], "status": goal["status"], "result": goal["result"]}


@app.post("/api/channels", status_code=201)
def api_create_channel(payload: ChannelCreate):
    conn = db.get_conn()
    workdir = payload.workdir if payload.workdir and workspace.valid_path(
        payload.workdir) else None
    channel_id = db.create_channel(
        conn, payload.title, topic=payload.topic, workdir=workdir,
        default_provider=payload.default_provider or None)
    return dict(db.get_channel(conn, channel_id))


@app.get("/api/channels")
def api_list_channels(status: str = "active"):
    conn = db.get_conn()
    rows = db.list_channels(conn, status=status or None)
    return [dict(r) for r in rows]


@app.get("/api/channels/{channel_id}")
def api_get_channel(channel_id: int, limit: int = 50):
    conn = db.get_conn()
    channel = db.get_channel(conn, channel_id)
    if channel is None:
        raise HTTPException(status_code=404)
    rows = db.list_root_messages(conn, channel_id, limit=limit)
    return {"channel": dict(channel), "messages": [dict(r) for r in rows]}


@app.patch("/api/channels/{channel_id}")
def api_update_channel(channel_id: int, payload: ChannelUpdate):
    conn = db.get_conn()
    if db.get_channel(conn, channel_id) is None:
        raise HTTPException(status_code=404)
    fields = {k: v for k, v in payload.model_dump().items() if v is not None}
    if fields:
        db.update_channel(conn, channel_id, **fields)
    return dict(db.get_channel(conn, channel_id))


@app.post("/api/channels/{channel_id}/archive")
def api_archive_channel(channel_id: int):
    conn = db.get_conn()
    if db.get_channel(conn, channel_id) is None:
        raise HTTPException(status_code=404)
    db.update_channel(conn, channel_id, status="archived")
    return dict(db.get_channel(conn, channel_id))


@app.delete("/api/channels/{channel_id}")
def api_delete_channel(channel_id: int):
    conn = db.get_conn()
    if db.get_channel(conn, channel_id) is None:
        raise HTTPException(status_code=404)
    db.delete_channel(conn, channel_id)
    return {"status": "deleted"}


@app.get("/api/channels/{channel_id}/messages")
def api_list_channel_messages(channel_id: int, before_seq: int | None = None,
                              limit: int = 50):
    conn = db.get_conn()
    if db.get_channel(conn, channel_id) is None:
        raise HTTPException(status_code=404)
    rows = db.list_root_messages(conn, channel_id, before_seq=before_seq,
                                 limit=limit)
    return [dict(r) for r in rows]


@app.post("/api/channels/{channel_id}/messages", status_code=202)
def api_create_channel_message(channel_id: int, payload: MessageCreate):
    conn = db.get_conn()
    channel = db.get_channel(conn, channel_id)
    if channel is None:
        raise HTTPException(status_code=404)
    if not payload.body.strip():
        raise HTTPException(status_code=400, detail="empty body")

    provider = payload.provider or channel["default_provider"] or "auto"
    enabled = settings.enabled_providers()
    route_reason = None
    if provider == "auto":
        provider, route_reason = route_auto(
            payload.body, usage_state=usage_state()["usage"], enabled=enabled)
    if provider == COUNCIL:
        try:
            council.select_members(usage_state()["usage"], enabled=enabled)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
    elif provider not in PROVIDERS:
        raise HTTPException(status_code=400, detail="unknown provider")
    elif provider not in enabled:
        raise HTTPException(
            status_code=400,
            detail="비활성화된 에이전트입니다. /setup 에서 활성화하세요")

    model = payload.model or ""
    if not models.is_valid_model(provider, model):
        model = ""
    workdir = channel["workdir"]
    if not workdir or not workspace.valid_path(workdir):
        workdir = None

    # parent_id는 항상 쓰레드 루트를 직접 가리키도록 정규화해서 넘긴다.
    thread_root_id = None
    session_id = None
    if payload.parent_id:
        parent = db.get_message(conn, payload.parent_id)
        if parent is None or parent["channel_id"] != channel_id:
            raise HTTPException(status_code=404, detail="parent message not found")
        thread_root_id = parent["root_id"] or parent["id"]
        inherited_session, inherited_provider = db.latest_thread_session(
            conn, thread_root_id)
        if inherited_session and inherited_provider == provider:
            session_id = inherited_session

    user_message_id = db.create_message(
        conn, channel_id, role="user", body=payload.body, author="user",
        parent_id=thread_root_id, status="done")

    # 채널의 새 루트 대화라면(부모 없음) 방금 만든 사용자 메시지가 쓰레드
    # 루트가 되고, 에이전트 응답은 그 아래 첫 답장으로 들어간다.
    agent_parent_id = thread_root_id or user_message_id
    agent_message_id = db.create_message(
        conn, channel_id, role="agent", body="", author=provider,
        parent_id=agent_parent_id, status="queued", provider=provider,
        model=model or None)

    job_id = db.create_job(
        conn, payload.body, provider, session_id=session_id,
        model=model or None, workdir=workdir, route_reason=route_reason,
        channel_id=channel_id, message_id=agent_message_id)
    db.update_message(conn, agent_message_id, job_id=job_id)

    return {"message_id": agent_message_id, "job_id": job_id,
            "user_message_id": user_message_id}


@app.get("/api/messages/{message_id}")
def api_get_message(message_id: int):
    conn = db.get_conn()
    message = db.get_message(conn, message_id)
    if message is None:
        raise HTTPException(status_code=404)
    return dict(message)


@app.get("/api/messages/{message_id}/thread")
def api_message_thread(message_id: int):
    conn = db.get_conn()
    message = db.get_message(conn, message_id)
    if message is None:
        raise HTTPException(status_code=404)
    root_id = message["root_id"] or message["id"]
    rows = db.list_thread(conn, root_id)
    return {"root_id": root_id, "messages": [dict(r) for r in rows]}


@app.post("/api/messages/{message_id}/cancel")
def api_cancel_message(message_id: int):
    conn = db.get_conn()
    message = db.get_message(conn, message_id)
    if message is None:
        raise HTTPException(status_code=404)
    if message["job_id"]:
        db.update_job(conn, message["job_id"], status="failed",
                      error="cancelled", finished_at=db.now_iso())
        worker.terminate_job_procs(message["job_id"])
    db.update_message(conn, message_id, status="canceled",
                      finished_at=db.now_iso())
    return dict(db.get_message(conn, message_id))


@app.get("/api/messages/{message_id}/trace")
def api_message_trace(message_id: int):
    conn = db.get_conn()
    message = db.get_message(conn, message_id)
    if message is None:
        raise HTTPException(status_code=404)
    steps = db.list_execution_steps(conn, message_id)
    return {"message": dict(message), "steps": [dict(s) for s in steps]}


@app.get("/api/messages/{message_id}/stream")
async def api_message_stream(message_id: int):
    conn = db.get_conn()
    message = db.get_message(conn, message_id)
    if message is None:
        raise HTTPException(status_code=404)
    job_id = message["job_id"]

    async def gen():
        # 잡이 아직 없거나(방금 큐잉) 이미 있으면 job_id로 구독한다. 잡이
        # 나중에 재시도로 새로 생기는 경우는 없다 — 메시지당 잡 1개.
        q = stream_hub.subscribe(job_id) if job_id else None
        sent_len: dict[int, int] = {}
        last_status = None
        try:
            while True:
                conn = db.get_conn()
                msg = db.get_message(conn, message_id)
                if msg is None:
                    break
                for step in db.list_execution_steps(conn, message_id):
                    prev = sent_len.get(step["id"], -1)
                    cur = len(step["detail"] or "")
                    if cur != prev or (prev == -1 and step["status"] != "running"):
                        payload = {
                            "id": step["id"], "seq": step["seq"],
                            "kind": step["kind"], "title": step["title"],
                            "status": step["status"], "detail": step["detail"],
                        }
                        yield f"event: step\ndata: {json.dumps(payload)}\n\n"
                        sent_len[step["id"]] = cur
                if msg["status"] != last_status:
                    yield f"event: status\ndata: {json.dumps(msg['status'])}\n\n"
                    last_status = msg["status"]
                if msg["status"] in ("done", "failed", "canceled"):
                    yield "event: done\ndata: {}\n\n"
                    break
                if q is not None:
                    try:
                        await asyncio.wait_for(q.get(), timeout=config.STREAM_POLL_SEC)
                    except asyncio.TimeoutError:
                        pass
                else:
                    await asyncio.sleep(config.STREAM_POLL_SEC)
        finally:
            if job_id and q is not None:
                stream_hub.unsubscribe(job_id, q)

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/partials/channels", response_class=HTMLResponse)
def partials_channels(request: Request):
    """사이드바 채널 목록: 워크스페이스 채널 + 실행 중인 액티브 세션(루트 메시지)을
    계층으로 보여준다. 진행 중인 세션이 있는 채널이 먼저 오도록 정렬한다."""
    conn = db.get_conn()
    rows = []
    for c in db.list_channels(conn, status="active"):
        roots = db.list_root_messages(conn, c["id"], limit=8)
        active = []
        for root in roots:
            thread = db.list_thread(conn, root["id"])
            last = thread[-1] if thread else root
            if last["status"] in ("queued", "running"):
                active.append({**dict(root), "status": last["status"]})
        rows.append({"channel": dict(c), "active": active})
    rows.sort(key=lambda r: bool(r["active"]), reverse=True)
    return templates.TemplateResponse(
        request, "partials/channels.html", {"rows": rows})


@app.get("/channels/{channel_id}", response_class=HTMLResponse)
def channel_page(request: Request, channel_id: int):
    conn = db.get_conn()
    channel = db.get_channel(conn, channel_id)
    if channel is None:
        raise HTTPException(status_code=404)
    roots = db.list_root_messages(conn, channel_id, limit=200)
    threads = [[dict(m) for m in db.list_thread(conn, r["id"])] for r in roots]
    return templates.TemplateResponse(
        request, "channel.html",
        {"channel": dict(channel), "threads": threads,
         "provider_models": models.get_provider_models(),
         "agent_order": settings.enabled_providers()})


@app.get("/note", response_class=HTMLResponse)
def note_view(request: Request, path: str):
    note = memory.read_note(path)
    if note is None:
        raise HTTPException(status_code=404)
    # hermes(로컬·무상태)·antigravity/gemini(헤드리스 UUID 재개 불가)는 이어가기 미지원.
    can_resume = bool(
        note["session_id"] and note["provider"] in PROVIDERS
        and note["provider"] not in ("hermes", "antigravity", "gemini")
    )
    pm = models.get_provider_models()
    order = settings.enabled_providers()
    agents = [p for p in order if p in pm] or list(pm)
    return templates.TemplateResponse(
        request, "note.html",
        {"note": note, "can_resume": can_resume,
         "provider_models": pm, "agents": agents,
         "turns": memory.parse_thread(note["body"])},
    )


def _job_view_ctx(job, embed_followup=True):
    """작업 상세 뷰(단독 페이지·홈 탭 공용)의 렌더 컨텍스트.

    thread: 이 작업 '이전'의 대화 턴들 — 연결된 스레드 노트에서 읽는다. 노트는
    작업이 끝난 뒤에 기록되므로, 끝난 작업이면 노트의 마지막 한 쌍이 이 작업
    자신이라 잘라낸다(프롬프트로 확인).
    can_resume: 같은 세션을 이어받아 후속 작업을 보낼 수 있는지.
    """
    thread = []
    if job["note_path"]:
        note = memory.read_note(job["note_path"])
        if note:
            thread = memory.parse_thread(note["body"])
            if (len(thread) >= 2 and thread[-2]["role"] == "user"
                    and thread[-2]["content"].strip() == (job["prompt"] or "").strip()):
                thread = thread[:-2]
    # note_view와 같은 기준 — hermes(무상태)·antigravity/gemini(UUID 재개 불가)는
    # 세션을 이어받을 수 없어 노트를 컨텍스트로 붙이는 폴백만 제공한다.
    can_resume = bool(
        job["status"] in ("done", "failed")
        and job["session_id"] and job["provider"] in PROVIDERS
        and job["provider"] not in ("hermes", "antigravity", "gemini")
    )
    can_follow_up = bool(
        job["status"] in ("done", "failed")
        and job["provider"] in PROVIDERS
        and (can_resume or job["note_path"])
    )
    # embed_followup=False면 후속 지시 컴포저를 본문에 넣지 않는다 — 홈 대시보드는
    # 중앙 탭을 결과 전용으로 두고 우측 레일에서 편집한다(/partials/job/{id}/followup).
    return {"job": job, "thread": thread,
            "can_resume": can_resume, "can_follow_up": can_follow_up,
            "embed_followup": embed_followup}


@app.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_detail(request: Request, job_id: int):
    conn = db.get_conn()
    job = db.get_job(conn, job_id)
    if job is None:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(request, "job.html", _job_view_ctx(job))


@app.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: int):
    conn = db.get_conn()
    if db.get_job(conn, job_id) is None:
        raise HTTPException(status_code=404)
    db.update_job(conn, job_id, status="failed", error="cancelled",
                  finished_at=db.now_iso())
    worker.terminate_job_procs(job_id)
    return RedirectResponse("/", status_code=303)


@app.get("/jobs/{job_id}/stream")
async def stream_job(job_id: int):
    if db.get_job(db.get_conn(), job_id) is None:
        raise HTTPException(status_code=404)

    async def gen():
        # 첫 DB 조회 '전'에 구독을 시작한다 → 구독과 조회 사이에 도착한 출력도
        # 신호로 큐에 남아 다음 대기에서 즉시 회수된다(유실 없음).
        q = stream_hub.subscribe(job_id)
        sent = 0
        try:
            while True:
                conn = db.get_conn()
                job = db.get_job(conn, job_id)
                if job is None:
                    break
                out = job["output"]
                if len(out) > sent:
                    yield f"data: {json.dumps(out[sent:])}\n\n"
                    sent = len(out)
                if job["status"] in ("done", "failed"):
                    yield f"event: status\ndata: {job['status']}\n\n"
                    break
                # 워커가 같은 프로세스면 신호로 즉시 깨어나 DB를 다시 읽는다.
                # 신호가 없으면(워커 프로세스 분리) 타임아웃마다 폴링하는 기존
                # 동작으로 자연 폴백한다.
                try:
                    await asyncio.wait_for(q.get(), timeout=config.STREAM_POLL_SEC)
                except asyncio.TimeoutError:
                    pass
        finally:
            stream_hub.unsubscribe(job_id, q)

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/jobs/{job_id}/delete", response_class=HTMLResponse)
def delete_job_endpoint(request: Request, job_id: int):
    conn = db.get_conn()
    job = db.get_job(conn, job_id)
    if job is None:
        raise HTTPException(status_code=404)
    # 실행 중이면 먼저 중단
    worker.terminate_job_procs(job_id)
    # 연결된 노트도 함께 삭제 (작업큐↔메모리 연동)
    # 단, 다른 작업(스레드)이 같은 노트를 공유하면 노트는 남긴다
    if (job["note_path"] and memory.is_managed(job["note_path"])
            and db.jobs_sharing_note(conn, job["note_path"], job_id) == 0):
        try:
            memory.delete_note(job["note_path"])
        except (OSError, ValueError):
            pass
    db.delete_job(conn, job_id)
    resp = templates.TemplateResponse(
        request, "partials/jobs.html", {"jobs": db.list_jobs(conn)}
    )
    resp.headers["HX-Trigger"] = "refresh-memory"   # 사이드바 메모리도 갱신
    return resp


@app.api_route("/partials/job/{job_id}", methods=["GET", "HEAD"], response_class=HTMLResponse)
def partial_job(request: Request, job_id: int):
    """작업 상세 조각 — 홈 중앙 워크스페이스가 탭 내용으로 불러온다.

    HEAD도 받는다 — home.js가 새로고침 후 탭을 복원할 때 작업이 아직 존재하는지
    본문 없이 확인한다(partial_task와 같은 계약)."""
    conn = db.get_conn()
    job = db.get_job(conn, job_id)
    if job is None:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request, "partials/job_view.html", _job_view_ctx(job, embed_followup=False))


@app.get("/partials/job/{job_id}/followup", response_class=HTMLResponse)
def partial_job_followup(request: Request, job_id: int):
    """후속 지시 컴포저만 따로 — 홈 우측 레일의 작업 인스펙터가 붙인다.

    히든 필드(provider/model/workdir/session_id/context_note)는 이미
    _job_view_ctx가 계산하므로 JSON API 대신 서버 렌더 조각을 그대로 준다.
    아직 실행 중이라 이어갈 수 없으면 빈 본문이 온다."""
    conn = db.get_conn()
    job = db.get_job(conn, job_id)
    if job is None:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request, "partials/job_followup.html", _job_view_ctx(job))


@app.get("/partials/jobs", response_class=HTMLResponse)
def partial_jobs(request: Request):
    conn = db.get_conn()
    return templates.TemplateResponse(
        request, "partials/jobs.html", {"jobs": db.list_jobs(conn)}
    )


@app.get("/partials/workspaces", response_class=HTMLResponse)
def partial_workspaces(request: Request):
    return templates.TemplateResponse(
        request, "partials/workspaces.html",
        {"workspaces": workspace.list_workspaces()},
    )


def _workspaces_response(request, selected_path=None):
    resp = templates.TemplateResponse(
        request, "partials/workspaces.html",
        {"workspaces": workspace.list_workspaces()},
    )
    if selected_path:
        # 방금 추가한 위치를 select에서 자동 선택하도록 경로를 헤더로 전달
        resp.headers["X-Workspace-Path"] = selected_path
    return resp


@app.post("/workspaces/add", response_class=HTMLResponse)
def workspace_add(request: Request, value: str = Form(...), name: str = Form("")):
    value = value.strip()
    if value and not config.is_browse_allowed(value):
        raise HTTPException(
            status_code=400,
            detail="허용된 범위(홈·iCloud Drive) 밖의 폴더는 등록할 수 없습니다",
        )
    try:
        ws = workspace.add(name.strip(), value)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _workspaces_response(request, ws["path"])


@app.post("/workspaces/add-github", response_class=HTMLResponse)
def workspace_add_github(request: Request, repo: str = Form(...),
                         branch: str = Form(""), name: str = Form("")):
    if not github_cli.REPO_RE.match(repo):
        raise HTTPException(status_code=400, detail="잘못된 리포 형식")
    try:
        ws = workspace.add_github(name.strip(), repo, branch.strip() or None)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _workspaces_response(request, ws["path"])


@app.post("/workspaces/remove", response_class=HTMLResponse)
def workspace_remove(request: Request, id: str = Form(...)):
    workspace.remove(id)
    return _workspaces_response(request)


# --- 폴더 탐색 팝업 (서버 파일시스템, 홈·iCloud 이하) ---

@app.get("/api/folders")
def api_folders(path: str = ""):
    root = config.resolve_path(config.BROWSE_ROOT)
    cur, notice, requested = config.resolve_browse_path(path)
    dirs = []
    try:
        dirs = config.list_browse_children(cur)
    except PermissionError:
        notice = (
            notice
            or "이 폴더에 접근할 수 없습니다. 시스템 설정 → 개인정보 보호 및 보안에서 "
            "Python(또는 터미널)에 iCloud Drive 접근을 허용했는지 확인해 주세요."
        )
    cur = config.canonical_path(cur)
    parent = config.browse_parent(cur)
    at_root = config.show_browse_shortcuts(cur)
    payload = {
        "path": str(cur),
        "parent": str(parent) if parent else None,
        "canUp": parent is not None,
        "home": str(root),
        "dirs": dirs,
    }
    if notice:
        payload["notice"] = notice
    if requested and requested != payload["path"]:
        payload["requested"] = requested
    if at_root:
        payload["shortcuts"] = config.browse_shortcuts()
    return payload


# --- GitHub 리포/브랜치 팝업 ---

@app.get("/api/github/status")
def api_github_status():
    return github_cli.status()


@app.get("/api/github/repos")
def api_github_repos():
    try:
        return {"repos": github_cli.list_repos()}
    except (RuntimeError, ValueError) as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/api/github/branches")
def api_github_branches(repo: str):
    try:
        return github_cli.list_branches(repo)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/partials/usage", response_class=HTMLResponse)
def partial_usage(request: Request):
    ctx = usage_state()
    # 최근 24시간 에이전트별 실행 횟수(활동 추세) — usage_log 기반
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat(
        timespec="seconds")
    ctx["recent"] = db.usage_counts(db.get_conn(), since)
    return templates.TemplateResponse(request, "partials/usage.html", ctx)


def _render_memory(request, q=""):
    ctx = {"q": q, "searching": bool(q)}
    if q:
        ctx["results"] = memory.search_notes(q)
    else:
        ctx.update(memory.browse_notes())
    return templates.TemplateResponse(request, "partials/memory.html", ctx)


@app.get("/partials/memory", response_class=HTMLResponse)
def partial_memory(request: Request, q: str = ""):
    return _render_memory(request, q)


def _managed_note_or_404(path):
    if not memory.is_managed(path):
        raise HTTPException(status_code=404, detail="managed 노트가 아닙니다")


@app.post("/notes/pin", response_class=HTMLResponse)
def note_pin(request: Request, path: str = Form(...)):
    _managed_note_or_404(path)
    memory.set_note_flags(path, pinned=not memory.note_flags(path)["pinned"])
    return _render_memory(request)


@app.post("/notes/archive", response_class=HTMLResponse)
def note_archive(request: Request, path: str = Form(...)):
    _managed_note_or_404(path)
    memory.set_note_flags(path, archived=not memory.note_flags(path)["archived"])
    return _render_memory(request)


@app.post("/notes/group", response_class=HTMLResponse)
def note_group(request: Request, path: str = Form(...), group: str = Form("")):
    _managed_note_or_404(path)
    # 수동 변경은 auto_group=False로 표시 → 자동 그룹핑이 덮어쓰지 않는다
    memory.set_note_flags(path, group=group.strip() or None, auto_group=False)
    return _render_memory(request)


@app.post("/notes/rename", response_class=HTMLResponse)
def note_rename(request: Request, path: str = Form(...), name: str = Form(...)):
    _managed_note_or_404(path)
    old = str(Path(path).resolve())
    try:
        new = memory.rename_note(path, name)
    except (FileExistsError, FileNotFoundError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    # 연결된 작업의 note_path도 갱신 (메모리↔작업큐 연동 유지)
    db.relink_note_path(db.get_conn(), old, str(new.resolve()))
    return _render_memory(request)


@app.post("/notes/delete", response_class=HTMLResponse)
def note_delete(request: Request, path: str = Form(...)):
    _managed_note_or_404(path)
    resolved = str(Path(path).resolve())
    memory.delete_note(path)
    # 노트에 연결된 작업도 함께 삭제 → 작업큐에서 사라진다
    db.delete_jobs_by_note(db.get_conn(), resolved)
    return _render_memory(request)


# --- 비전 보드 (프로젝트 오케스트레이션) ---

def _project_or_404(conn, project_id):
    project = db.get_project(conn, project_id)
    if project is None:
        raise HTTPException(status_code=404)
    return project


def _project_progress(conn, projects):
    """프로젝트 목록에 완료/전체 태스크 수를 붙인다."""
    rows = []
    for p in projects:
        tasks = db.list_tasks(conn, p["id"])
        rows.append({**dict(p), "total": len(tasks),
                     "done": sum(1 for t in tasks if t["status"] == "done")})
    return rows


@app.get("/board", response_class=HTMLResponse)
def board_page(request: Request):
    conn = db.get_conn()
    return templates.TemplateResponse(
        request, "board.html",
        {"projects": _project_progress(conn, db.list_projects(conn)),
         "provider_models": models.get_provider_models(),
         "agent_order": settings.enabled_providers(),
         "council_enabled": settings.council_available()})


_BOARD_TAB_KINDS = {"chat", "task", "workflow", "flow", "artifact", "diff", "preview"}
_BOARD_TAB_STATUSES = {"pending", "running", "done", "error"}


def _board_or_404(conn, board_id):
    if db.get_project(conn, board_id) is None:
        raise HTTPException(status_code=404, detail="보드를 찾을 수 없습니다")


def _tab_or_404(conn, board_id, tab_id):
    tab = db.get_board_tab(conn, tab_id)
    if tab is None or tab["board_id"] != board_id:
        raise HTTPException(status_code=404, detail="탭을 찾을 수 없습니다")
    return tab


@app.get("/api/boards/{board_id}/tabs")
def list_board_tabs(board_id: int):
    conn = db.get_conn()
    _board_or_404(conn, board_id)
    return {"tabs": [dict(tab) for tab in db.list_board_tabs(conn, board_id)]}


@app.post("/api/boards/{board_id}/tabs", status_code=201)
def create_board_tab(board_id: int, payload: BoardTabCreate):
    conn = db.get_conn()
    _board_or_404(conn, board_id)
    title = payload.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="탭 제목은 비울 수 없습니다")
    if payload.kind not in _BOARD_TAB_KINDS or payload.status not in _BOARD_TAB_STATUSES:
        raise HTTPException(status_code=400, detail="유효하지 않은 탭 종류 또는 상태입니다")
    tab_id = db.get_or_create_board_tab(conn, board_id, title, payload.kind,
                                        payload.ref_id, payload.status)
    return {"tab": dict(db.get_board_tab(conn, tab_id))}


@app.patch("/api/boards/{board_id}/tabs/{tab_id}")
def rename_board_tab(board_id: int, tab_id: int, payload: BoardTabRename):
    conn = db.get_conn()
    _board_or_404(conn, board_id)
    _tab_or_404(conn, board_id, tab_id)
    title = payload.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="탭 제목은 비울 수 없습니다")
    db.update_board_tab(conn, tab_id, title=title)
    return {"tab": dict(db.get_board_tab(conn, tab_id))}


@app.delete("/api/boards/{board_id}/tabs/{tab_id}")
def close_board_tab(board_id: int, tab_id: int):
    conn = db.get_conn()
    _board_or_404(conn, board_id)
    if not db.close_board_tab(conn, board_id, tab_id):
        raise HTTPException(status_code=404, detail="탭을 찾을 수 없습니다")
    return {"ok": True}


@app.put("/api/boards/{board_id}/tabs/order")
def reorder_board_tabs(board_id: int, payload: BoardTabOrder):
    conn = db.get_conn()
    _board_or_404(conn, board_id)
    try:
        db.reorder_board_tabs(conn, board_id, payload.tab_ids)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"tabs": [dict(tab) for tab in db.list_board_tabs(conn, board_id)]}


@app.post("/api/boards/{board_id}/tabs/{tab_id}/activate")
def activate_board_tab(board_id: int, tab_id: int):
    conn = db.get_conn()
    _board_or_404(conn, board_id)
    _tab_or_404(conn, board_id, tab_id)
    db.set_active_board_tab(conn, board_id, tab_id)
    return {"tab": dict(db.get_board_tab(conn, tab_id))}


@app.post("/projects")
async def create_project(
    request: Request,
    goal: str = Form(...),
    provider: str = Form("auto"),
    model: str = Form(""),
    workdir: str = Form(""),
    attach_memory: bool = Form(False),
    timeout_min: int | None = Form(None),
    files: list[UploadFile] = File(default=[]),
):
    conn = db.get_conn()
    if not workspace.valid_path(workdir):
        workdir = ""
    extra_context = ""
    if attach_memory:
        extra_context += memory.build_context(goal)
    uploads = await _save_uploads(files)
    if uploads:
        extra_context += "\n\n첨부 파일 (로컬 경로에서 읽을 것):\n" + "\n".join(
            f"- {p}" for p in uploads
        ) + "\n\n"
    project_id = orchestrator.start_project(
        conn, goal.strip(), workdir=workdir or None,
        enabled=settings.enabled_providers(), planner=provider, model=model,
        timeout_sec=timeout_min * 60 if timeout_min else None,
        extra_context=extra_context or None)
    # 홈 사이드바의 비전보드 채팅은 페이지 이동 대신 중앙에 탭을 연다 —
    # POST /jobs와 같은 Accept 분기(폼 제출은 그대로 303).
    if "application/json" in (request.headers.get("accept") or ""):
        return {"project_id": project_id}
    return RedirectResponse(f"/projects/{project_id}", status_code=303)


@app.get("/projects/{project_id}", response_class=HTMLResponse)
def project_page(request: Request, project_id: int):
    conn = db.get_conn()
    project = _project_or_404(conn, project_id)
    channel = db.get_or_create_project_channel(conn, project)
    tasks = db.list_tasks(conn, project_id)
    return templates.TemplateResponse(
        request, "project.html",
        {"project": project, "channel": dict(channel),
         "tasks": [dict(t) for t in tasks],
         "agent_order": settings.enabled_providers()})


@app.get("/partials/projects", response_class=HTMLResponse)
def partial_projects(request: Request):
    conn = db.get_conn()
    return templates.TemplateResponse(
        request, "partials/projects.html",
        {"projects": _project_progress(conn, db.list_projects(conn))})


def _board_partial(request, conn, project_id, orientation="lr"):
    """보드 조각 렌더 — 폴링과 모든 편집 액션이 같은 응답을 돌려준다."""
    project = _project_or_404(conn, project_id)
    tasks = db.list_tasks(conn, project_id)
    if orientation not in orchestrator.ORIENTATIONS:
        orientation = "lr"
    return templates.TemplateResponse(
        request, "partials/board.html",
        {"project": project, "tasks": tasks,
         # 일시정지 배너 문구/버튼 분기 — 수동 일시정지와 태스크 실패는 다르다
         "pause_reason": project["pause_reason"],
         "graph": orchestrator.layout_graph(tasks, orientation=orientation),
         # '완료'지만 결과가 오류 안내문인 태스크 — 캔버스에서도 눈에 띄어야
         # 사용자가 그 노드를 열어 조치할 수 있다
         "warn_ids": {t["id"] for t in tasks
                      if orchestrator.output_error_hint(t)},
         "editable": orchestrator.project_editable(project),
         "editable_task_statuses": orchestrator.EDITABLE_TASK_STATUSES,
         "task_types": orchestrator.TASK_TYPES,
         "agent_order": settings.enabled_providers()})


@app.api_route("/partials/board/{project_id}", methods=["GET", "HEAD"],
               response_class=HTMLResponse)
def partial_board(request: Request, project_id: int, o: str = "lr"):
    """o=lr(가로, 데스크톱) | tb(세로, 모바일) — 클라이언트가 뷰포트 폭으로 정한다."""
    return _board_partial(request, db.get_conn(), project_id, orientation=o)


@app.api_route("/partials/task/{task_id}", methods=["GET", "HEAD"], response_class=HTMLResponse)
def partial_task(request: Request, task_id: int):
    conn = db.get_conn()
    task = db.get_task(conn, task_id)
    if task is None:
        raise HTTPException(status_code=404)
    project = db.get_project(conn, task["project_id"])
    artifact_name = (Path(task["artifact_path"]).name
                     if task["artifact_path"] else None)
    siblings = [t for t in db.list_tasks(conn, task["project_id"])
                if t["seq"] != task["seq"]]
    agents = settings.enabled_providers()
    return templates.TemplateResponse(
        request, "partials/task_detail.html",
        {"task": task, "artifact_name": artifact_name, "project": project,
         "siblings": siblings, "deps": orchestrator.task_deps(task),
         "editable": orchestrator.task_editable(project, task),
         "task_types": orchestrator.TASK_TYPES,
         "agent_order": agents,
         # 오류 조치 패널 — 모델 교체용 목록과 '완료지만 오류' 감지 결과
         "recoverable": orchestrator.task_recoverable(project, task),
         "output_error": orchestrator.output_error_hint(task),
         "has_dependents": bool(
             orchestrator.dependent_seqs(siblings + [task], task["seq"])),
         "provider_models": {p: v for p, v in
                             models.get_provider_models().items() if p in agents}})


def _project_action(project_id, fn):
    conn = db.get_conn()
    _project_or_404(conn, project_id)
    try:
        fn(conn, project_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return RedirectResponse(f"/projects/{project_id}", status_code=303)


@app.post("/projects/{project_id}/approve")
def approve_project(project_id: int):
    return _project_action(project_id, orchestrator.approve)


@app.post("/projects/{project_id}/replan")
def replan_project(project_id: int):
    return _project_action(project_id, orchestrator.replan)


@app.post("/projects/{project_id}/pause")
def pause_project_endpoint(project_id: int):
    """실행 중 일시정지 — 진행 중 태스크는 끝까지 두고 새 디스패치만 멈춘다."""
    return _project_action(project_id, orchestrator.pause_project)


@app.post("/projects/{project_id}/resume")
def resume_project_endpoint(project_id: int):
    """일시정지 후 이어서 실행 — 완료된 태스크는 다시 돌지 않는다."""
    return _project_action(project_id, orchestrator.resume_project)


@app.post("/projects/{project_id}/retry-plan")
def retry_plan_project(project_id: int):
    return _project_action(project_id, orchestrator.retry_plan)


@app.post("/projects/{project_id}/cancel")
def cancel_project_endpoint(project_id: int):
    return _project_action(project_id, orchestrator.cancel_project)


@app.post("/projects/{project_id}/delete")
def delete_project_endpoint(project_id: int):
    conn = db.get_conn()
    _project_or_404(conn, project_id)
    orchestrator.cancel_project(conn, project_id)
    db.delete_project(conn, project_id)
    # 미디어 산출물 폴더도 함께 정리
    artifacts = config.ARTIFACTS_DIR / str(project_id)
    if artifacts.is_dir():
        import shutil
        shutil.rmtree(artifacts, ignore_errors=True)
    return RedirectResponse("/board", status_code=303)


def _remove_project_artifacts(project_id):
    artifacts = config.ARTIFACTS_DIR / str(project_id)
    if artifacts.is_dir():
        import shutil
        shutil.rmtree(artifacts, ignore_errors=True)


@app.post("/projects/cancelled/clear")
def clear_cancelled_projects_endpoint():
    """취소된 프로젝트를 모두 하드 삭제 — 실행 중/대기 중 프로젝트는 건드리지 않는다."""
    conn = db.get_conn()
    projects = db.list_projects_by_status(conn, "cancelled")
    for p in projects:
        db.delete_project(conn, p["id"])
        _remove_project_artifacts(p["id"])
    return {"deleted": len(projects)}


@app.post("/projects/{project_id}/delete-cancelled")
def delete_cancelled_project_endpoint(project_id: int):
    """취소된 프로젝트만 삭제를 허용 — 실행 중인 프로젝트를 실수로 지우지 않게 막는다."""
    conn = db.get_conn()
    project = _project_or_404(conn, project_id)
    if project["status"] != "cancelled":
        raise HTTPException(
            status_code=400,
            detail="취소된 프로젝트만 삭제할 수 있습니다")
    db.delete_project(conn, project_id)
    _remove_project_artifacts(project_id)
    return RedirectResponse("/board", status_code=303)


# --- 다이어그램 편집 (n8n식 캔버스) ---
# 편집 액션은 리다이렉트가 아니라 보드 조각을 그대로 돌려준다 — htmx가 #board를
# 제자리 교체하므로 캔버스의 팬/줌·선택 상태가 유지된다.

def _edit_action(request, project_id, fn, orientation="lr"):
    conn = db.get_conn()
    try:
        fn(conn)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _board_partial(request, conn, project_id, orientation=orientation)


def _task_project_id(conn, task_id):
    task = db.get_task(conn, task_id)
    if task is None:
        raise HTTPException(status_code=404)
    return task["project_id"]


@app.post("/tasks/{task_id}/edit", response_class=HTMLResponse)
def edit_task_endpoint(
    request: Request,
    task_id: int,
    title: str = Form(...),
    description: str = Form(...),
    task_type: str = Form("text"),
    agent: str = Form("auto"),
    model: str = Form(""),
    extra_instruction: str = Form(""),
    o: str = Form("lr"),
):
    conn = db.get_conn()
    pid = _task_project_id(conn, task_id)
    return _edit_action(
        request, pid,
        lambda c: orchestrator.update_task_fields(
            c, task_id, title=title, description=description,
            task_type=task_type, agent=agent, model=model,
            extra_instruction=extra_instruction,
            enabled=settings.enabled_providers()),
        orientation=o)


@app.post("/tasks/{task_id}/deps", response_class=HTMLResponse)
def task_deps_endpoint(request: Request, task_id: int,
                       deps: list[str] = Form([]), o: str = Form("lr")):
    """선행 목록 전체 교체 — 엣지 추가와 삭제가 같은 엔드포인트를 쓴다.
    체크박스 폼(반복 필드)과 캔버스 JS(FormData.append) 둘 다 같은 형태로 보낸다."""
    conn = db.get_conn()
    pid = _task_project_id(conn, task_id)
    try:
        parsed = [int(d) for d in deps if str(d).strip()]
    except ValueError:
        raise HTTPException(status_code=400, detail="deps는 정수 목록이어야 합니다")
    return _edit_action(request, pid,
                        lambda c: orchestrator.set_task_deps(c, task_id, parsed),
                        orientation=o)


@app.post("/tasks/{task_id}/move", response_class=HTMLResponse)
def move_task_endpoint(request: Request, task_id: int, x: float = Form(...),
                       y: float = Form(...), o: str = Form("lr")):
    conn = db.get_conn()
    pid = _task_project_id(conn, task_id)
    return _edit_action(request, pid,
                        lambda c: orchestrator.move_task(c, task_id, x, y),
                        orientation=o)


@app.post("/tasks/{task_id}/delete", response_class=HTMLResponse)
def delete_task_endpoint(request: Request, task_id: int, o: str = Form("lr")):
    conn = db.get_conn()
    pid = _task_project_id(conn, task_id)
    return _edit_action(request, pid,
                        lambda c: orchestrator.delete_task(c, task_id),
                        orientation=o)


@app.post("/projects/{project_id}/tasks", response_class=HTMLResponse)
def add_task_endpoint(
    request: Request,
    project_id: int,
    title: str = Form(...),
    description: str = Form(""),
    task_type: str = Form("text"),
    agent: str = Form("auto"),
    x: str = Form(""),
    y: str = Form(""),
    o: str = Form("lr"),
    message_id: int | None = Form(None),
):
    conn = db.get_conn()
    _project_or_404(conn, project_id)
    # 좌표는 캔버스 JS가 채운다. 비어 있으면(폼 직접 제출) 자동 배치에 맡긴다.
    try:
        pos = (float(x), float(y)) if x.strip() and y.strip() else None
    except ValueError:
        pos = None
    return _edit_action(
        request, project_id,
        lambda c: orchestrator.add_task(
            c, project_id, title, description=description, task_type=task_type,
            agent=agent, pos=pos, enabled=settings.enabled_providers(),
            source_message_id=message_id),
        orientation=o)


@app.post("/projects/{project_id}/relayout", response_class=HTMLResponse)
def relayout_project_endpoint(request: Request, project_id: int,
                              o: str = Form("lr")):
    conn = db.get_conn()
    _project_or_404(conn, project_id)
    return _edit_action(request, project_id,
                        lambda c: orchestrator.reset_layout(c, project_id),
                        orientation=o)


@app.post("/tasks/{task_id}/retry")
def retry_task_endpoint(
    task_id: int,
    agent: str = Form(""),
    model: str = Form(None),
    instruction: str = Form(None),
    cascade: bool = Form(False),
):
    """실패·오류 태스크 조치 — 모델 교체와 추가 지시를 함께 받아 다시 실행한다.

    폼 필드를 안 보내면(상단 '재시도' 버튼) 기존 설정 그대로 재실행한다.
    """
    conn = db.get_conn()
    task = db.get_task(conn, task_id)
    if task is None:
        raise HTTPException(status_code=404)
    try:
        orchestrator.retry_task(
            conn, task_id, agent=agent or None, model=model,
            instruction=instruction, cascade=cascade,
            enabled=settings.enabled_providers())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return RedirectResponse(f"/projects/{task['project_id']}", status_code=303)


# --- 사이드바 태스크 편집·채팅 API ---
# 다이어그램 편집(/tasks/{id}/edit 등)과 달리 이 JSON API는 사이드바에서 태스크
# 하나를 열어 편집/대화하는 용도라 편집 가능 범위가 더 넓다(완료된 태스크도
# 재편집 가능) — 유일한 제약은 "지금 실행 중인 프로젝트를 건드리지 않는다".

def _task_or_404(conn, task_id):
    task = db.get_task(conn, task_id)
    if task is None:
        raise HTTPException(status_code=404)
    return task


@app.get("/api/tasks/{task_id}")
def api_get_task(task_id: int):
    conn = db.get_conn()
    task = _task_or_404(conn, task_id)
    project = db.get_project(conn, task["project_id"])
    recent_job = None
    if task["job_id"]:
        job = db.get_job(conn, task["job_id"])
        if job is not None:
            recent_job = {
                "id": job["id"], "status": job["status"],
                "output": job["output"][-2000:] if job["output"] else "",
                "error": job["error"], "started_at": job["started_at"],
                "finished_at": job["finished_at"],
            }
    agents = settings.enabled_providers()
    siblings = [t for t in db.list_tasks(conn, task["project_id"])
                if t["seq"] != task["seq"]]
    return {
        "id": task["id"], "project_id": task["project_id"], "seq": task["seq"],
        "title": task["title"], "description": task["description"],
        "task_type": task["task_type"], "provider": task["provider"],
        "model": task["model"], "status": task["status"],
        "depends_on": orchestrator.task_deps(task),
        "output": task["output"], "artifact_path": task["artifact_path"],
        "error": task["error"], "extra_instruction": task["extra_instruction"],
        "editable": project is None or project["status"] != "running",
        "retryable": task["status"] in orchestrator.RETRYABLE_TASK_STATUSES,
        "project_status": project["status"] if project else None,
        "recent_job": recent_job,
        # 사이드바가 셀렉트/체크리스트를 그리는 데 필요한 선택지들 — 캔버스 탭
        # (task_detail.html)이 템플릿 컨텍스트로 받던 것과 같은 목록이다.
        "task_types": list(orchestrator.TASK_TYPES),
        "agents": agents,
        "provider_models": {p: v for p, v in models.get_provider_models().items()
                            if p in agents},
        "siblings": [{"seq": t["seq"], "title": t["title"]} for t in siblings],
    }


@app.get("/api/tasks/{task_id}/runs")
def api_list_task_runs(task_id: int):
    """이 태스크의 시도 이력 — 중단(interrupted) 시도의 부분 출력까지 보존된다."""
    conn = db.get_conn()
    _task_or_404(conn, task_id)
    return [{"id": r["id"], "attempt": r["attempt"], "status": r["status"],
             "agent": r["agent"], "error": r["error"],
             "output": (r["output"] or "")[-4000:],
             "created_at": r["created_at"], "finished_at": r["finished_at"]}
            for r in db.list_task_runs(conn, task_id)]


@app.patch("/api/tasks/{task_id}")
def api_update_task(task_id: int, payload: TaskUpdate):
    conn = db.get_conn()
    task = _task_or_404(conn, task_id)
    project = db.get_project(conn, task["project_id"])
    if project is not None and project["status"] == "running":
        raise HTTPException(
            status_code=409,
            detail="프로젝트 실행 중에는 태스크를 편집할 수 없습니다. 일시정지 후 편집하세요")

    fields = {}
    if payload.title is not None:
        title = payload.title.strip()
        if not title:
            raise HTTPException(status_code=400, detail="제목은 비울 수 없습니다")
        fields["title"] = title
    if payload.description is not None:
        description = payload.description.strip()
        if not description:
            raise HTTPException(status_code=400, detail="설명은 비울 수 없습니다")
        fields["description"] = description
    if payload.task_type is not None:
        if payload.task_type not in orchestrator.TASK_TYPES:
            raise HTTPException(
                status_code=400, detail=f"알 수 없는 type {payload.task_type!r}")
        fields["task_type"] = payload.task_type

    if payload.agent is not None:
        task_type = fields.get("task_type", task["task_type"])
        description = fields.get("description", task["description"])
        try:
            provider = orchestrator.resolve_provider(
                task_type, payload.agent, description,
                usage_state=usage_state()["usage"],
                enabled=settings.enabled_providers())
        except orchestrator.PlanError as e:
            raise HTTPException(status_code=400, detail=str(e))
        fields["provider"] = provider
        if payload.model:
            if not models.is_valid_model(provider, payload.model):
                raise HTTPException(
                    status_code=400, detail=f"알 수 없는 model {payload.model!r}")
            fields["model"] = payload.model
        else:
            fields["model"] = None
    elif payload.model is not None:
        if payload.model:
            if not models.is_valid_model(task["provider"], payload.model):
                raise HTTPException(
                    status_code=400, detail=f"알 수 없는 model {payload.model!r}")
            fields["model"] = payload.model
        else:
            fields["model"] = None

    if payload.extra_instruction is not None:
        fields["extra_instruction"] = payload.extra_instruction.strip() or None

    if not fields and not payload.reset_status and payload.depends_on is None:
        raise HTTPException(status_code=400, detail="변경할 내용이 없습니다")
    if payload.reset_status:
        fields["status"] = "pending"

    if fields:
        db.update_task(conn, task_id, **fields)
    if payload.depends_on is not None:
        # 의존성은 캔버스 편집과 같은 검증(자기참조·순환·미존재 seq)을 그대로 탄다.
        try:
            orchestrator.set_task_deps(conn, task_id, payload.depends_on)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
    return dict(db.get_task(conn, task_id))


@app.post("/api/tasks/{task_id}/retry")
def api_retry_task(task_id: int, payload: TaskRetry):
    """사이드바용 JSON 재시도 — 폼 라우트(/tasks/{id}/retry)와 달리 리다이렉트하지
    않아 사이드바 안에서 그대로 갱신할 수 있다."""
    conn = db.get_conn()
    _task_or_404(conn, task_id)
    try:
        orchestrator.retry_task(
            conn, task_id, agent=payload.agent or None, model=payload.model,
            instruction=payload.instruction, cascade=payload.cascade,
            enabled=settings.enabled_providers())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return dict(db.get_task(conn, task_id))


@app.get("/api/tasks/{task_id}/messages")
def api_list_task_messages(task_id: int):
    conn = db.get_conn()
    _task_or_404(conn, task_id)
    return [dict(r) for r in db.list_task_messages(conn, task_id)]


async def _generate_task_reply(conn, task, history, message):
    """태스크 채팅 메시지 하나를 에이전트에게 넘겨 답변/설명 수정 제안을 얻는다.

    채널 채팅과 달리 잡을 큐에 남겨 두지 않고 그 자리에서 실행한다 — 응답에
    수정 제안을 바로 담아 돌려줘야 사이드바가 '적용' 버튼을 즉시 띄울 수 있다.
    """
    enabled = settings.enabled_providers()
    provider_name = task["provider"] if task["provider"] in PROVIDERS else None
    model = task["model"] if provider_name else None
    if provider_name not in enabled:
        provider_name = enabled[0] if enabled else "claude"
        model = None
    prompt = orchestrator.build_task_chat_prompt(task, history, message)
    job_id = db.create_job(conn, prompt, provider_name, model=model)
    job = db.get_job(conn, job_id)
    await worker.run_job(conn, job, save=False)
    job = db.get_job(conn, job_id)
    if job["status"] != "done":
        return job["error"] or "에이전트 호출에 실패했습니다", None
    return orchestrator.parse_task_chat_reply(job["output"])


@app.post("/api/tasks/{task_id}/messages", status_code=201)
async def api_create_task_message(task_id: int, payload: TaskMessageCreate):
    conn = db.get_conn()
    task = _task_or_404(conn, task_id)
    content = (payload.content or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="내용을 입력하세요")

    db.create_task_message(conn, task_id, role="user", content=content)
    history = db.list_task_messages(conn, task_id)
    reply, suggested_description = await _generate_task_reply(
        conn, task, history, content)
    assistant_id = db.create_task_message(
        conn, task_id, role="assistant", content=reply,
        suggested_description=suggested_description)

    return {"message_id": assistant_id, "reply": reply,
            "suggested_description": suggested_description}


@app.get("/artifacts/{project_id}/{filename}")
def serve_artifact(project_id: int, filename: str):
    """미디어 산출물 서빙 — ARTIFACTS_DIR 밖 경로 접근을 차단한다."""
    from fastapi.responses import FileResponse
    base = config.ARTIFACTS_DIR.resolve()
    target = (base / str(project_id) / filename).resolve()
    if not target.is_relative_to(base) or not target.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(target)
