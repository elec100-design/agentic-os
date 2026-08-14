import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from app import config, db, worker
from app.providers import PROVIDERS, ParseResult


def _client(tmp_env):
    from app.main import app
    return TestClient(app)


class _FakeProvider:
    name = "claude"
    supports_resume = True

    def build_command(self, prompt, session_id=None, model=None, mcp_config_path=None):
        return ["sh", "-c", "echo 'hello from agent'"]

    def parse_output(self, stdout, stderr, code):
        return ParseResult(text=stdout.strip(), session_id="sess-abc")

    def detect_rate_limit(self, output, exit_code, now=None):
        return None


def test_channel_page_renders_thread_panel(tmp_env):
    """채널 상세 페이지에는 답장 쓰레드를 여는 패널 마크업이 있어야 한다."""
    with _client(tmp_env) as client:
        r = client.post("/api/channels", json={"title": "test-ch", "topic": "t"})
        assert r.status_code == 201
        ch_id = r.json()["id"]

        r = client.get(f"/channels/{ch_id}")
        assert r.status_code == 200
        for needle in ("thread-panel", "thread-panel-scrim",
                       "thread-panel-body", "thread-panel-form"):
            assert needle in r.text, needle
        # 답장 버튼(msg-thread-btn)은 channels.js가 메시지별로 그려 넣는다 —
        # 정적 HTML에는 없으니 스크립트가 로드되는지와 로직 존재만 확인한다.
        assert "channels.js" in r.text
        js = Path("static/channels.js").read_text(encoding="utf-8")
        assert "msg-thread-btn" in js


def test_channel_message_lifecycle_run_job_trace_thread_reply(tmp_env, monkeypatch):
    """채널 메시지 생성 → 잡 실행 → 조회/trace/쓰레드 → 답장 → 채널 삭제까지
    한 번에 검증한다(기존 smoke_test_tmp.py를 정식 테스트로 옮김)."""
    fake_providers = dict(PROVIDERS)
    fake_providers["claude"] = _FakeProvider()

    with _client(tmp_env) as client:
        r = client.post("/api/channels",
                        json={"title": "빌드 이슈 채널", "topic": "빌드 실패 추적"})
        assert r.status_code == 201
        cid = r.json()["id"]

        assert client.get(f"/api/channels/{cid}").status_code == 200

        r = client.patch(f"/api/channels/{cid}", json={"topic": "새 토픽"})
        assert r.status_code == 200
        assert r.json()["topic"] == "새 토픽"

        r = client.post(f"/api/channels/{cid}/messages",
                        json={"body": "빌드가 실패해요", "provider": "claude"})
        assert r.status_code == 202
        msg = r.json()
        agent_message_id = msg["message_id"]
        job_id = msg["job_id"]

        conn = db.get_conn(config.DB_PATH)
        job = db.get_job(conn, job_id)
        asyncio.run(worker.run_job(conn, job, providers=fake_providers, save=False))
        worker._sync_message(conn, job_id)

        r = client.get(f"/api/messages/{agent_message_id}")
        assert r.status_code == 200
        assert r.json()["body"] == "hello from agent"

        assert client.get(f"/api/messages/{agent_message_id}/trace").status_code == 200
        assert client.get(f"/api/messages/{agent_message_id}/thread").status_code == 200

        r = client.post(f"/api/channels/{cid}/messages", json={
            "body": "고쳐졌나요?", "provider": "claude",
            "parent_id": agent_message_id,
        })
        assert r.status_code == 202

        assert client.delete(f"/api/channels/{cid}").status_code == 200


# --- 커스텀 에이전트: @멘션과 에이전트 간 대화 -------------------------------

class _ScriptedAgent:
    """프롬프트에 실린 에이전트 슬러그를 보고 정해진 답을 돌려주는 가짜 CLI."""
    name = "claude"
    supports_resume = True
    supports_read_only = True
    replies = {}
    sessions = []      # build_command 가 받은 session_id (실행 시점 값)

    def build_command(self, prompt, session_id=None, model=None,
                      mcp_config_path=None, read_only=False):
        _ScriptedAgent.sessions.append(session_id)
        self._slug = "?"
        for slug in _ScriptedAgent.replies:
            if f"(@{slug})" in prompt:
                self._slug = slug
                break
        reply = _ScriptedAgent.replies.get(self._slug, "기본 응답")
        return ["python3", "-c", f"print({reply!r})"]

    def parse_output(self, stdout, stderr, code):
        # 에이전트마다 다른 세션 id — 세션이 섞이면 테스트가 잡아낸다
        return ParseResult(text=stdout.strip(), session_id=f"sess-{self._slug}")

    def detect_rate_limit(self, output, exit_code, now=None):
        return None


