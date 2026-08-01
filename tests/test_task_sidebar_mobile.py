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


# ─── iOS 입력 확대 방지 ────────────────────────────────────────────────
# 사파리는 포커스된 입력의 계산 font-size 가 16px 미만이면 페이지를 확대하고
# 그 배율이 입력창을 벗어난 뒤에도 남는다. 컴포넌트마다 따로 잡던 방식은 새
# 입력창이 생길 때 빠졌으므로(좌측 비전보드 컴포저), 한 규칙으로 일괄 적용한다.

def _mobile_zoom_rule():
    m = re.search(
        r"@media \(max-width: 767px\) \{\s*(textarea,.*?font-size: 16px !important;\s*\}\s*)\}",
        CSS, re.S)
    assert m, "모바일 입력 확대 방지 일괄 규칙을 찾지 못했습니다"
    return m.group(1)


def test_every_text_entry_control_is_16px_on_mobile():
    rule = _mobile_zoom_rule()
    # iOS 가 확대를 거는 입력 종류를 빠짐없이 덮어야 한다
    for sel in ("textarea", "select", 'input[type="text"]', 'input[type="search"]',
                'input[type="number"]', 'input[type="email"]',
                'input[type="password"]', 'input[type="tel"]', 'input[type="url"]'):
        assert sel in rule, f"{sel} 가 확대 방지 규칙에서 빠졌다"


def test_zoom_guard_beats_component_font_sizes():
    """각 컴포넌트가 좁은 화면용으로 폰트를 줄여 두므로 바닥값을 강제해야 한다."""
    assert "font-size: 16px !important;" in _mobile_zoom_rule()


def test_zoom_guard_is_not_duplicated_per_component():
    """예전처럼 컴포넌트마다 따로 잡으면 또 빠진다 — 16px 가드는 하나뿐이어야 한다."""
    hits = (CSS + THEME).count("font-size: 16px")
    assert hits == 1, f"16px 확대 가드가 {hits}곳에 흩어져 있다 — 일괄 규칙 하나로 모을 것"


def test_viewport_does_not_block_pinch_zoom():
    """확대 방지를 maximum-scale 로 하면 손가락 확대까지 막혀 접근성이 깨진다."""
    for tpl in ("templates/index.html", "templates/project.html"):
        head = Path(tpl).read_text(encoding="utf-8")
        assert "maximum-scale" not in head and "user-scalable" not in head, tpl


def test_left_drawer_is_as_wide_as_the_right_rail():
    """모바일 서랍이 데스크톱 도킹 폭(280px)이면 비전보드 컴포저가 접힌다."""
    drawer = re.search(r"@media \(max-width: 900px\) \{.*?\.sidebar \{(.*?)\}", CSS, re.S)
    assert drawer, ".sidebar 모바일 규칙을 찾지 못했습니다"
    assert "width: min(380px, 88vw)" in drawer.group(1)
    assert "width: min(380px, 88vw)" in THEME, "우측 레일과 같은 폭 식이어야 한다"


def test_vision_composer_bar_keeps_send_on_the_chip_row():
    """줄바꿈 컨테이너에서 flex 스페이서는 제 줄을 차지해 버튼을 밀어낸다."""
    assert ".vision-composer .composer-bar .spacer { display: none; }" in THEME
    assert ".vision-composer .composer-bar .send { margin-left: auto; }" in THEME
