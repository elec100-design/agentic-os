import asyncio
import json
import os
from datetime import datetime, timezone

from app import config, db, gitcheckpoint, instructions, memory, stream_hub, workspace
from app.providers import CONTINUE_PROMPT, COUNCIL, PROVIDERS

# 실행 중인 단일 CLI 잡의 프로세스 (job_id -> proc). 병렬 실행되므로 잡별로
# 추적해야 취소 시 정확한 프로세스를 종료할 수 있다. main.py가 참조한다.
running_procs: dict[int, object] = {}

# asyncio StreamReader의 기본 readline() 한도(64KiB)는 codex 등 일부 CLI가
# --json으로 뱉는 긴 한 줄짜리 이벤트(예: 스킬 설명 목록)를 넘겨 버려
# "Separator is not found, and chunk exceed the limit" ValueError를 낸다.
STREAM_LIMIT = 16 * 1024 * 1024


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


def _sanitize_google_adc(env):
    """잘못된 GOOGLE_APPLICATION_CREDENTIALS 를 제거해 기본 ADC 를 쓰게 한다.

    일부 툴(gws 등)이 OAuth client_secret.json 을 GAC 로 내보내면
    Vertex/ADC 경로가 깨진다. 유효한 ADC type 이 아니면 환경변수만 지운다.
    """
    import json
    from pathlib import Path

    gac = (env.get("GOOGLE_APPLICATION_CREDENTIALS") or "").strip()
    if not gac:
        return
    path = Path(gac)
    valid_types = {
        "authorized_user",
        "service_account",
        "external_account",
        "external_account_authorized_user",
        "impersonated_service_account",
        "gdch_service_account",
    }
    try:
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("type") in valid_types:
                return
    except (OSError, json.JSONDecodeError):
        pass
    env.pop("GOOGLE_APPLICATION_CREDENTIALS", None)


def _merge_gemini_dotenv(env):
    """~/.gemini/.env 의 Vertex/프로젝트 변수를 워커 env 에 보강.

    이미 설정된 키는 덮어쓰지 않는다. CLI 가 자체 로드하기도 하지만
    launchd 등 비대화형 환경에서 누락을 막기 위함.
    """
    from pathlib import Path

    path = Path.home() / ".gemini" / ".env"
    if not path.is_file():
        return
    keep = {
        "GOOGLE_CLOUD_PROJECT",
        "GOOGLE_CLOUD_PROJECT_ID",
        "GOOGLE_CLOUD_LOCATION",
        "GOOGLE_GENAI_USE_VERTEXAI",
        "GEMINI_CLI_TRUST_WORKSPACE",
    }
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key in keep and key not in env and val:
                env[key] = val
    except OSError:
        pass


def _clean_env():
    # 구독 CLI 로그인 세션만 쓰도록 API 키 환경변수를 제거한다(추가 과금 방지).
    # Vertex AI 는 ADC(+GOOGLE_CLOUD_PROJECT)를 쓰므로 키를 제거해도 동작한다.
    env = dict(os.environ)
    for key in (
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_GENAI_API_KEY",
        "XAI_API_KEY", "GROK_API_KEY",
        "OPENAI_API_KEY", "CODEX_API_KEY",
    ):
        env.pop(key, None)
    _sanitize_google_adc(env)
    _merge_gemini_dotenv(env)
    return env


async def _pump(stream, sink):
    while True:
        line = await stream.readline()
        if not line:
            return
        sink(line.decode("utf-8", errors="replace"))


