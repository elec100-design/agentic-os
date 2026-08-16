"""승인 게이트 — 제안 수확, 승인 후 실행, 게이트 해제, 인박스."""
import asyncio

import pytest
from fastapi.testclient import TestClient

from app import approvals, config, db, worker
from app.providers import PROVIDERS, ParseResult


def _client(tmp_env):
    from app.main import app
    return TestClient(app)


def _proposal(title="메일 발송", action="foo@bar.com 으로 견적서 발송"):
    return ('```json\n{"approval": {"title": "' + title + '", '
            '"detail": "고객이 요청한 견적서", "action": "' + action + '", '
            '"risk": "발송하면 되돌릴 수 없음"}}\n```')


class _ScriptedProvider:
    """정해진 답을 돌려주고, 받은 프롬프트와 read_only 를 기록한다."""
    name = "claude"
    supports_resume = True
    supports_read_only = True
    reply = "완료했습니다."
    prompts = []
    read_only_flags = []

    def build_command(self, prompt, session_id=None, model=None,
                      mcp_config_path=None, read_only=False):
        _ScriptedProvider.prompts.append(prompt)
        _ScriptedProvider.read_only_flags.append(read_only)
        return ["python3", "-c", f"print({_ScriptedProvider.reply!r})"]

    def parse_output(self, stdout, stderr, code):
        return ParseResult(text=stdout.strip(), session_id="s1")

    def detect_rate_limit(self, output, exit_code, now=None):
        return None


def _run(conn, job_id, providers):
    """_run_tracked 와 같은 순서로 1건 실행 (수확 → 메시지 동기화)."""
    async def go():
        await worker.run_job(conn, db.get_job(conn, job_id),
                             providers=providers, save=False)
    asyncio.run(go())
    approvals.harvest(conn, job_id)
    worker._sync_message(conn, job_id)


# --- 제안 파싱 ---------------------------------------------------------------

def test_parse_proposals_extracts_and_strips(tmp_env):
    """제안 JSON 은 결과물에서 걷어낸다 — 채널 본문·하류 프롬프트로 흘러간다."""
    text = f"견적서를 준비했습니다.\n\n{_proposal()}\n"
    cleaned, items = approvals.parse_proposals(text)
    assert len(items) == 1
    assert items[0]["title"] == "메일 발송"
    assert items[0]["risk"] == "발송하면 되돌릴 수 없음"
    assert "approval" not in cleaned and "견적서를 준비했습니다." in cleaned


def test_parse_proposals_handles_multiple_and_caps(tmp_env, monkeypatch):
    monkeypatch.setattr(config, "APPROVAL_MAX_PER_JOB", 2)
    text = "\n".join(_proposal(f"동작{i}") for i in range(5))
    cleaned, items = approvals.parse_proposals(text)
    assert [i["title"] for i in items] == ["동작0", "동작1"]
    # 상한을 넘은 블록은 본문에 그대로 남는다(정리 대상이 아니다)
    assert "동작2" in cleaned


def test_parse_proposals_ignores_malformed(tmp_env):
    for text in ("그냥 결과물",
                 "결과\n```json\n{깨진 JSON\n```",
                 '결과\n```json\n{"approval": {"detail": "제목 없음"}}\n```',
                 '결과\n```json\n{"handoff": {"to": "x", "title": "y"}}\n```'):
        cleaned, items = approvals.parse_proposals(text)
        assert items == [] and cleaned == text


# --- 수확 -------------------------------------------------------------------

def test_gate_section_only_for_ask_policy(tmp_env):
    conn = db.get_conn()
    auto = db.get_agent(conn, db.create_agent(conn, "a", "A"))
    ask = db.get_agent(conn, db.create_agent(conn, "b", "B",
                                             approval_policy="ask"))
    assert approvals.gate_section(auto) == ""
    assert approvals.gate_section(None) == ""
    assert "되돌릴 수 없는 동작은 실행하지 말 것" in approvals.gate_section(ask)


def test_ask_agent_gets_the_gate_in_its_prompt(tmp_env):
    conn = db.get_conn()
    agent_id = db.create_agent(conn, "sender", "Sender", provider="claude",
                               approval_policy="ask")
    job_id = db.create_job(conn, "견적서 보내줘", "claude", agent_id=agent_id)
    _ScriptedProvider.prompts = []
    _run(conn, job_id, {**PROVIDERS, "claude": _ScriptedProvider()})
    assert "되돌릴 수 없는 동작은 실행하지 말 것" in _ScriptedProvider.prompts[0]


