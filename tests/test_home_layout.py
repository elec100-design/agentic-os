"""홈 화면(templates/index.html)의 Orca ADE식 3분할 셸 검증.

좌: 내비 사이드바 / 중앙: 멀티 탭 워크스페이스 / 우: 채팅·세션 레일,
그리고 최하단의 접히는 작업 큐 상태바가 렌더된 HTML에 실제로 존재하는지,
컴포저·채널 목록이 이동 후에도 온전한지 정적으로 확인한다.

브라우저가 없어 픽셀은 검증할 수 없으므로(테스트 test_responsive_breakpoints와
같은 한계) 마크업 구조와 조각 엔드포인트 계약만 확인한다.
"""
import json
import re
from pathlib import Path

from fastapi.testclient import TestClient

from app import db, config

HOME_ELEMENTS = [
    "orca-home-shell",     # 셸 최상위
    "orca-home-row",       # 사이드바 | 중앙 | 레일 행
    "orca-home-main",      # 중앙 메인뷰
    "orca-home-ws",        # 멀티 탭 워크스페이스
    'id="home-tabbar"',
    'id="home-tabbar-scroll"',
    'id="home-tab-panels"',
    "orca-side-rail",      # 우측 채팅·세션 레일
    'id="home-rail-resize"',
    'id="home-rail-toggle"',
    'data-rail-tab="chat"',
    'data-rail-tab="sessions"',
    "orca-statusbar",      # 최하단 작업 큐 상태바
    'id="statusbar-toggle"',
    'id="statusbar-body"',
]

# 컴포저는 레일로 옮겨졌을 뿐이므로 app.js가 잡는 ID가 모두 살아 있어야 한다.
COMPOSER_IDS = [
    'id="composer"', 'id="prompt"', 'id="file-chips"', 'id="auto-hint"',
    'id="provider-input"', 'id="model-input"', 'id="tools-btn"',
    'id="agent-btn"', 'id="model-btn"', 'id="workspace-picker"',
    'id="tools-popup"', 'id="agent-popup"', 'id="model-popup"',
]


def _client():
    from app.main import app
    return TestClient(app)


def test_home_has_three_pane_shell(completed_setup):
    with _client() as client:
        r = client.get("/")
        assert r.status_code == 200
        for needle in HOME_ELEMENTS:
            assert needle in r.text, f"missing {needle} in rendered home HTML"


def test_home_keeps_composer_intact(completed_setup):
    with _client() as client:
        body = client.get("/").text
        for needle in COMPOSER_IDS:
            assert needle in body, f"composer lost {needle} after moving into rail"


def test_job_queue_starts_collapsed(completed_setup):
    """작업큐는 기본적으로 접혀 있다 — 서버가 내려주는 HTML에서 본문이 hidden."""
    with _client() as client:
        body = client.get("/").text
        assert '<div class="orca-statusbar-body" id="statusbar-body" hidden>' in body
        assert 'aria-expanded="false"' in body
        # 접혀 있어도 큐 자체는 계속 폴링돼야 카운트·말풍선이 갱신된다.
        assert 'hx-get="/partials/jobs"' in body


def test_sessions_moved_from_sidebar_to_rail(completed_setup):
    """채널(세션) 목록은 좌측 사이드바가 아니라 우측 레일의 세션 탭에 있다."""
    with _client() as client:
        body = client.get("/").text
        rail_at = body.index("orca-side-rail")
        channels_at = body.index('id="channels"')
        assert channels_at > rail_at, "channels list must live inside the right rail"
        sidebar = body[body.index('<aside class="sidebar">'):body.index("</aside>")]
        assert 'id="channels"' not in sidebar


def test_partial_job_returns_shared_view(completed_setup):
    conn = db.get_conn(config.DB_PATH)
    job_id = db.create_job(conn, prompt="테스트 작업", provider="claude")
    with _client() as client:
        r = client.get(f"/partials/job/{job_id}")
        assert r.status_code == 200
        assert 'class="job-view"' in r.text
        assert f'data-job-id="{job_id}"' in r.text
        assert "테스트 작업" in r.text
        # 홈 탭의 복원 로직이 HEAD로 존재 여부만 확인한다.
        assert client.head(f"/partials/job/{job_id}").status_code == 200


def test_partial_job_404_for_missing(completed_setup):
    with _client() as client:
        assert client.get("/partials/job/999999").status_code == 404


def test_job_view_shows_conversation_and_follow_up(completed_setup):
    """탭에 대화(이전 턴 + 이번 턴)가 보이고, 끝난 작업엔 이어가기 컴포저가 붙는다."""
    conn = db.get_conn(config.DB_PATH)
    from app import memory
    note = memory.save_note("첫 질문", "claude", "첫 답변", session_id="sess-1")
    memory.append_note(note, "둘째 질문", "claude", "둘째 답변", session_id="sess-2")
    job_id = db.create_job(conn, prompt="둘째 질문", provider="claude",
                           session_id="sess-2", note_path=str(note.resolve()))
    db.update_job(conn, job_id, status="done", output="둘째 답변")
    with _client() as client:
        body = client.get(f"/partials/job/{job_id}").text
        assert 'class="chat-thread job-thread"' in body
        # 이전 턴은 JSON으로 실려 job-view.js가 말풍선으로 렌더한다.
        thread = json.loads(re.search(
            r'class="job-thread-src">(.*?)</script>', body, re.S).group(1))
        assert [(t["role"], t["content"]) for t in thread] == [
            ("user", "첫 질문"), ("assistant", "첫 답변")]
        # 이번 작업 자신의 턴은 노트에서 잘라내 중복되지 않는다(프롬프트 말풍선 한 번).
        assert body.count("둘째 질문") == 1
        # 세션 재개가 가능한 에이전트라 세션을 이어받는 컴포저가 붙는다.
        assert 'class="composer job-followup"' in body
        assert 'name="session_id" value="sess-2"' in body
        assert 'name="origin_note"' in body


