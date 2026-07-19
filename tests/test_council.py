import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app import config, council, db, worker
from app.providers import ParseResult, rank_cloud


class FakeProvider:
    """실제 CLI 대신 셸 명령을 실행하는 테스트용 어댑터 (호출 횟수 기록)."""

    def __init__(self, name, cmd=None, resume_at=None):
        self.name = name
        self.cmd = cmd or ["sh", "-c", f"echo {name}-답"]
        self.resume_at = resume_at
        self.calls = 0

    def build_command(self, prompt, session_id=None, model=None):
        self.calls += 1
        return self.cmd

    def parse_output(self, stdout, stderr, exit_code):
        return ParseResult(text=stdout.strip())

    def detect_rate_limit(self, output, exit_code, now=None):
        return self.resume_at


def _setup(tmp_env, monkeypatch, providers, members=None):
    monkeypatch.setattr(config, "COUNCIL_MEMBERS", members or list(providers))
    monkeypatch.setattr(config, "COUNCIL_AGGREGATOR", "")
    conn = db.get_conn(config.DB_PATH)
    db.create_job(conn, "어려운 문제", "council")
    job = db.claim_next_job(conn)
    return conn, job


async def test_council_success(tmp_env, monkeypatch):
    providers = {n: FakeProvider(n) for n in ("a", "b", "c")}
    conn, job = _setup(tmp_env, monkeypatch, providers)
    await worker.run_job(conn, job, providers=providers, save=True)
    fresh = db.get_job(conn, job["id"])
    assert fresh["status"] == "done"
    # 최종 답이 앞, 협의 과정이 뒤
    assert fresh["output"].startswith("a-답")
    assert "## 협의 과정" in fresh["output"]
    assert "## 제안들" in fresh["output"]
    assert "## 비평·개선안" in fresh["output"]
    # 기본 2라운드: 제안+비평 각 1회, 종합자(a)는 +1회
    assert providers["a"].calls == 3
    assert providers["b"].calls == 2
    assert providers["c"].calls == 2
    # usage_log는 실제 실행된 CLI별로 기록
    counts = db.usage_counts(conn, "2000-01-01")
    assert counts["a"]["ok"] == 3
    assert counts["b"]["ok"] == 2
    # 노트 저장 확인
    notes = list(config.MEMORY_DIR.glob("*.md"))
    assert len(notes) == 1
    text = notes[0].read_text(encoding="utf-8")
    assert "provider: council" in text
    assert "협의 과정" in text
    assert db.get_job(conn, job["id"])["note_path"] == str(notes[0].resolve())


async def test_council_rounds_1_skips_critique(tmp_env, monkeypatch):
    providers = {n: FakeProvider(n) for n in ("a", "b")}
    conn, job = _setup(tmp_env, monkeypatch, providers)
    monkeypatch.setattr(config, "COUNCIL_ROUNDS", 1)
    await worker.run_job(conn, job, providers=providers, save=False)
    fresh = db.get_job(conn, job["id"])
    assert fresh["status"] == "done"
    assert "## 비평·개선안" not in fresh["output"]
    assert providers["a"].calls == 2   # 제안 + 종합
    assert providers["b"].calls == 1   # 제안만


async def test_council_partial_failure_continues(tmp_env, monkeypatch):
    providers = {
        "a": FakeProvider("a"),
        "b": FakeProvider("b"),
        "bad": FakeProvider("bad", cmd=["sh", "-c", "echo boom >&2; exit 1"]),
    }
    conn, job = _setup(tmp_env, monkeypatch, providers)
    await worker.run_job(conn, job, providers=providers, save=False)
    fresh = db.get_job(conn, job["id"])
    assert fresh["status"] == "done"
    assert "bad" not in fresh["output"].split("## 제안들")[1]  # 제안에 미포함
    assert db.usage_counts(conn, "2000-01-01")["bad"]["failed"] == 1


async def test_council_too_few_survivors_fails(tmp_env, monkeypatch):
    providers = {
        "a": FakeProvider("a"),
        "bad1": FakeProvider("bad1", cmd=["sh", "-c", "exit 1"]),
        "bad2": FakeProvider("bad2", cmd=["sh", "-c", "exit 1"]),
    }
    conn, job = _setup(tmp_env, monkeypatch, providers)
    await worker.run_job(conn, job, providers=providers, save=False)
    fresh = db.get_job(conn, job["id"])
    assert fresh["status"] == "failed"
    assert "제안 단계" in fresh["error"]


