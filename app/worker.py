import asyncio
import os
from datetime import datetime, timezone

from app import config, db, memory, workspace
from app.providers import CONTINUE_PROMPT, PROVIDERS

# 실행 중인 작업 취소를 위해 main.py가 참조하는 공유 상태
current = {"job_id": None, "proc": None}


def _clean_env():
    env = dict(os.environ)
    for key in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY",
                "XAI_API_KEY", "GROK_API_KEY"):
        env.pop(key, None)
    return env


async def _pump(stream, sink):
    while True:
        line = await stream.readline()
        if not line:
            return
        sink(line.decode("utf-8", errors="replace"))


async def run_job(conn, job, providers=None, save=True):
    providers = providers or PROVIDERS
    provider = providers[job["provider"]]
    timeout = job["timeout_sec"] or config.JOB_TIMEOUT_SEC
    # resume_at이 있으면 사용 제한 후 재개 → 이어서 완료하라는 고정 프롬프트.
    # 없는데 session_id가 있으면 사용자가 만든 세션 이어가기 → 본인 프롬프트.
    send_prompt = job["prompt"]
    if job["session_id"] and job["resume_at"]:
        send_prompt = CONTINUE_PROMPT
    cmd = provider.build_command(send_prompt, session_id=job["session_id"],
                                 model=job["model"])
    start = datetime.now(timezone.utc)

    workdir = job["workdir"] or None
    if workdir:
        for ws in workspace.list_workspaces():
            if config.paths_equivalent(ws["path"], workdir):
                workdir = ws["path"]
                break
        if not os.path.isdir(workdir):
            workdir = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_clean_env(),
            cwd=workdir,
        )
    except (FileNotFoundError, OSError) as e:
        db.update_job(conn, job["id"], status="failed", error=str(e),
                      finished_at=db.now_iso())
        db.log_usage(conn, provider.name, 0, "failed", job["id"])
        return

    current["job_id"], current["proc"] = job["id"], proc
    stdout_parts, stderr_parts = [], []

    def on_stdout(text):
        stdout_parts.append(text)
        db.append_output(conn, job["id"], text)

    try:
        await asyncio.wait_for(
            asyncio.gather(
                _pump(proc.stdout, on_stdout),
                _pump(proc.stderr, stderr_parts.append),
                proc.wait(),
            ),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        db.update_job(conn, job["id"], status="failed", error="timeout",
                      finished_at=db.now_iso())
        db.log_usage(conn, provider.name, timeout, "failed", job["id"])
        return
    finally:
        current["job_id"], current["proc"] = None, None

    stdout = "".join(stdout_parts)
    stderr = "".join(stderr_parts)
    duration = (datetime.now(timezone.utc) - start).total_seconds()

    # 사용자가 취소한 작업은 결과를 덮어쓰지 않는다
    fresh = db.get_job(conn, job["id"])
    if fresh["status"] == "failed" and fresh["error"] == "cancelled":
        return

    result = provider.parse_output(stdout, stderr, proc.returncode)
    resume_at = provider.detect_rate_limit(stdout + "\n" + stderr, proc.returncode)

    if resume_at is not None:
        db.update_job(
            conn, job["id"], status="rate_limited",
            resume_at=resume_at.isoformat(timespec="seconds"),
            session_id=result.session_id or job["session_id"],
        )
        db.log_usage(conn, provider.name, duration, "rate_limited", job["id"])
        return

    if proc.returncode != 0:
        db.update_job(conn, job["id"], status="failed",
                      error=(stderr or f"exit {proc.returncode}")[-2000:],
                      finished_at=db.now_iso())
        db.log_usage(conn, provider.name, duration, "failed", job["id"])
        return

    db.update_job(
        conn, job["id"], status="done", output=result.text,
        session_id=result.session_id or job["session_id"],
        finished_at=db.now_iso(),
    )
    db.log_usage(conn, provider.name, duration, "ok", job["id"])

    if save:
        try:
            note_path = memory.save_note(
                job["prompt"], provider.name, result.text,
                session_id=result.session_id or job["session_id"],
                workdir=job["workdir"])
            # 노트↔작업 연동을 위해 생성된 노트 경로를 작업에 기록
            db.update_job(conn, job["id"], note_path=str(note_path.resolve()))
        except OSError as e:
            db.update_job(conn, job["id"], error=f"memory_save_failed: {e}")


async def worker_loop(stop_event=None, providers=None, save=True, poll_sec=None):
    conn = db.get_conn()
    db.recover_running(conn)
    while not (stop_event and stop_event.is_set()):
        job = db.claim_next_job(conn)
        if job is None:
            await asyncio.sleep(poll_sec or config.WORKER_POLL_SEC)
            continue
        if job["attempts"] > config.MAX_ATTEMPTS:
            db.update_job(conn, job["id"], status="failed",
                          error="max attempts exceeded", finished_at=db.now_iso())
            continue
        try:
            await run_job(conn, job, providers=providers, save=save)
        except Exception as e:  # 큐는 어떤 작업이 터져도 살아있어야 한다
            db.update_job(conn, job["id"], status="failed",
                          error=f"worker error: {e!r}", finished_at=db.now_iso())
