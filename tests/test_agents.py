"""커스텀 에이전트 — 생성·페르소나 주입·read_only·프리셋 시드."""
import asyncio

import pytest
from fastapi.testclient import TestClient

from app import agents, db, settings, worker
from app.providers import PROVIDERS, ParseResult


def _client(tmp_env):
    from app.main import app
    return TestClient(app)


class _RecordingProvider:
    """실제로 CLI 에 넘어간 프롬프트와 read_only 를 잡아 두는 가짜 provider."""
    name = "claude"
    supports_resume = True
    supports_read_only = True

    def __init__(self):
        self.prompts = []
        self.read_only_flags = []

    def build_command(self, prompt, session_id=None, model=None,
                      mcp_config_path=None, read_only=False):
        self.prompts.append(prompt)
        self.read_only_flags.append(read_only)
        return ["sh", "-c", "echo done"]

    def parse_output(self, stdout, stderr, code):
        return ParseResult(text=stdout.strip(), session_id="sess-1")

    def detect_rate_limit(self, output, exit_code, now=None):
        return None


# --- 생성 -------------------------------------------------------------------

def test_slugify_falls_back_and_stays_unique(tmp_env):
    conn = db.get_conn()
    # 한글 이름은 ASCII 슬러그로 남는 글자가 없다 → 폴백 + 유일성 접미
    assert agents.slugify("조사 담당") == "agent"
    assert agents.slugify("Code Reviewer!") == "code-reviewer"
    first = agents.unique_slug(conn, "조사 담당")
    db.create_agent(conn, first, "조사 담당")
    assert agents.unique_slug(conn, "조사 담당") == "agent-2"


def test_archive_keeps_row_for_backreferences(tmp_env):
    """보관은 하드 삭제가 아니다 — 과거 메시지가 '누가 말했는지'를 잃으면 안 된다."""
    conn = db.get_conn()
    agent_id = db.create_agent(conn, "reviewer", "Reviewer")
    db.archive_agent(conn, agent_id)
    assert db.list_agents(conn) == []
    assert db.get_agent(conn, agent_id)["status"] == "archived"
    assert db.get_agent_by_slug(conn, "reviewer") is None


def test_seed_presets_is_idempotent_and_respects_deletion(tmp_env):
    conn = db.get_conn()
    created = agents.seed_presets(conn)
    assert len(created) == 3
    assert {a["slug"] for a in db.list_agents(conn)} == {
        "researcher", "builder", "reviewer"}
    # 두 번째 호출은 아무것도 만들지 않는다
    assert agents.seed_presets(conn) == []
    # 사용자가 프리셋을 지워도 되살아나지 않는다
    db.archive_agent(conn, db.get_agent_by_slug(conn, "reviewer")["id"])
    agents.seed_presets(conn)
    assert db.get_agent_by_slug(conn, "reviewer") is None


def test_setup_save_keeps_agents_seeded_flag(tmp_env):
    """셋업을 다시 저장해도 시드 플래그가 지워지면 안 된다(프리셋 부활 방지)."""
    settings.mark_agents_seeded()
    settings.save(["claude"])
    assert settings.load()["agents_seeded"] is True


# --- 실행 -------------------------------------------------------------------

def test_persona_is_prepended_to_the_actual_prompt(tmp_env):
    conn = db.get_conn()
    agent_id = db.create_agent(conn, "reviewer", "Reviewer",
                               persona="당신은 냉정한 검토자입니다.", provider="claude")
    job_id = db.create_job(conn, "이 코드를 봐줘", "claude", agent_id=agent_id)

    provider = _RecordingProvider()
    asyncio.run(worker.run_job(conn, db.get_job(conn, job_id),
                               providers={**PROVIDERS, "claude": provider},
                               save=False))
    sent = provider.prompts[0]
    assert "당신은 냉정한 검토자입니다." in sent
    assert sent.index("냉정한") < sent.index("이 코드를 봐줘")  # 페르소나가 앞
    assert "@reviewer" in sent


def test_job_without_agent_is_untouched(tmp_env):
    """agent_id 가 없는 잡(기존 경로)은 프롬프트가 그대로 나가야 한다."""
    conn = db.get_conn()
    job_id = db.create_job(conn, "그냥 프롬프트", "claude")
    provider = _RecordingProvider()
    asyncio.run(worker.run_job(conn, db.get_job(conn, job_id),
                               providers={**PROVIDERS, "claude": provider},
                               save=False))
    assert provider.prompts[0] == "그냥 프롬프트"
    assert provider.read_only_flags[0] is False