def _drain(conn, providers, limit=10):
    """큐가 빌 때까지 잡을 실행한다(worker_loop 대역)."""
    async def run():
        for _ in range(limit):
            job = db.claim_next_job(conn)
            if job is None:
                return
            await worker.run_job(conn, job, providers=providers, save=False)
            worker._sync_message(conn, job["id"])
    asyncio.run(run())


def _two_agent_channel(client, conn):
    a1 = db.create_agent(conn, "researcher", "Researcher", persona="조사한다",
                         provider="claude")
    a2 = db.create_agent(conn, "reviewer", "Reviewer", persona="검토한다",
                         provider="claude")
    cid = client.post("/api/channels", json={"title": "team"}).json()["id"]
    client.post(f"/api/channels/{cid}/members", json={"agent_id": a1})
    client.post(f"/api/channels/{cid}/members", json={"agent_id": a2})
    return cid, a1, a2


def _agent_messages(conn):
    return list(conn.execute(
        "SELECT author, hop_depth, author_agent_id FROM messages "
        "WHERE author_agent_id IS NOT NULL ORDER BY id"))


def test_mention_routes_to_that_agent(tmp_env):
    with _client(tmp_env) as client:
        conn = db.get_conn(config.DB_PATH)
        cid, a1, _a2 = _two_agent_channel(client, conn)
        r = client.post(f"/api/channels/{cid}/messages",
                        json={"body": "@researcher 조사해줘"})
        assert r.status_code == 202
        assert r.json()["agent_id"] == a1
        job = db.get_job(conn, r.json()["job_id"])
        assert job["agent_id"] == a1


def test_mention_of_non_member_is_rejected(tmp_env):
    """실재하는 에이전트인데 멤버가 아니면 조용히 무시하지 않고 이유를 알린다."""
    with _client(tmp_env) as client:
        conn = db.get_conn(config.DB_PATH)
        db.create_agent(conn, "researcher", "Researcher", provider="claude")
        cid = client.post("/api/channels", json={"title": "solo"}).json()["id"]
        r = client.post(f"/api/channels/{cid}/messages",
                        json={"body": "@researcher 조사해줘"})
        assert r.status_code == 400
        assert "멤버" in r.json()["detail"]


def test_unknown_mention_is_plain_text(tmp_env):
    """아무 에이전트에도 없는 @는 그냥 텍스트다 — 기존 동작을 깨지 않는다."""
    with _client(tmp_env) as client:
        cid = client.post("/api/channels", json={"title": "c"}).json()["id"]
        r = client.post(f"/api/channels/{cid}/messages",
                        json={"body": "메일은 foo@bar.com 이고 @nobody 는 없음",
                              "provider": "claude"})
        assert r.status_code == 202
        assert "agent_id" not in r.json()


def test_agent_chat_off_by_default_blocks_the_chain(tmp_env):
    providers = {**PROVIDERS, "claude": _ScriptedAgent()}
    _ScriptedAgent.replies = {"researcher": "조사 끝. @reviewer 검토해줘."}
    with _client(tmp_env) as client:
        conn = db.get_conn(config.DB_PATH)
        cid, _a1, _a2 = _two_agent_channel(client, conn)
        assert db.get_channel(conn, cid)["agent_chat"] == 0  # 기본 꺼짐
        client.post(f"/api/channels/{cid}/messages", json={"body": "@researcher 조사"})
        _drain(conn, providers)
        # researcher 만 답하고 연쇄는 일어나지 않는다
        assert [m["author"] for m in _agent_messages(conn)] == ["researcher"]


def test_agent_chat_on_lets_an_agent_call_another(tmp_env):
    providers = {**PROVIDERS, "claude": _ScriptedAgent()}
    _ScriptedAgent.replies = {"researcher": "조사 끝. @reviewer 검토해줘.",
                              "reviewer": "검토 완료."}
    _ScriptedAgent.sessions = []
    with _client(tmp_env) as client:
        conn = db.get_conn(config.DB_PATH)
        cid, _a1, a2 = _two_agent_channel(client, conn)
        client.patch(f"/api/channels/{cid}", json={"agent_chat": True})
        client.post(f"/api/channels/{cid}/messages", json={"body": "@researcher 조사"})
        _drain(conn, providers)

        msgs = _agent_messages(conn)
        assert [m["author"] for m in msgs] == ["researcher", "reviewer"]
        assert [m["hop_depth"] for m in msgs] == [0, 1]
        # 세션 격리 — reviewer 는 researcher 의 세션을 들고 실행되지 않았다
        assert _ScriptedAgent.sessions == [None, None]
        # 그리고 각자 자기 세션만 남긴다(다음 호출 때 물려받을 값)
        root = conn.execute(
            "SELECT id FROM messages WHERE parent_id IS NULL").fetchone()["id"]
        assert db.latest_thread_session(conn, root, agent_id=a2)[0] == "sess-reviewer"
        assert db.latest_thread_session(
            conn, root, agent_id=msgs[0]["author_agent_id"])[0] == "sess-researcher"


