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


# ─── 노트 탭 ───────────────────────────────────────────────────────────
# 우측 레일 '노트'의 노트도 작업·보드처럼 중앙 탭으로 연다. refId 가 숫자가
# 아니라 파일 경로라 탭 시스템이 문자열 id 를 견뎌야 한다.

def _write_note(body="## 프롬프트\n안녕\n\n## 결과\n반가워요\n"):
    from app import config
    config.MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    p = config.MEMORY_DIR / "탭 테스트 노트.md"
    p.write_text("---\nprovider: claude\n---\n\n" + body, encoding="utf-8")
    return str(p)


def test_note_partial_renders_thread_data(completed_setup):
    path = _write_note()
    with _client() as client:
        r = client.get("/partials/note", params={"path": path})
        assert r.status_code == 200
        # 조각은 innerHTML 로 꽂히므로 인라인 스크립트 대신 JSON 으로 싣는다
        assert "data-note-data" in r.text
        assert "data-note-thread" in r.text
        assert "application/json" in r.text
        # 전체 페이지(이어서 진행하기 포함)로 가는 탈출구
        assert "/note?path=" in r.text


def test_note_partial_falls_back_to_body_when_not_a_thread(completed_setup):
    path = _write_note("그냥 평범한 메모입니다.\n")
    with _client() as client:
        r = client.get("/partials/note", params={"path": path})
        assert r.status_code == 200
        assert "data-note-body" in r.text
        assert "data-note-thread" not in r.text


def test_note_partial_head_probe_and_404(completed_setup):
    path = _write_note()
    with _client() as client:
        # 탭 복원 시 살아 있는지 확인하는 probe
        assert client.head("/partials/note", params={"path": path}).status_code == 200
        assert client.get("/partials/note", params={"path": "/없는/노트.md"}).status_code == 404


def test_home_js_opens_notes_as_tabs():
    home = Path("static/home.js").read_text(encoding="utf-8")
    assert "note: {" in home, "note 로더가 없다"
    assert "/partials/note?path=" in home
    # 노트 refId 는 파일 경로 — 숫자로 강제 변환하면 탭이 깨진다
    assert "textRef: true" in home
    assert 'LOADERS[kind]?.textRef ? String(spec.refId) : +spec.refId' in home
    # 레일의 노트 링크를 가로채 페이지 이동 대신 탭으로 연다
    assert '.note-link[href^="/note?path="]' in home
    assert 'openTab({ kind: "note"' in home