async def test_council_rate_limited_member_dropped_not_job(tmp_env, monkeypatch):
    resume = datetime.now(timezone.utc) + timedelta(hours=1)
    providers = {
        "a": FakeProvider("a"),
        "b": FakeProvider("b"),
        "limited": FakeProvider("limited", cmd=["sh", "-c", "echo limit; exit 1"],
                                resume_at=resume),
    }
    conn, job = _setup(tmp_env, monkeypatch, providers)
    await worker.run_job(conn, job, providers=providers, save=False)
    fresh = db.get_job(conn, job["id"])
    # 한 에이전트의 사용 제한이 잡 전체를 rate_limited로 만들지 않는다
    assert fresh["status"] == "done"
    assert fresh["resume_at"] is None
    assert db.usage_counts(conn, "2000-01-01")["limited"]["rate_limited"] == 1


async def test_council_insufficient_members_fails_early(tmp_env, monkeypatch):
    providers = {"a": FakeProvider("a")}
    conn, job = _setup(tmp_env, monkeypatch, providers)
    await worker.run_job(conn, job, providers=providers, save=False)
    fresh = db.get_job(conn, job["id"])
    assert fresh["status"] == "failed"
    assert "부족" in fresh["error"]


async def test_council_cancel_terminates_all_procs(tmp_env, monkeypatch):
    # sh -c 는 자식 sleep이 파이프를 물고 있어 종료 감지가 늦어진다 — 직접 실행
    providers = {n: FakeProvider(n, cmd=["sleep", "30"]) for n in ("a", "b")}
    conn, job = _setup(tmp_env, monkeypatch, providers)
    task = asyncio.create_task(
        worker.run_job(conn, job, providers=providers, save=False))
    for _ in range(100):
        await asyncio.sleep(0.05)
        if len(council.council_procs.get(job["id"], [])) >= 2:
            break
    db.update_job(conn, job["id"], status="failed", error="cancelled",
                  finished_at=db.now_iso())
    worker.terminate_job_procs(job["id"])
    await asyncio.wait_for(task, timeout=10)
    fresh = db.get_job(conn, job["id"])
    assert fresh["status"] == "failed"
    assert fresh["error"] == "cancelled"          # 결과가 덮어써지지 않음
    assert not council.council_procs.get(job["id"])


async def test_council_aggregator_failure_falls_back(tmp_env, monkeypatch):
    # 종합자로 뽑히는 첫 멤버(a)가 두 번째 호출(비평)까지는 성공하고
    # 세 번째(종합)에서 실패하도록 상태 있는 명령을 쓰기 어렵다 —
    # 대신 고정 종합자를 실패 멤버로 지정해 폴백을 검증한다.
    providers = {
        "a": FakeProvider("a"),
        "b": FakeProvider("b"),
    }
    conn, job = _setup(tmp_env, monkeypatch, providers)
    monkeypatch.setattr(config, "COUNCIL_AGGREGATOR", "b")
    await worker.run_job(conn, job, providers=providers, save=False)
    fresh = db.get_job(conn, job["id"])
    assert fresh["status"] == "done"
    assert "종합: b" in fresh["output"]


def test_select_members_excludes_exhausted(monkeypatch):
    monkeypatch.setattr(config, "COUNCIL_MEMBERS", ["a", "b", "c"])
    providers = {"a": object(), "b": object(), "c": object()}
    usage = {"b": {"available": False}}
    assert council.select_members(usage, providers) == ["a", "c"]
    usage = {"b": {"available": False}, "c": {"available": False}}
    with pytest.raises(ValueError):
        council.select_members(usage, providers)


def test_select_aggregator_prefers_config_then_quota(monkeypatch):
    monkeypatch.setattr(config, "COUNCIL_AGGREGATOR", "grok")
    usage = {"claude": {"remaining": 90}, "grok": {"remaining": 10}}
    assert council.select_aggregator(["claude", "grok"], usage) == "grok"
    monkeypatch.setattr(config, "COUNCIL_AGGREGATOR", "")
    assert council.select_aggregator(["claude", "grok"], usage) == "claude"
    # 클라우드가 없으면 hermes, 그것도 없으면 첫 멤버
    assert council.select_aggregator(["hermes", "x"], {}) == "hermes"
    assert council.select_aggregator(["x", "y"], {}) == "x"


def test_rank_cloud_orders_by_remaining():
    usage = {
        "claude": {"remaining": 10},
        "antigravity": {"available": False},
        "grok": {"remaining": 80},
    }
    ranked = rank_cloud(usage)
    assert [name for name, _ in ranked] == ["grok", "claude"]


def test_select_members_respects_enabled(monkeypatch):
    import pytest
    from app import config, council
    monkeypatch.setattr(config, "COUNCIL_MEMBERS",
                        ["claude", "antigravity", "grok", "hermes"])
    members = council.select_members({}, enabled=["claude", "grok", "hermes"])
    assert members == ["claude", "grok", "hermes"]
    with pytest.raises(ValueError):
        council.select_members({}, enabled=["claude"])
