"""홈 중앙 탭이 잡(kind=job)과 비전보드(kind=project)를 함께 다루는지 검증.

브라우저가 없어 동작은 못 돌리므로, 계약이 되는 지점(엔드포인트 응답 형태와
home.js가 그 계약을 부르는 코드)만 정적으로 확인한다 — 이 저장소의 기존
레이아웃 테스트와 같은 방식이다.
"""
from pathlib import Path

from fastapi.testclient import TestClient

from app import config, db

HOME_JS = Path("static/home.js").read_text(encoding="utf-8")


def _client():
    from app.main import app
    return TestClient(app)


def _project(conn):
    pid = db.create_project(conn, "목표")
    db.create_task(conn, pid, 1, "태스크1", "설명", "text", "claude")
    return pid


def test_create_project_returns_json_for_sidebar_chat(completed_setup):
    """좌측 비전보드 채팅은 리다이렉트 대신 project_id를 받아 그 자리에서 탭을 연다."""
    with _client() as client:
        r = client.post("/projects", data={"goal": "새 비전보드"},
                        headers={"Accept": "application/json"})
        assert r.status_code == 200
        assert isinstance(r.json()["project_id"], int)
        # 기존 폼 제출(/board 페이지)은 그대로 리다이렉트한다.
        r2 = client.post("/projects", data={"goal": "폼 제출"}, follow_redirects=False)
        assert r2.status_code == 303


def test_partial_board_supports_head_for_tab_restore(completed_setup):
    """탭 복원은 HEAD로 존재 여부만 확인한다(잡 탭과 같은 계약)."""
    conn = db.get_conn(config.DB_PATH)
    pid = _project(conn)
    with _client() as client:
        assert client.head(f"/partials/board/{pid}").status_code == 200
        assert client.get(f"/partials/board/{pid}").status_code == 200
        assert client.head("/partials/board/999999").status_code == 404


def test_home_js_has_project_tab_kind():
    assert 'project: {' in HOME_JS
    assert "/partials/board/" in HOME_JS
    assert "window.openHomeTab" in HOME_JS
    # 잡 탭 계약은 그대로 — job-view.js의 후속 작업 연결이 여기에 의존한다.
    assert "window.openJobTab" in HOME_JS
    assert "/partials/job/" in HOME_JS


def test_home_js_migrates_v1_tab_state():
    """기존 사용자의 열린 잡 탭이 새 저장 형식으로 조용히 이관돼야 한다."""
    assert "saved.jobIds" in HOME_JS
    assert 'kind: "job", refId: id' in HOME_JS
    assert '"aos-home-tabs"' in HOME_JS


def test_home_js_pauses_board_polling_on_inactive_tabs():
    """보드 탭 여러 개가 동시에 서버를 때리지 않아야 한다."""
    assert "tb.tabId === activeTabId" in HOME_JS


def test_home_js_opens_project_cards_and_vision_chat_as_tabs():
    assert '.project-card[href^="/projects/"]' in HOME_JS
    assert 'id="vision-composer"' in Path("templates/index.html").read_text(encoding="utf-8")
    assert 'headers: { Accept: "application/json" }' in HOME_JS
