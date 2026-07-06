import asyncio
import json
import os
import re
from contextlib import asynccontextmanager
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

from app import codexbar, config, db, memory, workspace, worker
from app.providers import PROVIDERS, route_auto

BASE = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE / "templates"))

# same-origin POST는 포트 무관 허용되지만, tailscale serve처럼 프록시가 Host를
# 다시 쓰는 경우를 대비해 tailnet 이름을 명시 허용한다.
# 추가 origin은 AOS_EXTRA_ORIGINS(콤마 구분) 환경변수로도 넣을 수 있다.
ALLOWED_ORIGINS = {"localhost:8899", "127.0.0.1:8899",
                   "macmini.tail22aa0a.ts.net"}
ALLOWED_ORIGINS |= {
    o.strip() for o in os.environ.get("AOS_EXTRA_ORIGINS", "").split(",") if o.strip()
}


@asynccontextmanager
async def lifespan(app):
    stop = asyncio.Event()
    tasks = []
    if not os.environ.get("AOS_DISABLE_WORKER"):
        tasks.append(asyncio.create_task(worker.worker_loop(stop)))
        tasks.append(asyncio.create_task(codexbar.refresh_loop(stop)))
    yield
    stop.set()
    for task in tasks:
        task.cancel()


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")


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
    if secs <= 0:
        return "곧"
    hours, mins = int(secs // 3600), int(secs % 3600 // 60)
    if hours:
        return f"{hours}시간 {mins}분"
    return f"{max(mins, 1)}분"


def usage_state(now=None):
    """CodexBar 캐시를 읽어 각 프로바이더의 표시/라우팅용 상태를 만든다.
    hermes는 로컬·무제한. 캐시가 없거나 프로바이더에 에러가 있으면 정보없음 상태."""
    now = now or datetime.now(timezone.utc)
    cache = codexbar.read_cache()
    cached = cache.get("providers", {})
    providers = ["claude", "gemini", "grok", "hermes"]
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
    if not iso:
        return "갱신 대기"
    dt = None
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return "갱신 대기"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    secs = int((now - dt).total_seconds())
    if secs < 60:
        return "방금"
    if secs < 3600:
        return f"{secs // 60}분 전"
    return f"{secs // 3600}시간 전"


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(
        request, "index.html",
        {"provider_models": config.PROVIDER_MODELS},
    )


async def _save_uploads(files):
    saved = []
    for f in files or []:
        if not f.filename:
            continue
        config.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^\w.\-가-힣]", "_", Path(f.filename).name)
        dest = config.UPLOAD_DIR / f"{datetime.now():%Y%m%d-%H%M%S%f}-{safe}"
        dest.write_bytes(await f.read())
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
    context_note: str = Form(""),
    files: list[UploadFile] = File(default=[]),
):
    conn = db.get_conn()
    if provider == "auto":
        provider, _ = route_auto(prompt, usage_state=usage_state()["usage"])
    if provider not in PROVIDERS:
        raise HTTPException(status_code=400, detail="unknown provider")
    if not _valid_model(provider, model):
        model = ""
    # 등록된 작업 위치만 cwd로 허용 (임의 경로 실행 방지)
    if not workspace.valid_path(workdir):
        workdir = ""
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
                  workdir=workdir or None)
    return RedirectResponse("/", status_code=303)


def _valid_model(provider, model):
    """폼으로 넘어온 model이 해당 provider의 허용 목록에 있는지 검증."""
    if not model:
        return True
    return any(m.get("model") == model
               for m in config.PROVIDER_MODELS.get(provider, []))


@app.get("/api/recommend")
def api_recommend(prompt: str = ""):
    """자동 모드 코칭: 지금 이 프롬프트를 자동으로 보내면 어느 에이전트로
    가는지와 그 이유를 JSON으로 돌려준다 (컴포저에서 실시간 표시)."""
    provider, reason = route_auto(prompt or " ", usage_state=usage_state()["usage"])
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
    return templates.TemplateResponse(
        request, "note.html", {"note": note, "can_resume": can_resume}
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
    if worker.current["job_id"] == job_id and worker.current["proc"]:
        worker.current["proc"].terminate()
    return RedirectResponse("/", status_code=303)


@app.get("/jobs/{job_id}/stream")
async def stream_job(job_id: int):
    if db.get_job(db.get_conn(), job_id) is None:
        raise HTTPException(status_code=404)

    async def gen():
        sent = 0
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
            await asyncio.sleep(1)

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/jobs/{job_id}/delete", response_class=HTMLResponse)
def delete_job_endpoint(request: Request, job_id: int):
    conn = db.get_conn()
    job = db.get_job(conn, job_id)
    if job is None:
        raise HTTPException(status_code=404)
    # 실행 중이면 먼저 중단
    if worker.current["job_id"] == job_id and worker.current["proc"]:
        worker.current["proc"].terminate()
    # 연결된 노트도 함께 삭제 (작업큐↔메모리 연동)
    if job["note_path"] and memory.is_managed(job["note_path"]):
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


@app.post("/workspaces/add", response_class=HTMLResponse)
def workspace_add(request: Request, value: str = Form(...), name: str = Form("")):
    try:
        workspace.add(name.strip(), value.strip())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return templates.TemplateResponse(
        request, "partials/workspaces.html",
        {"workspaces": workspace.list_workspaces()},
    )


@app.post("/workspaces/remove", response_class=HTMLResponse)
def workspace_remove(request: Request, id: str = Form(...)):
    workspace.remove(id)
    return templates.TemplateResponse(
        request, "partials/workspaces.html",
        {"workspaces": workspace.list_workspaces()},
    )


@app.get("/partials/usage", response_class=HTMLResponse)
def partial_usage(request: Request):
    return templates.TemplateResponse(
        request, "partials/usage.html", usage_state()
    )


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
    memory.set_note_flags(path, group=group.strip() or None)
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
