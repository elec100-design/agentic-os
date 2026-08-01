import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app import config, db, worker
from app.providers import ParseResult


class FakeProvider:
    """실제 CLI 대신 셸 명령을 실행하는 테스트용 어댑터."""
    name = "fake"

    def __init__(self, cmd, resume_at=None, session_id="s1"):
        self.cmd = cmd
        self.resume_at = resume_at
        self._session_id = session_id

    def build_command(self, prompt, session_id=None, model=None, mcp_config_path=None):
        return self.cmd

    def parse_output(self, stdout, stderr, exit_code):
        return ParseResult(text=stdout.strip(), session_id=self._session_id)

    def detect_rate_limit(self, output, exit_code, now=None):
        return self.resume_at


def _setup(tmp_env, provider):
    conn = db.get_conn(config.DB_PATH)
    job_id = db.create_job(conn, "테스트", "fake")
    job = db.claim_next_job(conn)
    return conn, job, {"fake": provider}


async def test_run_job_success(tmp_env):
    p = FakeProvider(["sh", "-c", "echo hello"])
    conn, job, providers = _setup(tmp_env, p)
    await worker.run_job(conn, job, providers=providers, save=False)
    fresh = db.get_job(conn, job["id"])
    assert fresh["status"] == "done"
    assert fresh["output"] == "hello"
    assert fresh["session_id"] == "s1"
    counts = db.usage_counts(conn, "2000-01-01")
    assert counts["fake"]["ok"] == 1


async def test_run_job_failure_preserves_stderr(tmp_env):
    p = FakeProvider(["sh", "-c", "echo boom >&2; exit 3"])
    conn, job, providers = _setup(tmp_env, p)
    await worker.run_job(conn, job, providers=providers, save=False)
    fresh = db.get_job(conn, job["id"])
    assert fresh["status"] == "failed"
    assert "boom" in fresh["error"]
    assert db.usage_counts(conn, "2000-01-01")["fake"]["failed"] == 1


async def test_run_job_rate_limited_stores_resume(tmp_env):
    resume = datetime.now(timezone.utc) + timedelta(hours=1)
    p = FakeProvider(["sh", "-c", "echo limited; exit 1"], resume_at=resume)
    conn, job, providers = _setup(tmp_env, p)
    await worker.run_job(conn, job, providers=providers, save=False)
    fresh = db.get_job(conn, job["id"])
    assert fresh["status"] == "rate_limited"
    assert fresh["resume_at"] == resume.isoformat(timespec="seconds")
    assert fresh["session_id"] == "s1"
    assert db.usage_counts(conn, "2000-01-01")["fake"]["rate_limited"] == 1


async def test_run_job_timeout(tmp_env):
    p = FakeProvider(["sh", "-c", "sleep 30"])
    conn, job, providers = _setup(tmp_env, p)
    db.update_job(conn, job["id"], timeout_sec=1)
    job = db.get_job(conn, job["id"])
    await worker.run_job(conn, job, providers=providers, save=False)
    fresh = db.get_job(conn, job["id"])
    assert fresh["status"] == "failed"
    assert "timeout" in fresh["error"]


async def test_run_job_missing_cli(tmp_env):
    p = FakeProvider(["definitely-not-a-real-command-xyz"])
    conn, job, providers = _setup(tmp_env, p)
    await worker.run_job(conn, job, providers=providers, save=False)
    assert db.get_job(conn, job["id"])["status"] == "failed"


async def test_run_job_cancelled_not_overwritten(tmp_env):
    p = FakeProvider(["sh", "-c", "echo hi"])
    conn, job, providers = _setup(tmp_env, p)
    db.update_job(conn, job["id"], status="failed", error="cancelled")
    await worker.run_job(conn, job, providers=providers, save=False)
    fresh = db.get_job(conn, job["id"])
    assert fresh["status"] == "failed"
    assert fresh["error"] == "cancelled"


async def test_run_job_saves_memory_note(tmp_env):
    p = FakeProvider(["sh", "-c", "echo 결과물"])
    conn, job, providers = _setup(tmp_env, p)
    await worker.run_job(conn, job, providers=providers, save=True)
    notes = list(config.MEMORY_DIR.glob("*.md"))
    assert len(notes) == 1
    assert "결과물" in notes[0].read_text(encoding="utf-8")


async def test_run_job_records_note_path_on_job(tmp_env):
    p = FakeProvider(["sh", "-c", "echo 결과물"])
    conn, job, providers = _setup(tmp_env, p)
    await worker.run_job(conn, job, providers=providers, save=True)
    note = list(config.MEMORY_DIR.glob("*.md"))[0]
    assert db.get_job(conn, job["id"])["note_path"] == str(note.resolve())


