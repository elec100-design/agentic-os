"""좌측 사이드바의 크기 — 폭 조절·접기, 그리고 목록 한 줄이 잘리던 문제.

PC(≥901px)에서 사이드바는 고정 200px 이었고 사용자가 조절할 수단이 없었다.
게다가 `.rail-preview { max-width: 13rem }`(208px)가 사이드바 내부 폭보다 넓고
`.rail-slug` 가 자리를 먼저 가져가 **에이전트 이름이 잘렸다**.

브라우저가 없어 픽셀은 검증할 수 없으므로(test_responsive_breakpoints 와 같은 한계)
마크업·CSS·JS 원문으로 계약만 확인한다.
"""
from pathlib import Path

from fastapi.testclient import TestClient

from app import db

HOME_JS = Path("static/home.js").read_text(encoding="utf-8")
STYLE_CSS = Path("static/style.css").read_text(encoding="utf-8")
ORCA_CSS = Path("static/orca-theme.css").read_text(encoding="utf-8")


def _client(tmp_env):
    from app.main import app
    return TestClient(app)


# --- 폭 조절 ----------------------------------------------------------------

def test_sidebar_width_is_a_variable_not_a_fixed_track(tmp_env, completed_setup):
    """고정 200px 이면 이름이 잘려도 사용자가 할 수 있는 게 없다."""
    assert "--aos-side-w: 240px" in ORCA_CSS
    assert "flex: 0 0 var(--aos-side-w)" in ORCA_CSS
    assert "flex: 0 0 200px" not in ORCA_CSS


def test_sidebar_has_a_resize_handle_like_the_right_rail(tmp_env, completed_setup):
    with _client(tmp_env) as client:
        home = client.get("/").text
        assert 'id="home-side-resize"' in home
        assert 'class="side-resize-handle"' in home
    assert ".side-resize-handle" in ORCA_CSS


def test_both_rails_share_one_resizer(tmp_env):
    """좌우가 같은 조작감이어야 한다 — 로직을 두 벌 두면 어긋난다."""
    assert "function mountResizer(" in HOME_JS
    assert HOME_JS.count("mountResizer({\n") == 2      # 좌·우 호출 두 곳
    assert "aos-home-side-width" in HOME_JS      # 폭을 기억한다
    assert 'dir: "right"' in HOME_JS and 'dir: "left"' in HOME_JS


def test_sidebar_width_is_clamped(tmp_env):
    assert "SIDE_MIN = 180" in HOME_JS and "SIDE_MAX = 360" in HOME_JS


# --- 접기 -------------------------------------------------------------------

def test_sidebar_can_collapse_to_icons(tmp_env):
    assert ".sidebar.is-collapsed { --aos-side-w: 64px; }" in ORCA_CSS
    assert "aos-home-side-collapsed" in HOME_JS
    # 서랍(≤900px)에서는 같은 버튼이 '닫기'로 남아야 한다
    assert "function isDrawer()" in HOME_JS


def test_collapsed_sidebar_keeps_settings_reachable(tmp_env, completed_setup):
    """접었다고 설정으로 갈 길이 막히면 안 된다 — 아이콘만 남기고 라벨을 접는다."""
    with _client(tmp_env) as client:
        home = client.get("/").text
        assert 'class="side-settings-icon"' in home
        assert 'class="side-settings-label"' in home
        assert 'href="/setup"' in home and 'href="/settings/mcp"' in home
    assert ".sidebar.is-collapsed .side-settings-label" in ORCA_CSS


# --- 목록 한 줄이 잘리던 문제 -------------------------------------------------

def test_preview_no_longer_has_a_fixed_max_width(tmp_env):
    """13rem(208px)은 사이드바 내부 폭보다 넓어 목록을 밀어냈다."""
    block = STYLE_CSS.split(".rail-preview {")[1].split("}")[0]
    assert "max-width" not in block
    assert "min-width: 0" in block


def test_the_name_wins_over_the_slug_when_space_runs_out(tmp_env, completed_setup):
    """@slug 가 nowrap 으로 자리를 먼저 가져가 이름이 잘렸다(`Researcher @researc`)."""
    with _client(tmp_env) as client:
        conn = db.get_conn()
        db.create_agent(conn, "researcher", "Researcher", provider="claude")
        home = client.get("/").text
        assert '<span class="rail-name">Researcher</span>' in home

    name = STYLE_CSS.split(".rail-name {")[1].split("}")[0]
    assert "text-overflow: ellipsis" in name and "min-width: 0" in name
    slug = STYLE_CSS.split(".rail-slug {")[1].split("}")[0]
    assert "flex: none" in slug          # 이름이 남는 폭을 다 쓴다
    assert ".sidebar.is-narrow .rail-slug" in STYLE_CSS   # 아주 좁으면 접는다
    assert "is-narrow" in HOME_JS


# --- 미리보기 문자열 ----------------------------------------------------------

def test_preview_strips_markdown(tmp_env):
    """목록은 렌더링이 아니라 한 줄 요약이다 — `저는 **Ch…` 처럼 별표가 보였다."""
    from app.main import _preview
    conn = db.get_conn()
    cid = db.create_channel(conn, "c")
    db.create_message(conn, cid, "agent",
                      "## 안녕하세요\n저는 **Chief** 입니다. `계획`을 *정리*했고 "
                      "[문서](http://x)를 봤습니다", author="a")
    preview = _preview(conn, cid)
    for mark in ("**", "`", "##", "](http"):
        assert mark not in preview, mark
    assert "Chief" in preview and "문서" in preview


def test_preview_leaves_ordinary_asterisks_alone(tmp_env):
    from app.main import _strip_markdown
    assert _strip_markdown("곱하기 2 * 3 은 6") == "곱하기 2 * 3 은 6"