def test_self_mention_does_not_loop(tmp_env):
    providers = {**PROVIDERS, "claude": _ScriptedAgent()}
    _ScriptedAgent.replies = {"researcher": "계속 생각한다 @researcher"}
    with _client(tmp_env) as client:
        conn = db.get_conn(config.DB_PATH)
        cid, _a1, _a2 = _two_agent_channel(client, conn)
        client.patch(f"/api/channels/{cid}", json={"agent_chat": True})
        client.post(f"/api/channels/{cid}/messages", json={"body": "@researcher 조사"})
        _drain(conn, providers)
        assert [m["author"] for m in _agent_messages(conn)] == ["researcher"]


def test_hop_limit_stops_the_chain_with_a_system_message(tmp_env, monkeypatch):
    """한도에 걸리면 실패가 아니라 정지 — 이유를 system 메시지로 남긴다."""
    monkeypatch.setattr(config, "AGENT_HOP_MAX", 1)
    providers = {**PROVIDERS, "claude": _ScriptedAgent()}
    _ScriptedAgent.replies = {"researcher": "@reviewer 봐줘",
                              "reviewer": "@researcher 다시 봐줘"}
    with _client(tmp_env) as client:
        conn = db.get_conn(config.DB_PATH)
        cid, _a1, _a2 = _two_agent_channel(client, conn)
        client.patch(f"/api/channels/{cid}", json={"agent_chat": True})
        client.post(f"/api/channels/{cid}/messages", json={"body": "@researcher 조사"})
        _drain(conn, providers)

        assert [m["author"] for m in _agent_messages(conn)] == ["researcher", "reviewer"]
        systems = list(conn.execute("SELECT body FROM messages WHERE role = 'system'"))
        assert len(systems) == 1 and "최대 깊이" in systems[0]["body"]


def test_chain_limit_stops_a_long_conversation(tmp_env, monkeypatch):
    monkeypatch.setattr(config, "AGENT_HOP_MAX", 99)
    monkeypatch.setattr(config, "AGENT_PINGPONG_MAX", 99)
    monkeypatch.setattr(config, "AGENT_CHAIN_MAX", 3)
    providers = {**PROVIDERS, "claude": _ScriptedAgent()}
    _ScriptedAgent.replies = {"researcher": "@reviewer 봐줘",
                              "reviewer": "@researcher 다시 봐줘"}
    with _client(tmp_env) as client:
        conn = db.get_conn(config.DB_PATH)
        cid, _a1, _a2 = _two_agent_channel(client, conn)
        client.patch(f"/api/channels/{cid}", json={"agent_chat": True})
        client.post(f"/api/channels/{cid}/messages", json={"body": "@researcher 조사"})
        _drain(conn, providers, limit=20)

        assert len(_agent_messages(conn)) == config.AGENT_CHAIN_MAX
        systems = list(conn.execute("SELECT body FROM messages WHERE role = 'system'"))
        assert systems and "상한" in systems[0]["body"]


def test_channel_member_api_and_page(tmp_env):
    with _client(tmp_env) as client:
        conn = db.get_conn(config.DB_PATH)
        cid, a1, _a2 = _two_agent_channel(client, conn)
        assert {m["slug"] for m in client.get(f"/api/channels/{cid}/members").json()} == {
            "researcher", "reviewer"}

        page = client.get(f"/channels/{cid}").text
        assert "@researcher" in page and "agent-chat-toggle" in page

        r = client.delete(f"/api/channels/{cid}/members/{a1}")
        assert [m["slug"] for m in r.json()] == ["reviewer"]

        # 채널을 지우면 멤버십도 함께 사라진다
        client.delete(f"/api/channels/{cid}")
        assert conn.execute(
            "SELECT COUNT(*) c FROM channel_members").fetchone()["c"] == 0