async def run_job(conn, job, providers=None, save=True):
    providers = providers or PROVIDERS
    if job["message_id"]:
        db.update_message(conn, job["message_id"], status="running",
                          started_at=db.now_iso())
    if job["provider"] == "council":
        from app import council
        await council.run_council(conn, job, providers=providers, save=save)
        return
    if job["provider"] == "media":
        from app import media
        await media.run_media_job(conn, job)
        return
    provider = providers[job["provider"]]
    timeout = job["timeout_sec"] or config.JOB_TIMEOUT_SEC

    # 지침 주입은 workdir 을 읽어야 하므로 프롬프트 조립보다 먼저 확정한다.
    workdir = job["workdir"] or None
    if workdir:
        for ws in workspace.list_workspaces():
            if config.paths_equivalent(ws["path"], workdir):
                workdir = ws["path"]
                break
        if not os.path.isdir(workdir):
            workdir = None

    # resume_at이 있으면 사용 제한 후 재개 → 이어서 완료하라는 고정 프롬프트.
    # 없는데 session_id가 있으면 사용자가 만든 세션 이어가기 → 본인 프롬프트.
    # 단, CLI가 세션 재개를 지원하지 않으면(supports_resume=False) 이 호출은
    # 이전 맥락이 전혀 없는 새 세션이 되므로, CONTINUE_PROMPT(맥락 의존 문구)
    # 대신 원래 프롬프트를 다시 보낸다 — 그렇지 않으면 에이전트가 맥락 없이
    # 임의로 행동(환각)한다.
    # 서버가 죽어 재큐잉된 잡(attempts > 1)도 마찬가지다 — 세션이 남아 있으면
    # 처음부터 다시 시키지 않고 하던 작업을 이어서 완료하게 한다.
    send_prompt = job["prompt"]
    if (job["session_id"] and getattr(provider, "supports_resume", True)
            and (job["resume_at"] or job["attempts"] > 1)):
        send_prompt = CONTINUE_PROMPT

    # 워크스페이스 프로젝트 지침(CLAUDE.md/AGENTS.md/.agentic-os.md) 주입 —
    # provider 가 스스로 읽는 파일은 instructions.build_context 가 알아서 뺀다
    # (app/instructions.py 참고). 라우팅이 자동이라 어느 에이전트가 걸리든
    # 지침이 똑같이 적용되게 하는 것이 목적이다.
    instr_prefix, instr_applied = instructions.build_context(workdir, provider)
    if instr_prefix:
        send_prompt = instr_prefix + send_prompt
    # 결과와 무관하게(도중에 죽어도) 무엇이 적용됐는지 남긴다 — 실행 전에
    # 이미 확정되는 값이라 여기서 바로 기록한다.
    db.update_job(conn, job["id"],
                  instructions_applied=json.dumps(instr_applied) if instr_applied else None)

    cmd = provider.build_command(send_prompt, session_id=job["session_id"],
                                 model=job["model"])
    start = datetime.now(timezone.utc)

    # 승인 없이 워크스페이스에 직접 쓰는 잡이 많다(codex는 샌드박스까지 끔) —
    # 무엇이 바뀌었는지 실제 git diff 로 보여주고 되돌릴 수 있게, 시작 시점을
    # 찍어 둔다. 시작할 때 이미 지저분한 워크스페이스는 "이 실행이 바꾼 것"과
    # "원래 사용자가 고치던 것"을 구분할 수 없어 대상에서 뺀다(capture 참고).
    checkpoint = await gitcheckpoint.capture(workdir)

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_clean_env(),
            cwd=workdir,
            limit=STREAM_LIMIT,
        )
    except (FileNotFoundError, OSError) as e:
        db.update_job(conn, job["id"], status="failed", error=str(e),
                      finished_at=db.now_iso())
        db.log_usage(conn, provider.name, 0, "failed", job["id"])
        return

    running_procs[job["id"]] = proc
    stdout_parts, stderr_parts = [], []
    step_id = None  # 메시지에 연결된 잡이면, 출력 청크를 누적하는 실행 스텝 1개
    # 이벤트 스트림을 내보내는 CLI(현재 claude)는 stdout 이 JSONL 이라 그대로
    # 보여 주면 읽을 수 없다 → 원문은 파싱용으로만 모으고, 화면·DB 에는
    # 사람이 읽는 줄만 남기며 도구 호출은 타임라인 스텝으로 적는다.
    streams = getattr(provider, "streams_events", False)

    def on_stdout(text):
        nonlocal step_id
        stdout_parts.append(text)
        if streams:
            for ev in provider.iter_events(text):
                db.create_execution_step(
                    conn, job["message_id"], kind=ev.kind, title=ev.title,
                    detail=ev.detail, status="done", job_id=job["id"])
                if ev.display:
                    db.append_output(conn, job["id"], ev.display.rstrip("\n") + "\n")
        else:
            db.append_output(conn, job["id"], text)
            if job["message_id"]:
                if step_id is None:
                    step_id = db.create_execution_step(
                        conn, job["message_id"], kind="output_chunk",
                        title="실행 로그", status="running", job_id=job["id"])
                db.append_execution_step_detail(conn, step_id, text)
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
        # 죽이기 전에 파일을 이미 고쳤을 수 있다 — 타임아웃이어도 diff는 남긴다.
        if checkpoint.get("available"):
            diff = await gitcheckpoint.diff_since(workdir, checkpoint)
            checkpoint = {**checkpoint, **diff}
        db.update_job(conn, job["id"], status="failed", error="timeout",
                      finished_at=db.now_iso(), git_run=json.dumps(checkpoint))
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

    # 실행 도중 프로세스가 죽었더라도(rate limit·오류) 파일은 이미 바뀌었을 수
    # 있으므로, 아래 세 갈래(rate_limited/failed/done) 모두에 diff 를 남긴다.
    if checkpoint.get("available"):
        diff = await gitcheckpoint.diff_since(workdir, checkpoint)
        checkpoint = {**checkpoint, **diff}
    git_run = json.dumps(checkpoint)

    result = provider.parse_output(stdout, stderr, proc.returncode)
    resume_at = provider.detect_rate_limit(stdout + "\n" + stderr, proc.returncode)

    if resume_at is not None:
        db.update_job(
            conn, job["id"], status="rate_limited",
            resume_at=resume_at.isoformat(timespec="seconds"),
            session_id=result.session_id or job["session_id"],
            git_run=git_run,
        )
        db.log_usage(conn, provider.name, duration, "rate_limited", job["id"])
        return

    if proc.returncode != 0:
        db.update_job(conn, job["id"], status="failed",
                      error=(stderr or f"exit {proc.returncode}")[-2000:],
                      finished_at=db.now_iso(), git_run=git_run)
        db.log_usage(conn, provider.name, duration, "failed", job["id"])
        return

    db.update_job(
        conn, job["id"], status="done", output=result.text,
        session_id=result.session_id or job["session_id"],
        finished_at=db.now_iso(), git_run=git_run,
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


def _sync_message(conn, job_id):
    """잡의 최종 상태를 연결된 메시지·실행 스텝에 반영한다.

    run_job은 rate_limited/failed/done 등 여러 조기 반환 경로가 있어 각각에서
    메시지를 갱신하는 대신, run_job이 끝난 뒤(모든 경로 공통) 여기 한 곳에서
    잡의 최신 상태를 읽어 동기화한다.
    """
    job = db.get_job(conn, job_id)
    if job is None or not job["message_id"]:
        return
    message_id = job["message_id"]
    status = job["status"]
    if status == "rate_limited":
        # 재개 대기 중 — 실행 스텝은 그대로 두고(다음 재개 시 이어 씀)
        # 메시지 상태만 갱신해 UI가 "대기 중"을 보여줄 수 있게 한다.
        db.update_message(conn, message_id, status="rate_limited")
        return
    for step in db.list_execution_steps(conn, message_id):
        if step["status"] == "running":
            db.update_execution_step(
                conn, step["id"], status="done" if status == "done" else "failed")
    if status == "done":
        db.update_message(
            conn, message_id, status="done", body=job["output"],
            session_id=job["session_id"] or None,
            finished_at=job["finished_at"] or db.now_iso())
    elif status == "failed":
        canceled = job["error"] == "cancelled"
        db.update_message(
            conn, message_id, status="canceled" if canceled else "failed",
            error=job["error"], finished_at=job["finished_at"] or db.now_iso())


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
        _sync_message(conn, job["id"])
        stream_hub.publish(job["id"])
        release(job["id"], job["provider"])


async def run_test_goal(conn, goal_id):
    """상태를 running으로 변경 후 실행, 완료 시 done/failed로 전이."""
    db.update_test_goal(conn, goal_id, status="running")
    goal = db.get_test_goal(conn, goal_id)
    try:
        result = f"'{goal['name']}' 실행 완료"
        db.update_test_goal(conn, goal_id, status="done", result=result)
    except Exception as e:
        db.update_test_goal(conn, goal_id, status="failed", result=str(e))


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
                    _sync_message(conn, job["id"])
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
