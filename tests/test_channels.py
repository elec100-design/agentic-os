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

    def build_command(self, prompt, session_id=None, model=None):
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