async def test_run_job_appends_to_thread_note(tmp_env):
    from app import memory
    origin = memory.save_note("원질문", "fake", "첫 답변", session_id="s0")
    p = FakeProvider(["sh", "-c", "echo 이어진결과"])
    conn = db.get_conn(config.DB_PATH)
    db.create_job(conn, "이어서", "fake", session_id="s0",
                  note_path=str(origin.resolve()))
    job = db.claim_next_job(conn)
    await worker.run_job(conn, job, providers={"fake": p}, save=True)
    notes = list(config.MEMORY_DIR.glob("*.md"))
    assert len(notes) == 1                      # 새 노트가 생기지 않음
    text = origin.read_text(encoding="utf-8")
    assert "## 결과" in text
    assert "## 결과 (2차)" in text
    assert "이어진결과" in text
    assert db.get_job(conn, job["id"])["note_path"] == str(origin.resolve())


async def test_run_job_falls_back_when_thread_note_deleted(tmp_env):
    from app import memory
    origin = memory.save_note("원질문", "fake", "첫 답변", session_id="s0")
    origin.unlink()
    p = FakeProvider(["sh", "-c", "echo 새결과"])
    conn = db.get_conn(config.DB_PATH)
    db.create_job(conn, "이어서", "fake", session_id="s0",
                  note_path=str(origin.resolve()))
    job = db.claim_next_job(conn)
    await worker.run_job(conn, job, providers={"fake": p}, save=True)
    notes = list(config.MEMORY_DIR.glob("*.md"))
    assert len(notes) == 1                      # 폴백으로 새 노트 생성
    assert db.get_job(conn, job["id"])["note_path"] == str(notes[0].resolve())


async def test_run_job_runs_in_workdir(tmp_env, tmp_path):
    workdir = tmp_path / "work"
    workdir.mkdir()
    p = FakeProvider(["sh", "-c", "pwd"])
    conn = db.get_conn(config.DB_PATH)
    db.create_job(conn, "테스트", "fake", workdir=str(workdir))
    job = db.claim_next_job(conn)
    await worker.run_job(conn, job, providers={"fake": p}, save=False)
    # pwd 출력이 workdir 여야 함 (macOS의 /private 심링크 고려해 endswith)
    assert db.get_job(conn, job["id"])["output"].endswith(str(workdir.name))


class PromptSpyProvider(FakeProvider):
    """실제로 CLI에 넘어간 프롬프트를 기록한다."""

    def __init__(self):
        super().__init__(["sh", "-c", "echo ok"])
        self.sent = None

    def build_command(self, prompt, session_id=None, model=None, mcp_config_path=None):
        self.sent = prompt
        return self.cmd


async def test_rerun_after_interruption_continues_session(tmp_env):
    """서버가 죽어 재큐잉된 잡(attempts>1)은 처음부터가 아니라 이어서 실행한다."""
    from app.providers import CONTINUE_PROMPT
    p = PromptSpyProvider()
    conn = db.get_conn(config.DB_PATH)
    db.create_job(conn, "원래 프롬프트", "fake")
    job = db.claim_next_job(conn)
    db.update_job(conn, job["id"], session_id="s1")
    db.recover_running(conn)          # 서버 재시작 복구 → queued로 되돌림
    job = db.claim_next_job(conn)     # attempts = 2
    await worker.run_job(conn, job, providers={"fake": p}, save=False)
    assert p.sent == CONTINUE_PROMPT


async def test_first_attempt_sends_original_prompt(tmp_env):
    p = PromptSpyProvider()
    conn, job, providers = _setup(tmp_env, p)
    db.update_job(conn, job["id"], session_id="s1")
    job = db.get_job(conn, job["id"])
    await worker.run_job(conn, job, providers=providers, save=False)
    assert p.sent == "테스트"


async def test_worker_loop_fails_job_over_max_attempts(tmp_env):
    conn = db.get_conn(config.DB_PATH)
    job_id = db.create_job(conn, "p", "fake")
    db.update_job(conn, job_id, attempts=config.MAX_ATTEMPTS)
    stop = asyncio.Event()
    providers = {"fake": FakeProvider(["sh", "-c", "echo hi"])}
    task = asyncio.create_task(
        worker.worker_loop(stop, providers=providers, save=False, poll_sec=0.1)
    )
    for _ in range(100):
        await asyncio.sleep(0.05)
        if db.get_job(conn, job_id)["status"] == "failed":
            break
    stop.set()
    await task
    fresh = db.get_job(conn, job_id)
    assert fresh["status"] == "failed"
    assert "max attempts" in fresh["error"]


