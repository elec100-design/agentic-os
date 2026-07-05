import asyncio
import json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import config, db, memory, worker
from app.providers import route_auto

BASE = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE / "templates"))


@asynccontextmanager
async def lifespan(app):
    stop = asyncio.Event()
    task = None
    if not os.environ.get("AOS_DISABLE_WORKER"):
        task = asyncio.create_task(worker.worker_loop(stop))
    yield
    stop.set()
    if task:
        task.cancel()


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")


def _usage_context(conn):
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat(
        timespec="seconds"
    )
    return {
        "counts": db.usage_counts(conn, since),
        "limits": db.limit_status(conn),
        "providers": ["claude", "gemini", "grok", "hermes"],
    }


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {})


@app.post("/jobs")
def create_job(
    prompt: str = Form(...),
    provider: str = Form("auto"),
    attach_memory: bool = Form(False),
    timeout_min: int | None = Form(None),
):
    if provider == "auto":
        provider = route_auto(prompt)
    if attach_memory:
        prompt = memory.build_context(prompt) + prompt
    conn = db.get_conn()
    db.create_job(conn, prompt, provider,
                  timeout_sec=timeout_min * 60 if timeout_min else None)
    return RedirectResponse("/", status_code=303)


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
            if job["status"] in ("done", "failed", "rate_limited"):
                yield f"event: status\ndata: {job['status']}\n\n"
                break
            await asyncio.sleep(1)

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/partials/jobs", response_class=HTMLResponse)
def partial_jobs(request: Request):
    conn = db.get_conn()
    return templates.TemplateResponse(
        request, "partials/jobs.html", {"jobs": db.list_jobs(conn)}
    )


@app.get("/partials/usage", response_class=HTMLResponse)
def partial_usage(request: Request):
    conn = db.get_conn()
    return templates.TemplateResponse(
        request, "partials/usage.html", _usage_context(conn)
    )


@app.get("/partials/memory", response_class=HTMLResponse)
def partial_memory(request: Request, q: str = ""):
    notes = memory.search_notes(q) if q else memory.recent_notes()
    return templates.TemplateResponse(
        request, "partials/memory.html", {"notes": notes, "q": q}
    )