def test_harvest_creates_pending_approvals_and_cleans_output(tmp_env):
    conn = db.get_conn()
    agent_id = db.create_agent(conn, "sender", "Sender", provider="claude",
                               approval_policy="ask")
    _ScriptedProvider.reply = f"준비했습니다.\n\n{_proposal()}"
    job_id = db.create_job(conn, "견적서 보내줘", "claude", agent_id=agent_id)
    _run(conn, job_id, {**PROVIDERS, "claude": _ScriptedProvider()})

    pending = db.list_approvals(conn)
    assert len(pending) == 1
    assert pending[0]["title"] == "메일 발송" and pending[0]["status"] == "pending"
    assert pending[0]["agent_id"] == agent_id and pending[0]["job_id"] == job_id
    # 잡 출력에서 JSON 이 사라졌다
    assert "approval" not in db.get_job(conn, job_id)["output"]


def test_auto_policy_agent_does_not_create_approvals(tmp_env):
    """정책이 auto 면 제안 형식이 나와도 승인 큐를 만들지 않는다."""
    conn = db.get_conn()
    agent_id = db.create_agent(conn, "doer", "Doer", provider="claude")
    _ScriptedProvider.reply = f"했습니다.\n\n{_proposal()}"
    job_id = db.create_job(conn, "해줘", "claude", agent_id=agent_id)
    _run(conn, job_id, {**PROVIDERS, "claude": _ScriptedProvider()})
    assert db.list_approvals(conn) == []


def test_job_without_agent_is_unaffected(tmp_env):
    conn = db.get_conn()
    _ScriptedProvider.reply = f"했습니다.\n\n{_proposal()}"
    job_id = db.create_job(conn, "해줘", "claude")
    _run(conn, job_id, {**PROVIDERS, "claude": _ScriptedProvider()})
    assert db.list_approvals(conn) == []
    assert "approval" in db.get_job(conn, job_id)["output"]  # 손대지 않는다


# --- 승인 후 실행 ------------------------------------------------------------

def test_approve_creates_an_execution_job_with_the_gate_lifted(tmp_env):
    """승인된 동작을 실행하는 잡에는 게이트도 읽기 전용도 걸리지 않는다 —
    그러지 않으면 승인해도 또 제안만 하고 끝나 무한 왕복이 된다."""
    conn = db.get_conn()
    agent_id = db.create_agent(conn, "sender", "Sender", provider="claude",
                               approval_policy="ask", read_only=1)
    _ScriptedProvider.reply = f"준비했습니다.\n\n{_proposal()}"
    job_id = db.create_job(conn, "견적서 보내줘", "claude", agent_id=agent_id)
    _run(conn, job_id, {**PROVIDERS, "claude": _ScriptedProvider()})

    approval = db.list_approvals(conn)[0]
    exec_job_id = approvals.approve(conn, approval["id"], note="정중하게 써줘")

    approval = db.get_approval(conn, approval["id"])
    assert approval["status"] == "approved"
    assert approval["execution_job_id"] == exec_job_id

    exec_job = db.get_job(conn, exec_job_id)
    assert exec_job["agent_id"] == agent_id
    assert "승인했습니다" in exec_job["prompt"]
    assert "정중하게 써줘" in exec_job["prompt"]

    _ScriptedProvider.reply = "발송 완료."
    _ScriptedProvider.prompts, _ScriptedProvider.read_only_flags = [], []
    _run(conn, exec_job_id, {**PROVIDERS, "claude": _ScriptedProvider()})
    # 게이트 지시가 붙지 않았고, 읽기 전용도 풀렸다
    assert "되돌릴 수 없는 동작은 실행하지 말 것" not in _ScriptedProvider.prompts[0]
    assert _ScriptedProvider.read_only_flags[0] is False
    # 실행 결과가 새 승인을 또 만들지 않는다
    assert db.count_pending_approvals(conn) == 0


def test_read_only_ask_agent_is_gated_before_approval(tmp_env):
    """승인 전에는 읽기 전용이 그대로 걸린다 — 이 조합이 유일한 실제 강제다."""
    conn = db.get_conn()
    agent_id = db.create_agent(conn, "sender", "Sender", provider="claude",
                               approval_policy="ask", read_only=1)
    _ScriptedProvider.reply = "준비했습니다."
    _ScriptedProvider.read_only_flags = []
    job_id = db.create_job(conn, "보내줘", "claude", agent_id=agent_id)
    _run(conn, job_id, {**PROVIDERS, "claude": _ScriptedProvider()})
    assert _ScriptedProvider.read_only_flags[0] is True


def test_reject_does_not_execute(tmp_env):
    conn = db.get_conn()
    agent_id = db.create_agent(conn, "sender", "Sender", provider="claude",
                               approval_policy="ask")
    approval_id = db.create_approval(conn, "메일 발송", agent_id=agent_id)
    approvals.reject(conn, approval_id, note="지금은 하지 마세요")

    approval = db.get_approval(conn, approval_id)
    assert approval["status"] == "rejected"
    assert approval["execution_job_id"] is None
    assert approval["decided_note"] == "지금은 하지 마세요"
    assert db.list_jobs(conn) == []