def test_read_only_agent_runs_claude_without_write_tools(tmp_env):
    conn = db.get_conn()
    agent_id = db.create_agent(conn, "researcher", "Researcher",
                               provider="claude", read_only=1)
    job_id = db.create_job(conn, "조사해줘", "claude", agent_id=agent_id)
    provider = _RecordingProvider()
    asyncio.run(worker.run_job(conn, db.get_job(conn, job_id),
                               providers={**PROVIDERS, "claude": provider},
                               save=False))
    assert provider.read_only_flags[0] is True

    # 실제 claude 명령행에서도 쓰기가 빠지는지 (plan 모드 + Bash 미허용)
    cmd = PROVIDERS["claude"].build_command("x", read_only=True)
    assert "plan" in cmd and "Bash" not in cmd
    assert "acceptEdits" in PROVIDERS["claude"].build_command("x")


def test_read_only_is_not_forced_on_other_clis(tmp_env):
    """claude 외의 CLI 는 실행 단위 권한 제어가 없다 — 인자를 받지도 않는다.
    (app/agents.py 상단 주석의 한계를 코드로 못 박아 둔다)"""
    for name, provider in PROVIDERS.items():
        if name == "claude":
            assert getattr(provider, "supports_read_only", False)
        else:
            assert not getattr(provider, "supports_read_only", False), name


def test_resolve_provider_auto_and_disabled(tmp_env):
    conn = db.get_conn()
    fixed = db.get_agent(conn, db.create_agent(conn, "b", "B", provider="claude"))
    auto = db.get_agent(conn, db.create_agent(conn, "a", "A", provider="auto"))

    assert agents.resolve_provider(conn, fixed, "일", enabled=["claude"])[0] == "claude"
    # auto 는 라우팅에 맡긴다 — 활성 목록 안에서만 고른다
    picked, _ = agents.resolve_provider(conn, auto, "코드를 구현해줘",
                                        enabled=["claude", "hermes"])
    assert picked in ("claude", "hermes")
    # 고정 CLI 가 비활성이면 실행을 거부한다(조용히 다른 CLI 로 바꾸지 않는다)
    with pytest.raises(agents.AgentError):
        agents.resolve_provider(conn, fixed, "일", enabled=["hermes"])


# --- 설정 화면 ---------------------------------------------------------------

def test_agents_settings_crud(tmp_env):
    with _client(tmp_env) as client:
        assert client.get("/settings/agents").status_code == 200

        r = client.post("/settings/agents/add",
                        data={"name": "리서처", "persona": "조사한다",
                              "provider": "auto", "read_only": "1"},
                        follow_redirects=False)
        assert r.status_code == 303
        conn = db.get_conn()
        agent = db.list_agents(conn)[0]
        assert agent["read_only"] == 1 and agent["provider"] == "auto"

        # 체크박스를 빼면 read_only 가 풀린다 (미체크는 폼에서 아예 안 온다)
        r = client.post(f"/settings/agents/{agent['id']}/edit",
                        data={"name": "리서처", "persona": "x", "provider": "auto"},
                        follow_redirects=False)
        assert r.status_code == 303
        assert db.get_agent(conn, agent["id"])["read_only"] == 0

        assert f"@{agent['slug']}" in client.get("/settings/agents").text

        r = client.post(f"/settings/agents/{agent['id']}/archive",
                        follow_redirects=False)
        assert r.status_code == 303
        assert db.list_agents(conn) == []


def test_agents_settings_rejects_empty_name(tmp_env):
    with _client(tmp_env) as client:
        r = client.post("/settings/agents/add", data={"name": "  "},
                        follow_redirects=False)
        assert r.status_code == 400
        assert db.list_agents(db.get_conn()) == []


def test_thread_prompt_lists_who_can_be_called(tmp_env):
    """부를 상대를 알려 주지 않으면 @멘션 규약은 무용지물이다."""
    conn = db.get_conn()
    a1 = db.create_agent(conn, "researcher", "Researcher", provider="claude")
    a2 = db.create_agent(conn, "reviewer", "Reviewer", provider="claude")
    cid = db.create_channel(conn, "team")
    db.add_channel_member(conn, cid, a1)
    db.add_channel_member(conn, cid, a2)
    root = db.create_message(conn, cid, "user", "@researcher 조사해줘", author="user")
    channel = db.get_channel(conn, cid)
    thread = db.list_thread(conn, root)
    trigger = db.get_message(conn, root)

    # agent_chat 이 꺼져 있으면 부를 상대를 알려 주지 않는다(어차피 무시된다)
    agents.spawn(conn, channel, root, trigger, db.get_agent(conn, a1))
    off_prompt = db.get_job(conn, 1)["prompt"]
    assert "다른 에이전트 부르기" not in off_prompt

    db.update_channel(conn, cid, agent_chat=1)
    channel = db.get_channel(conn, cid)
    on_prompt = agents.build_thread_prompt(
        conn, channel, thread, trigger,
        roster=[db.get_agent(conn, a2)])
    assert "@reviewer(Reviewer)" in on_prompt
    assert "다른 에이전트 부르기" in on_prompt