def test_running_job_has_no_follow_up(completed_setup):
    conn = db.get_conn(config.DB_PATH)
    job_id = db.create_job(conn, prompt="진행 중", provider="claude")
    db.update_job(conn, job_id, status="running")
    with _client() as client:
        assert "job-followup" not in client.get(f"/partials/job/{job_id}").text


def test_unresumable_job_follows_up_with_note_context(completed_setup):
    """hermes 등 세션 재개 불가 에이전트는 노트를 컨텍스트로 붙여 이어간다."""
    conn = db.get_conn(config.DB_PATH)
    from app import memory
    note = memory.save_note("질문", "hermes", "답변")
    job_id = db.create_job(conn, prompt="질문", provider="hermes",
                           note_path=str(note.resolve()))
    db.update_job(conn, job_id, status="done", output="답변")
    with _client() as client:
        body = client.get(f"/partials/job/{job_id}").text
        assert 'name="context_note"' in body
        assert 'name="session_id"' not in body


def test_follow_up_post_returns_job_id_as_json(completed_setup):
    """탭 안 제출은 리다이렉트 대신 새 작업 id를 받아 그 자리에서 탭을 연다."""
    with _client() as client:
        r = client.post("/jobs", data={"prompt": "이어서", "provider": "claude"},
                        headers={"Accept": "application/json"})
        assert r.status_code == 200
        assert isinstance(r.json()["job_id"], int)
        # 일반 폼 제출은 그대로 대시보드로 리다이렉트한다.
        r2 = client.post("/jobs", data={"prompt": "새 작업", "provider": "claude"},
                         follow_redirects=False)
        assert r2.status_code == 303


def test_mobile_drawer_toggles_are_symmetric_and_reachable(completed_setup):
    """모바일에서 좌·우 서랍 버튼이 하단 양쪽 모서리에 대칭으로 놓이고,
    열려 있는 동안은 서랍 위에 겹치지 않게 감춰진다(닫기는 ✕·스크림·ESC)."""
    with _client() as client:
        body = client.get("/").text
        assert 'id="nav-toggle"' in body
        assert 'id="home-rail-toggle"' in body
        assert 'id="home-rail-close"' in body      # 레일 헤드의 ✕ (모바일)
        assert 'id="home-rail-scrim"' in body

    css = Path("static/orca-theme.css").read_text(encoding="utf-8")
    # 우측 레일 버튼: 하단 우측 모서리, 44px 이상 터치 타깃
    assert re.search(r"\.orca-side-rail-toggle\s*\{[^}]*right:\s*0\.7rem;"
                     r"[^}]*bottom:\s*2\.8rem;[^}]*width:\s*48px", css)
    # 좌측 사이드바 버튼: 같은 높이의 하단 좌측 모서리(대칭)
    assert re.search(r"body\.orca-home\s+#nav-toggle\s*\{[^}]*left:\s*0\.7rem;"
                     r"[^}]*bottom:\s*2\.8rem;[^}]*width:\s*48px", css)
    # 열린 서랍 위에서는 두 버튼 모두 사라진다
    assert "body.home-rail-open .orca-side-rail-toggle { display: none; }" in css
    assert "body.orca-home.nav-open #nav-toggle { display: none; }" in css
    # 좌측 서랍도 스크림을 얻어 밖을 탭하면 닫는다는 신호를 준다
    assert re.search(r"body\.orca-home\.nav-open::before\s*\{[^}]*z-index:\s*19", css)
    # 오프캔버스 구간에서는 폭 접기 대신 ✕ 로 닫는다
    assert ".orca-side-rail .orca-rail-collapse-btn { display: none; }" in css
    assert re.search(r"\.orca-rail-close-btn\s*\{[^}]*display:\s*inline-flex", css)

    js = Path("static/app.js").read_text(encoding="utf-8")
    assert 'e.target.closest(".sidebar")' in js     # 밖을 탭하면 좌측 서랍 닫기
    home = Path("static/home.js").read_text(encoding="utf-8")
    assert 'classList.remove("nav-open")' in home   # 두 서랍이 동시에 열리지 않는다


def test_job_page_still_renders_shared_view(completed_setup):
    """단독 작업 페이지도 같은 조각을 include한다(중복 마크업 없음)."""
    conn = db.get_conn(config.DB_PATH)
    job_id = db.create_job(conn, prompt="단독 페이지", provider="claude")
    with _client() as client:
        r = client.get(f"/jobs/{job_id}")
        assert r.status_code == 200
        assert 'class="job-view"' in r.text
        assert "job-view.js" in r.text