async def test_run_job_strips_api_key_env(tmp_env, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-123")
    p = FakeProvider(["sh", "-c", "echo key=${ANTHROPIC_API_KEY:-none}"])
    conn, job, providers = _setup(tmp_env, p)
    await worker.run_job(conn, job, providers=providers, save=False)
    assert db.get_job(conn, job["id"])["output"].strip() == "key=none"


class ExplodingProvider(FakeProvider):
    """parse_output이 항상 예외를 던지는 테스트용 어댑터."""

    def parse_output(self, stdout, stderr, exit_code):
        raise RuntimeError("boom")


async def test_worker_loop_survives_run_job_exception(tmp_env):
    conn = db.get_conn(config.DB_PATH)
    bad_id = db.create_job(conn, "터짐", "bad")
    good_id = db.create_job(conn, "정상", "fake")
    stop = asyncio.Event()
    providers = {
        "bad": ExplodingProvider(["sh", "-c", "echo x"]),
        "fake": FakeProvider(["sh", "-c", "echo ok"]),
    }
    task = asyncio.create_task(
        worker.worker_loop(stop, providers=providers, save=False, poll_sec=0.1)
    )
    for _ in range(100):
        await asyncio.sleep(0.05)
        if db.get_job(conn, good_id)["status"] == "done":
            break
    stop.set()
    await task
    bad = db.get_job(conn, bad_id)
    good = db.get_job(conn, good_id)
    assert bad["status"] == "failed"
    assert "worker error" in bad["error"]
    assert good["status"] == "done"


# --- git 체크포인트 통합 (app/gitcheckpoint.py) --------------------------
# 세부 동작은 tests/test_gitcheckpoint.py 에서 검증한다. 여기서는 worker.run_job
# 이 실제로 그 결과를 job.git_run 에 남기는지만 확인한다.

def _git(args, cwd):
    import subprocess
    subprocess.run(["git", *args], cwd=cwd, check=True,
                   capture_output=True, text=True)


def _git_repo(tmp_path):
    d = tmp_path / "repo"
    d.mkdir()
    _git(["init", "-q", "."], d)
    _git(["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q",
         "--allow-empty", "-m", "init"], d)
    return d


async def test_run_job_records_git_diff_for_workdir_changes(tmp_env, tmp_path):
    import json
    d = _git_repo(tmp_path)
    p = FakeProvider(["sh", "-c", 'echo "새 파일" > created.txt'])
    conn = db.get_conn(config.DB_PATH)
    db.create_job(conn, "테스트", "fake", workdir=str(d))
    job = db.claim_next_job(conn)
    await worker.run_job(conn, job, providers={"fake": p}, save=False)

    fresh = db.get_job(conn, job["id"])
    git_run = json.loads(fresh["git_run"])
    assert git_run["available"] is True
    assert git_run["files"] == ["created.txt"]
    assert "created.txt" in git_run["stat"]


async def test_run_job_skips_git_checkpoint_when_workdir_dirty(tmp_env, tmp_path):
    """이미 사람이 고치던 파일이 있으면 체크포인트를 잡지 않는다."""
    import json
    d = _git_repo(tmp_path)
    (d / "already_editing.txt").write_text("사람이 만든 파일")
    p = FakeProvider(["sh", "-c", "echo ok"])
    conn = db.get_conn(config.DB_PATH)
    db.create_job(conn, "테스트", "fake", workdir=str(d))
    job = db.claim_next_job(conn)
    await worker.run_job(conn, job, providers={"fake": p}, save=False)

    git_run = json.loads(db.get_job(conn, job["id"])["git_run"])
    assert git_run == {"available": False, "reason": "dirty_at_start"}


async def test_run_job_records_git_diff_even_on_failure(tmp_env, tmp_path):
    """중간에 실패해도(exit != 0) 그 전에 고친 파일은 diff 에 남아야 한다."""
    import json
    d = _git_repo(tmp_path)
    p = FakeProvider(["sh", "-c", 'echo "부분 작업" > partial.txt; exit 1'])
    conn = db.get_conn(config.DB_PATH)
    db.create_job(conn, "테스트", "fake", workdir=str(d))
    job = db.claim_next_job(conn)
    await worker.run_job(conn, job, providers={"fake": p}, save=False)

    fresh = db.get_job(conn, job["id"])
    assert fresh["status"] == "failed"
    git_run = json.loads(fresh["git_run"])
    assert git_run["files"] == ["partial.txt"]


async def test_run_job_has_no_git_run_without_workdir(tmp_env):
    p = FakeProvider(["sh", "-c", "echo ok"])
    conn, job, providers = _setup(tmp_env, p)
    await worker.run_job(conn, job, providers=providers, save=False)
    fresh = db.get_job(conn, job["id"])
    import json
    assert json.loads(fresh["git_run"]) == {"available": False, "reason": "no_workdir"}


# --- 워크스페이스 지침 주입 (app/instructions.py) -------------------------

async def test_run_job_injects_workspace_instructions_into_prompt(tmp_env, tmp_path):
    """PromptSpyProvider 가 실제로 build_command 에 받은 프롬프트를 기록한다
    — 지침이 진짜 프롬프트 앞에 붙어서 CLI 로 넘어가는지 확인한다."""
    import json
    (tmp_path / ".agentic-os.md").write_text("항상 지켜야 할 규칙")
    p = PromptSpyProvider()
    conn = db.get_conn(config.DB_PATH)
    db.create_job(conn, "이 작업을 해줘", "fake", workdir=str(tmp_path))
    job = db.claim_next_job(conn)
    await worker.run_job(conn, job, providers={"fake": p}, save=False)

    assert "항상 지켜야 할 규칙" in p.sent
    assert p.sent.endswith("이 작업을 해줘")  # 지침 뒤에 원래 프롬프트가 그대로 온다
    applied = json.loads(db.get_job(conn, job["id"])["instructions_applied"])
    assert applied == [".agentic-os.md"]


async def test_run_job_records_no_instructions_when_workspace_has_none(tmp_env, tmp_path):
    p = FakeProvider(["sh", "-c", "echo ok"])
    conn = db.get_conn(config.DB_PATH)
    db.create_job(conn, "테스트", "fake", workdir=str(tmp_path))
    job = db.claim_next_job(conn)
    await worker.run_job(conn, job, providers={"fake": p}, save=False)
    assert db.get_job(conn, job["id"])["instructions_applied"] is None


# --- MCP 서버 설정 주입 (app/mcp_servers.py) -------------------------------

class _McpSpyProvider(FakeProvider):
    """실제 claude 처럼 supports_mcp=True 를 선언하고, 받은 경로를 기록한다."""
    supports_mcp = True

    def __init__(self):
        super().__init__(["sh", "-c", "echo ok"])
        self.received_path = "unset"

    def build_command(self, prompt, session_id=None, model=None, mcp_config_path=None):
        self.received_path = mcp_config_path
        return self.cmd


async def test_run_job_passes_mcp_config_path_when_provider_supports_it(tmp_env, tmp_path):
    from app import mcp_servers, workspace
    ws = workspace.add_local("proj", str(tmp_path))
    mcp_servers.add(ws["id"], "fs", "npx", ["-y", "server-fs"], {})

    p = _McpSpyProvider()
    conn = db.get_conn(config.DB_PATH)
    db.create_job(conn, "테스트", "fake", workdir=str(tmp_path))
    job = db.claim_next_job(conn)
    await worker.run_job(conn, job, providers={"fake": p}, save=False)

    assert p.received_path is not None
    import json
    data = json.loads(open(p.received_path).read())
    assert "fs" in data["mcpServers"]


async def test_run_job_passes_none_mcp_config_when_provider_lacks_support(tmp_env, tmp_path):
    """FakeProvider 는 supports_mcp 를 선언하지 않는다 — MCP 서버가 등록돼
    있어도 파일을 만들거나 넘기지 않아야 한다."""
    from app import mcp_servers, workspace
    ws = workspace.add_local("proj", str(tmp_path))
    mcp_servers.add(ws["id"], "fs", "npx")

    p = FakeProvider(["sh", "-c", "echo ok"])
    conn = db.get_conn(config.DB_PATH)
    db.create_job(conn, "테스트", "fake", workdir=str(tmp_path))
    job = db.claim_next_job(conn)
    await worker.run_job(conn, job, providers={"fake": p}, save=False)  # 에러 없이 끝나야 함
    assert db.get_job(conn, job["id"])["status"] == "done"


async def test_run_job_mcp_config_path_none_when_no_servers_registered(tmp_env, tmp_path):
    p = _McpSpyProvider()
    conn = db.get_conn(config.DB_PATH)
    db.create_job(conn, "테스트", "fake", workdir=str(tmp_path))
    job = db.claim_next_job(conn)
    await worker.run_job(conn, job, providers={"fake": p}, save=False)
    assert p.received_path is None
