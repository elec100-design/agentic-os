import asyncio
import os
from datetime import datetime, timezone

from app import config, db, memory, stream_hub, workspace
from app.providers import CONTINUE_PROMPT, COUNCIL, PROVIDERS

# 실행 중인 단일 CLI 잡의 프로세스 (job_id -> proc). 병렬 실행되므로 잡별로
# 추적해야 취소 시 정확한 프로세스를 종료할 수 있다. main.py가 참조한다.
running_procs: dict[int, object] = {}


def terminate_job_procs(job_id):
    """잡의 실행 중 프로세스를 모두 종료한다 (단일 CLI 잡 + 협의 잡의 병렬 CLI들)."""
    proc = running_procs.get(job_id)
    if proc:
        try:
            proc.terminate()
        except ProcessLookupError:
            pass
    from app import council
    for proc in list(council.council_procs.get(job_id, [])):
        try:
            proc.terminate()
        except ProcessLookupError:
            pass


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
    if job["provider"] == "council":
        from app import council
        await council.run_council(conn, job, providers=providers, save=save)
        return
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

    running_procs[job["id"]] = proc
    stdout_parts, stderr_parts = [], []

    def on_stdout(text):
        stdout_parts.append(text)
        db.append_output(conn, job["id"], text)
        # 구독 중인 SSE 스트림을 즉시 깨운다(프로세스 내 fast-path). DB 기록
        # '뒤에' 신호하므로, 깨어난 구독자는 방금 쓴 내용을 반드시 본다.
        stream_hub.publish(job["id"])

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
        running_procs.pop(job["id"], None)

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
            new_session = result.session_id or job["session_id"]
            # 세션 이어가기 잡은 note_path에 원본 스레드 노트가 미리 채워져
            # 있다 → 새 노트 대신 그 노트에 이어 쓴다 (실행 중 rename 대비 재조회)
            target = db.get_job(conn, job["id"])["note_path"]
            note_path = None
            if target and memory.is_managed(target):
                try:
                    note_path = memory.append_note(
                        target, job["prompt"], provider.name, result.text,
                        session_id=new_session, model=job["model"])
                except FileNotFoundError:
                    note_path = None  # 원본이 삭제됨 → 새 노트로 폴백
            if note_path is None:
                note_path = memory.save_note(
                    job["prompt"], provider.name, result.text,
                    session_id=new_session, workdir=job["workdir"],
                    model=job["model"])
            # 노트↔작업 연동을 위해 생성된 노트 경로를 작업에 기록
            db.update_job(conn, job["id"], note_path=str(note_path.resolve()))
        except OSError as e:
            db.update_job(conn, job["id"], error=f"memory_save_failed: {e}")


async def _run_tracked(conn, job, providers, save, release):
    """run_job 1건을 감싸 예외를 흡수하고, 끝나면 provider 슬롯을 반납한다.

    큐는 어떤 작업이 터져도 살아있어야 하므로 run_job의 예외를 여기서 잡는다.
    finally에서 SSE 구독자에게 종료를 알리고(run_job의 모든 조기 반환 경로를
    한 곳에서 커버) provider를 busy 집합에서 해제한다.
    """
    try:
        await run_job(conn, job, providers=providers, save=save)
    except Exception as e:
        db.update_job(conn, job["id"], status="failed",
                      error=f"worker error: {e!r}", finished_at=db.now_iso())
    finally:
        stream_hub.publish(job["id"])
        release(job["id"], job["provider"])


async def worker_loop(stop_event=None, providers=None, save=True, poll_sec=None):
    """병렬 디스패처. 서로 다른 provider의 잡을 동시에 실행하되(전역 상한
    MAX_CONCURRENT_JOBS), 같은 provider는 직렬로 돌려 사용량 레이스를 막는다.
    협의(council) 잡은 배타적 — 완전 idle일 때만 시작하고, 도는 동안 다른 잡을
    새로 뽑지 않는다(협의는 내부적으로 여러 CLI를 동시에 쓰므로).

    이 코루틴이 유일한 클레임 주체다. claim_next_job은 SELECT~UPDATE 사이에
    await가 없어 원자적이므로, 실행 태스크들과 동시에 돌아도 이중 클레임이 없다.
    """
    conn = db.get_conn()
    db.recover_running(conn)
    running: dict[int, asyncio.Task] = {}  # job_id -> 실행 태스크
    busy: set[str] = set()                 # 실행 중인 provider 이름
    poll = poll_sec or config.WORKER_POLL_SEC

    def release(job_id, provider):
        running.pop(job_id, None)
        busy.discard(provider)

    while not (stop_event and stop_event.is_set()):
        dispatched = False
        # 협의 잡이 도는 동안엔 아무것도 새로 시작하지 않는다(배타).
        council_busy = COUNCIL in busy
        if not council_busy and len(running) < config.MAX_CONCURRENT_JOBS:
            # 이미 도는 provider는 제외(직렬화). 뭔가 실행 중이면 council도 제외
            # → 협의 잡은 완전 idle일 때만 뽑히고, 다른 provider가 앞질러 실행된다.
            exclude = set(busy)
            if running:
                exclude.add(COUNCIL)
            job = db.claim_next_job(conn, exclude_providers=exclude)
            if job is not None:
                if job["attempts"] > config.MAX_ATTEMPTS:
                    db.update_job(conn, job["id"], status="failed",
                                  error="max attempts exceeded",
                                  finished_at=db.now_iso())
                    stream_hub.publish(job["id"])
                else:
                    busy.add(job["provider"])
                    running[job["id"]] = asyncio.create_task(
                        _run_tracked(conn, job, providers, save, release))
                dispatched = True
        if dispatched:
            continue  # 여유가 남아있으면 곧바로 다음 잡도 채운다
        if running:
            # 폴링 대신 '하나라도 끝나면' 깨어난다(슬롯이 비면 즉시 다음 잡 투입).
            await asyncio.wait(set(running.values()), timeout=poll,
                               return_when=asyncio.FIRST_COMPLETED)
        else:
            await asyncio.sleep(poll)

    # 종료 신호를 받으면 실행 중인 잡이 끝날 때까지 기다린다(고아 태스크 방지).
    if running:
        await asyncio.gather(*running.values(), return_exceptions=True)