def test_decisions_are_not_repeatable(tmp_env):
    conn = db.get_conn()
    agent_id = db.create_agent(conn, "sender", "Sender", provider="claude",
                               approval_policy="ask")
    approval_id = db.create_approval(conn, "메일 발송", agent_id=agent_id)
    approvals.approve(conn, approval_id)
    with pytest.raises(ValueError):
        approvals.approve(conn, approval_id)
    with pytest.raises(ValueError):
        approvals.reject(conn, approval_id)


def test_approve_fails_when_the_agent_is_gone(tmp_env):
    conn = db.get_conn()
    agent_id = db.create_agent(conn, "sender", "Sender", provider="claude",
                               approval_policy="ask")
    approval_id = db.create_approval(conn, "메일 발송", agent_id=agent_id)
    db.archive_agent(conn, agent_id)
    with pytest.raises(ValueError):
        approvals.approve(conn, approval_id)


def test_channel_approval_reports_back_into_the_thread(tmp_env):
    """채널에서 나온 제안은 결과도 그 쓰레드에 남아야 한다 — 밖에서 조용히
    실행되면 무슨 일이 있었는지 대화 기록에서 사라진다."""
    conn = db.get_conn()
    agent_id = db.create_agent(conn, "sender", "Sender", provider="claude",
                               approval_policy="ask")
    cid = db.create_channel(conn, "고객")
    root = db.create_message(conn, cid, "user", "견적서 보내줘", author="user")
    msg = db.create_message(conn, cid, "agent", "", author="sender",
                            parent_id=root, status="queued",
                            author_agent_id=agent_id)
    approval_id = db.create_approval(conn, "메일 발송", agent_id=agent_id,
                                     channel_id=cid, message_id=msg)

    approvals.approve(conn, approval_id)
    thread = db.list_thread(conn, root)
    assert thread[-1]["role"] == "agent" and thread[-1]["status"] == "queued"
    assert thread[-1]["author_agent_id"] == agent_id

    other = db.create_approval(conn, "파일 삭제", agent_id=agent_id,
                               channel_id=cid, message_id=msg)
    approvals.reject(conn, other, note="위험함")
    thread = db.list_thread(conn, root)
    assert thread[-1]["role"] == "system" and "위험함" in thread[-1]["body"]


# --- 인박스 -----------------------------------------------------------------

def test_approvals_inbox_page_and_api(tmp_env):
    with _client(tmp_env) as client:
        conn = db.get_conn(config.DB_PATH)
        agent_id = db.create_agent(conn, "sender", "Sender", provider="claude",
                                   approval_policy="ask")
        approval_id = db.create_approval(
            conn, "메일 발송", detail="견적서를 보냅니다",
            action="foo@bar.com 으로 발송", risk="되돌릴 수 없음",
            agent_id=agent_id)

        r = client.get("/api/approvals")
        assert r.json()["pending_count"] == 1
        assert r.json()["items"][0]["agent_slug"] == "sender"

        page = client.get("/approvals").text
        assert "메일 발송" in page and "@sender" in page and "되돌릴 수 없음" in page

        r = client.post(f"/api/approvals/{approval_id}/approve",
                        json={"note": "정중하게"})
        assert r.status_code == 200 and r.json()["job_id"]
        assert client.get("/api/approvals").json()["pending_count"] == 0

        # 같은 항목을 다시 처리하려 하면 400
        assert client.post(f"/api/approvals/{approval_id}/reject").status_code == 400

        # 처리된 항목은 이력에 남는다
        assert "승인됨" in client.get("/approvals").text or "Approved" in client.get("/approvals").text


def test_approvals_api_rejects_unknown_id(tmp_env):
    with _client(tmp_env) as client:
        assert client.post("/api/approvals/999/approve").status_code == 400


# --- 진입 경로 ---------------------------------------------------------------

def test_sidebar_no_longer_links_to_approvals(tmp_env, completed_setup):
    """사이드바 하단에서 '승인'·'에이전트' 버튼을 뺐다(사용자 요청).

    인박스 자체는 /approvals 로 남아 있다 — 링크만 없앤 것이지 기능을 지운 게
    아니다. 대기 배지도 함께 사라졌으므로, 승인이 밀려 있어도 화면에 표시가
    나지 않는다는 점을 이 테스트가 명시적으로 못 박아 둔다.
    """
    with _client(tmp_env) as client:
        home = client.get("/").text
        assert 'href="/approvals"' not in home
        assert "approvals-badge" not in home
        # 페이지는 그대로 살아 있다
        assert client.get("/approvals").status_code == 200


def test_sidebar_foot_shows_model_settings(tmp_env, completed_setup):
    """하단은 '모델 설정'·'MCP 서버' 둘만 남는다 — 에이전트는 목록의 ＋ 로 간다."""
    with _client(tmp_env) as client:
        for path in ("/", "/channels/new"):
            page = client.get(path).text
            foot = page.split('class="side-foot"')[1].split("</div>")[0] \
                if 'class="side-foot"' in page else ""
            if not foot:
                continue
            assert 'href="/setup"' in foot
            assert 'href="/settings/agents"' not in foot
