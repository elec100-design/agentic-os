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

from app import (
    codexbar, config, council, db, github_cli, health, i18n, memory, models,
    settings, setup, stream_hub, workspace, worker,
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
    db.create_job(conn, prompt, provider,
                  timeout_sec=timeout_min * 60 if timeout_min else None,
                  session_id=session_id.strip() or None,
                  model=model or None,
                  workdir=workdir or None,
                  note_path=str(Path(origin_note).resolve()) if origin_note else None,
                  route_reason=route_reason)
    return RedirectResponse("/", status_code=303)


@app.get("/api/recommend")
def api_recommend(prompt: str = ""):
    """자동 모드 코칭: 지금 이 프롬프트를 자동으로 보내면 어느 에이전트로
    가는지와 그 이유를 JSON으로 돌려준다 (컴포저에서 실시간 표시)."""
    provider, reason = route_auto(prompt or " ", usage_state=usage_state()["usage"],
                                  enabled=settings.enabled_providers())
    return {"provider": provider, "reason": reason}


@app.get("/note", response_class=HTMLResponse)
def note_view(request: Request, path: str):
    note = memory.read_note(path)
    if note is None:
        raise HTTPException(status_code=404)
    can_resume = bool(
        note["session_id"] and note["provider"] in PROVIDERS
        and note["provider"] != "hermes"
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


@app.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_detail(request: Request, job_id: int):
    conn = db.get_conn()
    job = db.get_job(conn, job_id)
    if job is None:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(request, "job.html", {"job": job})


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
