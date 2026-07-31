"""모바일 사이드바 태스크 인스펙터 회귀 방지.

브라우저에서 재현했던 네 가지를 정적으로 못박는다:
1. 태스크 인스펙터와 채팅/실행 로그 패널이 같은 자리에 겹쳐 보이던 문제
   (인스펙터가 흐름에 남아 있었고, 탭을 누르면 패널이 다시 켜졌다).
2. 태스크 채팅 입력창에 닿을 수 없던 문제(인스펙터에 내부 스크롤이 없었다).
3. 좁은 화면에서 작업창이 폼으로만 꽉 차던 문제(에이전트·모델·추가 지시는
   '고급'으로 접고, 상단 배지와 중복되는 '상태: …' 줄은 없앤다).
4. 작업 뷰에서 페이지가 통째로 스크롤·드래그되던 문제(안쪽 캔버스에서만).
"""
import re
from pathlib import Path

CSS = Path("static/style.css").read_text(encoding="utf-8")
THEME = Path("static/orca-theme.css").read_text(encoding="utf-8")
RAIL_JS = Path("static/chat-rail.js").read_text(encoding="utf-8")
INSPECTOR_JS = Path("static/task-inspector.js").read_text(encoding="utf-8")


def _rule(css, selector):
    """해당 선택자 규칙의 본문(첫 매치)을 돌려준다."""
    m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
    assert m, f"CSS 규칙을 찾지 못했습니다: {selector}"
    return m.group(1)


def test_task_context_shares_panel_layer_and_scrolls_internally():
    """인스펙터는 패널과 같은 레이어(absolute inset:0)에 놓여 겹치지 않고,
    자체 스크롤을 가져 아래쪽 태스크 채팅까지 닿을 수 있어야 한다."""
    body = _rule(CSS, ".orca-task-context")
    assert "position: absolute" in body
    assert "inset: 0" in body
    assert "overflow-y: auto" in body

    # 패널 쪽도 같은 레이어 규칙(absolute inset:0)을 쓰고 있어야 성립한다.
    panel = _rule(THEME, ".orca-rail-panel")
    assert "position: absolute" in panel and "inset: 0" in panel


def test_rail_tab_click_closes_task_context():
    """탭을 누르면 인스펙터를 먼저 닫는다 — 안 닫으면 패널과 겹쳐 그려진다."""
    m = re.search(r"tabBtn\.addEventListener\(\"click\", \(\) => \{(.*?)\}\);",
                  RAIL_JS, re.S)
    assert m, "레일 탭 클릭 핸들러를 찾지 못했습니다"
    handler = m.group(1)
    assert "inspector.close()" in handler
    assert handler.index("inspector.close()") < handler.index("activateTab(")


def test_task_chat_composer_sticks_to_bottom():
    body = _rule(CSS, ".orca-task-chat-form")
    assert "position: sticky" in body
    assert "bottom: 0" in body


def test_advanced_fields_collapsed_on_narrow_screens_only():
    """담당 에이전트·모델·추가 지시는 '고급'으로 묶고, 데스크톱에서만 펼친 채 연다."""
    assert 'class="orca-task-advanced"' in INSPECTOR_JS
    assert 'wideScreen() ? " open" : ""' in INSPECTOR_JS
    assert 'matchMedia("(min-width: 768px)")' in INSPECTOR_JS
    for field in ('name="agent"', 'name="model"', 'name="extra_instruction"'):
        assert field in INSPECTOR_JS
    # 세 필드가 모두 '고급' <details> 안에 들어 있어야 한다.
    start = INSPECTOR_JS.index('class="orca-task-advanced"')
    advanced = INSPECTOR_JS[start:INSPECTOR_JS.index("</details>", start)]
    for field in ('name="agent"', 'name="model"', 'name="extra_instruction"'):
        assert field in advanced


def test_status_line_removed_from_sidebar_form():
    """상태는 폼 상단 배지로 이미 보인다 — 좁은 화면에서 같은 정보를 반복하지 않는다."""
    assert 'tt("상태")' not in INSPECTOR_JS
    assert 'class="badge badge-${taskEscape(task.status)}"' in INSPECTOR_JS


def test_workspace_view_is_pinned_and_only_canvas_pans():
    """작업 뷰에서는 페이지가 스크롤·고무줄되지 않고, 끌기는 캔버스가 받는다."""
    # 캔버스 컨테이너가 브라우저 기본 팬/줌을 가져가지 않아야 SVG 밖 여백에서
    # 시작한 끌기도 페이지가 아니라 다이어그램을 움직인다.
    assert re.search(r"\.graph-canvas\s*\{[^}]*touch-action:\s*none[^}]*"
                     r"overscroll-behavior:\s*contain", CSS)

    mobile = THEME[THEME.index("@media (max-width: 767px)"):]
    pinned = _rule(mobile, 'body[data-orca-mobile-view="workspace"]')
    assert "overflow: hidden" in pinned
    assert "overscroll-behavior: none" in pinned


def test_inspector_module_is_shared_by_rail_and_home():
    """같은 인스펙터를 프로젝트 페이지 레일과 홈 좌측 사이드바가 함께 쓴다."""
    assert "window.mountTaskInspector" in INSPECTOR_JS
    assert "mountTaskInspector" in RAIL_JS
    home = Path("static/home.js").read_text(encoding="utf-8")
    assert "mountTaskInspector" in home
    for tpl in ("templates/index.html", "templates/partials/chat_rail.html"):
        assert "task-inspector.js" in Path(tpl).read_text(encoding="utf-8"), tpl
