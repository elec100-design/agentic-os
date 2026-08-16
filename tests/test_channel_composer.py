"""대화창(채널·DM) 컴포저 — 첨부·에이전트·모델·작업 위치 컨트롤.

우측 작업 채팅과 같은 조각(composer_controls.html)을 대화창도 쓴다. 한 페이지에
컴포저가 여럿이므로 id 가 아니라 class 로만 나가야 하고, 탭으로 나중에 열리는
대화창은 channels.js 가 직접 마운트해야 한다(app.js 의 일괄 마운트는 로드 시 1회).

브라우저가 없으므로 렌더된 HTML 과 JS 원문으로 확인한다
(test_vision_composer_controls.py 와 같은 방식).
"""
import re
from collections import Counter
from pathlib import Path

from fastapi.testclient import TestClient

from app import db

APP_JS = Path("static/app.js").read_text(encoding="utf-8")
CHANNELS_JS = Path("static/channels.js").read_text(encoding="utf-8")
VIEW_HTML = Path("templates/partials/channel_view.html").read_text(encoding="utf-8")

CONTROLS = [
    "composer-provider",   # 에이전트 hidden input
    "composer-model",      # 모델 hidden input
    "composer-files",      # 파일 첨부 input
    "file-chips",          # 첨부 칩
    "tools-btn",           # 첨부·도구 (+)
    "workspace-picker",    # 폴더/리포 선택
]


def _client(tmp_env):
    from app.main import app
    return TestClient(app)


def _channel(conn, title="팀"):
    cid = db.create_channel(conn, title)
    return cid


def _dm(client, conn, slug="researcher"):
    db.create_agent(conn, slug, slug.capitalize(), provider="claude")
    client.get(f"/dm/{slug}", follow_redirects=False)
    return db.list_channels(conn, kind="dm")[0]["id"]


# --- 컨트롤이 실제로 렌더되는가 ----------------------------------------------

def test_channel_composer_has_the_same_controls_as_the_job_chat(tmp_env, completed_setup):
    with _client(tmp_env) as client:
        conn = db.get_conn()
        cid = _channel(conn)
        for path in (f"/channels/{cid}", f"/partials/channel/{cid}"):
            page = client.get(path).text
            for control in CONTROLS:
                assert control in page, f"{path}: {control}"


def test_dm_composer_keeps_attachments_but_hides_the_agent_chip(tmp_env, completed_setup):
    """1:1 대화는 상대가 정해져 있다 — 에이전트·모델 칩은 접고 첨부·폴더는 남긴다."""
    with _client(tmp_env) as client:
        conn = db.get_conn()
        cid = _dm(client, conn)
        page = client.get(f"/partials/channel/{cid}").text
        assert "composer-files" in page and "workspace-picker" in page
        assert re.search(r'class="cbar-chip agent-btn"[^>]*hidden', page)
    # 모델 칩은 JS 가 켜고 끈다 — 에이전트 칩이 숨겨진 컴포저에서는 계속 숨긴다
    assert "const agentFixed = !!agentBtn?.hidden;" in APP_JS
    assert "if (agentFixed) { modelBtn.hidden = true; return; }" in APP_JS


def test_the_old_provider_select_is_gone(tmp_env, completed_setup):
    """에이전트 선택은 컴포저 칩으로 대체됐다 — select 가 남아 있으면 둘이 싸운다."""
    assert "new-thread-provider" not in VIEW_HTML
    assert "new-thread-provider" not in CHANNELS_JS


def test_ids_are_not_duplicated_in_a_tab(tmp_env, completed_setup):
    """탭을 여러 개 열면 id 가 겹친다 — 대화창 조각은 class 로만 나가야 한다."""
    with _client(tmp_env) as client:
        conn = db.get_conn()
        cid = _channel(conn)
        fragment = client.get(f"/partials/channel/{cid}").text
        ids = re.findall(r'\sid="([^"]+)"', fragment)
        assert not [i for i, n in Counter(ids).items() if n > 1]
        for global_id in ("file-chips", "tools-btn", "workspace-picker", "agent-btn"):
            assert f'id="{global_id}"' not in fragment


# --- 마운트 ------------------------------------------------------------------

def test_channels_js_mounts_the_composer_itself(tmp_env):
    """app.js 의 일괄 마운트는 페이지 로드 때 한 번뿐이다."""
    assert "window.mountComposer?.(newThreadForm)" in CHANNELS_JS
    # 작업 위치 목록은 htmx 가 아니라 직접 받아 온다(탭은 innerHTML 로 붙는다)
    assert '"/partials/workspaces"' in CHANNELS_JS
    assert "initWsPicker(CHANNEL.workdir" in CHANNELS_JS


def test_standalone_channel_page_loads_what_the_composer_needs(tmp_env, completed_setup):
    """전용 페이지에는 app.js 도 작업 위치 모달도 없었다 — ＋ 가 죽어 있었다."""
    with _client(tmp_env) as client:
        conn = db.get_conn()
        cid = _channel(conn)
        page = client.get(f"/channels/{cid}").text
        assert "app.js" in page
        assert 'id="ws-modal"' in page
        assert "const MODELS =" in page and "const AGENT_ORDER =" in page
        # app.js 가 사이드바 토글을 하므로 nav.js 를 겹쳐 싣지 않는다
        assert "nav.js" not in page


def test_home_and_channel_page_share_one_ws_modal_partial(tmp_env, completed_setup):
    with _client(tmp_env) as client:
        conn = db.get_conn()
        cid = _channel(conn)
        for path in ("/", f"/channels/{cid}"):
            page = client.get(path).text
            assert page.count('id="ws-modal"') == 1, path


# --- 전송 경로 ---------------------------------------------------------------

def test_send_uploads_first_then_posts_paths(tmp_env):
    assert "/api/uploads" in CHANNELS_JS
    assert "if (attachments === null) return;" in CHANNELS_JS   # 실패 시 발화를 잃지 않는다
    assert "clearAttachments()" in CHANNELS_JS


def test_picked_workdir_is_sent_and_remembered(tmp_env):
    assert "function pickedWorkdir()" in CHANNELS_JS
    assert "rememberWorkdir" in CHANNELS_JS
    assert 'JSON.stringify({ workdir })' in CHANNELS_JS


def test_attachment_chips_are_rendered_on_the_bubble(tmp_env):
    assert "function messageAttachments(m)" in CHANNELS_JS
    assert "msg-attachments" in CHANNELS_JS
    assert ".msg-attachments" in Path("static/style.css").read_text(encoding="utf-8")


def test_dead_controls_are_not_shown(tmp_env, completed_setup):
    """메모리·타임아웃은 폼 제출로만 전달된다 — JSON 으로 보내는 대화창에서는
    받는 쪽이 없다. 눌러도 아무 일 없는 항목을 두지 않는다."""
    with _client(tmp_env) as client:
        conn = db.get_conn()
        cid = _channel(conn)
        fragment = client.get(f"/partials/channel/{cid}").text
        assert 'name="attach_memory"' not in fragment
        assert 'name="timeout_min"' not in fragment
        assert "composer-files" in fragment      # 파일 첨부는 남는다

        # 우측 작업 채팅(POST /jobs)에는 그대로 있어야 한다
        home = client.get("/").text
        assert 'name="attach_memory"' in home and 'name="timeout_min"' in home
