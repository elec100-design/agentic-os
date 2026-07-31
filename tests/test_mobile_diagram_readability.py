"""모바일(<768px) 비전 보드 워크스페이스가 "확대 없이 읽히는지"를 정적으로 검증한다.

헤드리스 브라우저가 없으므로 (1) 렌더된 HTML 구조, (2) CSS 규칙 존재,
(3) 노드 글자 크기·줄바꿈 글자 수가 노드 박스 안에 실제로 들어가는지의 산술을
확인한다. 검증 대상은 세 갈래다:

  * 프로젝트 개요(목표·작업 위치·삭제)가 접히는 드롭다운 안으로 들어가 헤더가
    화면을 먹지 않는다 — 스크린샷에서 워크스페이스가 화면 아래 좁은 띠로
    밀려 있던 원인.
  * 워크플로 탭 패널이 고정 vh 대신 패널 높이를 다 쓴다(이중 스크롤 제거).
  * 노드 글자를 키우고 제목을 두 줄로 흘리되, 각 줄이 NODE_W 안에 들어간다.
"""
import re
from pathlib import Path

from fastapi.testclient import TestClient

from app import config, db
from app.orchestrator import (MOBILE_NODE_H, MOBILE_NODE_W, NODE_H, NODE_W,
                              TITLE_WRAP)

ORCA_CSS = Path("static/orca-theme.css").read_text(encoding="utf-8")
STYLE_CSS = Path("static/style.css").read_text(encoding="utf-8")
BOARD_HTML = Path("templates/partials/board.html").read_text(encoding="utf-8")
EDITOR_JS = Path("static/board-editor.js").read_text(encoding="utf-8")


def _client(tmp_env):
    from app.main import app
    return TestClient(app)


def _font_px(css, selector):
    """selector 블록의 font-size(rem) → px (루트 16px 기준)."""
    block = re.search(re.escape(selector) + r"\s*\{[^}]*\}", css).group()
    return float(re.search(r"font-size:\s*([\d.]+)rem", block).group(1)) * 16


def test_project_header_collapses_into_dropdown(tmp_env):
    with _client(tmp_env) as client:
        r = client.post("/projects", data={"goal": "긴 목표 문장", "title": "제목",
                                          "workdir": ""})
        assert r.status_code == 200
        html = r.text

    # 목표·작업 위치·삭제는 접히는 개요(details) 안에 있다.
    brief = re.search(r'<details class="project-brief".*?</details>', html, re.S)
    assert brief, "project-brief details block missing"
    body = brief.group()
    assert "project-goal-full" in body
    assert "/delete" in body
    # 제목 줄은 summary(탭 타깃) — 접혀 있어도 무엇인지 알 수 있다.
    assert re.search(r'<summary class="project-brief-summary">.*?job-title', body, re.S)
    # 데스크톱은 펼친 채로 시작하고, 모바일에서만 board-workspace.js가 접는다.
    assert 'id="project-brief" open' in body
    ws_js = Path("static/board-workspace.js").read_text(encoding="utf-8")
    assert 'getElementById("project-brief")' in ws_js
    assert "isMobile()) brief.open = false" in ws_js


def test_mobile_workspace_takes_remaining_height():
    mobile = re.search(r"@media \(max-width: 767px\) \{(.*)\n\}", ORCA_CSS, re.S).group(1)
    # 페이지 자체가 스크롤되면(overflow-y: auto) 헤더가 워크스페이스를 아래로 밀어낸다.
    main_rule = re.search(
        r'body\[data-orca-mobile-view="workspace"\] \.orca-project-main \{[^}]*\}',
        mobile).group()
    assert "overflow: hidden" in main_rule
    assert "overflow-y: auto" not in main_rule
    assert "height: 100dvh" in main_rule
    # 펼친 개요는 화면 절반까지만 쓰고 안에서 스크롤한다.
    brief_rule = re.search(r"\.project-brief-body \{[^}]*\}", mobile).group()
    assert "max-height" in brief_rule and "overflow-y: auto" in brief_rule
    # 접힌 상태는 눌릴 만하되(≥32px) 본문을 밀어내지 않는 한 줄이다.
    brief_h = int(re.search(r"min-height:\s*(\d+)px", re.search(
        r"\.project-brief-summary \{[^}]*\}", mobile).group()).group(1))
    assert 32 <= brief_h <= 36
    # 툴바 힌트 문구는 한 줄을 차지하지 않고, 버튼도 캔버스 높이를 덜 먹는다.
    assert "#board .graph-hint { display: none; }" in mobile
    tb_h = int(re.search(r"min-height:\s*(\d+)px", re.search(
        r"#board \.graph-toolbar \.btn-ghost \{[^}]*\}", mobile).group()).group(1))
    assert 30 <= tb_h <= 34
    # 상태 배너도 한 줄 알림 수준의 여백만 쓴다.
    assert re.search(r"#board \.board-banner \{[^}]*padding:\s*0\.3rem", mobile)


def test_workflow_panel_fills_height_instead_of_fixed_vh():
    canvas_rule = re.search(r"#board \.graph-canvas \{[^}]*\}", ORCA_CSS).group()
    assert "flex: 1" in canvas_rule
    assert "max-height: none" in canvas_rule
    # id 선택자라 style.css의 고정 높이(.graph-canvas { height: 56vh })를 이긴다.
    assert "height: auto" in canvas_rule
    assert re.search(
        r'\.orca-tab-panel\[data-tab-kind="workflow"\]\.active \{[^}]*display: flex',
        ORCA_CSS)


def test_narrow_fit_snaps_to_the_canvas_width():
    """모바일 캔버스는 폭이 곧 화면 폭 — 화면 맞춤이 폭에 딱 맞춰야 1:1 이상으로
    보인다(전체를 다 넣으려 세로까지 맞추면 글자가 짜부라진다)."""
    assert re.search(r"vpWidth\(\) < NARROW\) \{\s*vw = w;\s*vh = vw / ar;",
                     EDITOR_JS)
    # 1:1 상한(축소 방지 클램프)은 데스크톱 쪽으로만 걸린다.
    assert re.search(r"vh = vw / ar;\s*\} else if \(box\.width > 0 && vw < box\.width\)",
                     EDITOR_JS)


def test_orientation_breakpoint_matches_the_css_mobile_tier():
    """다이어그램 방향 판정이 CSS 모바일 티어(<768px)와 같은 경계·같은 폭 소스를
    써야 한다. window.innerWidth는 iOS Safari에서 핀치 줌을 따라 커지므로,
    폰에서 화면을 벌리면 CSS는 모바일인데 그래프만 lr로 뒤집혔다(스크린샷 원인)."""
    ws_js = Path("static/board-workspace.js").read_text(encoding="utf-8")
    for js, name in ((EDITOR_JS, "board-editor.js"), (ws_js, "board-workspace.js")):
        assert "document.documentElement.clientWidth" in js, \
            f"{name}: 폭은 레이아웃 뷰포트에서 재야 한다"
        assert not re.search(r"(NARROW|isDesktop|isMobile)[^\n]*window\.innerWidth", js), \
            f"{name}: 브레이크포인트 판정에 핀치 줌을 타는 innerWidth를 쓰면 안 된다"
    assert re.search(r"const NARROW = 768;", EDITOR_JS)
    assert re.search(r"vpWidth\(\) < 768", ws_js)
    # 방향이 어긋난 조각이 들어와도 한 번은 스스로 다시 받아 바로잡는다.
    assert 'c.dataset.orientation !== boardOrientation()' in EDITOR_JS
    assert "orientationRetry" in EDITOR_JS
    # CSS 모바일 티어도 같은 경계여야 한다.
    assert "@media (max-width: 767px)" in ORCA_CSS


def test_node_text_is_larger_and_fits_the_box(tmp_env):
    title_px = _font_px(STYLE_CSS, ".node-title")
    meta_px = _font_px(STYLE_CSS, ".node-meta")
    # 이전 값(0.8rem/0.72rem = 12.8/11.5px)보다 확실히 크다.
    assert title_px >= 15 and meta_px >= 13

    # 제목 줄바꿈 글자 수가 실제로 박스 안에 들어가는지 — 한글 글자폭은 약 1em,
    # 아이콘+공백은 약 2글자, 좌우 여백 12px, 오른쪽 ⚠ 자리 1글자로 잡는다.
    # 데스크톱과 모바일은 박스 폭이 다르므로 각자의 줄 수/글자 수로 검사한다.
    base_y, line_h = (int(m) for m in re.search(
        r'class="node-title" x="12" y="\{\{ (\d+) \+ (\d+) \* loop\.index0 \}\}"',
        BOARD_HTML).groups())
    for orient, w, h in (("lr", NODE_W, NODE_H),
                         ("tb", MOBILE_NODE_W, MOBILE_NODE_H)):
        first, rest, max_lines = TITLE_WRAP[orient]
        assert 12 + (2 + first) * title_px <= w - 12 - title_px, "첫 줄이 ⚠ 자리를 침범"
        assert (rest + 1) * title_px <= w - 12 - 12, "다음 줄(말줄임 포함)이 박스를 넘음"
        # 제목 마지막 줄과 메타(h - 14) baseline이 겹치지 않고 박스 안에 든다.
        last_title = base_y + line_h * (max_lines - 1)
        assert last_title + title_px <= h - 14 <= h - 6

    # 실제 렌더 — 같은 프로젝트가 폭에 따라 다른 줄 수로 나온다.
    conn = db.get_conn(config.DB_PATH)
    pid = db.create_project(conn, "목표")
    long_title = "구현 갭 감사 스펙 대비 실제 코드 대조 보고서와 후속 조치 계획"
    db.create_task(conn, pid, 1, long_title, "설명", "text", "claude")
    with _client(tmp_env) as client:
        desktop = client.get(f"/partials/board/{pid}?o=lr").text
        mobile = client.get(f"/partials/board/{pid}?o=tb").text
    assert desktop.count('class="node-title"') == TITLE_WRAP["lr"][2]
    assert mobile.count('class="node-title"') == TITLE_WRAP["tb"][2]

    def shown(html):
        return "".join(re.findall(r'class="node-title"[^>]*>\s*(.*?)</text>', html))

    # 모바일이 더 많은 글자를 보여준다(잘림이 덜하다).
    assert shown(desktop).endswith("…"), "데스크톱 두 줄에는 다 안 들어간다"
    assert long_title.replace(" ", "") in shown(mobile).replace(" ", ""), \
        "모바일 세 줄에는 이 정도 제목이 통째로 들어가야 한다"


def test_mobile_nodes_stack_in_one_column(tmp_env):
    """세로 스택 + 폰 폭에 맞는 캔버스 — 가로 스크롤도, 확대도 필요 없다."""
    conn = db.get_conn(config.DB_PATH)
    pid = db.create_project(conn, "목표")
    db.update_project(conn, pid, status="plan_ready")   # 편집 가능 = 포트가 그려진다
    for seq in (1, 2, 3):
        db.create_task(conn, pid, seq, f"태스크 {seq}", "설명", "text", "claude",
                       depends_on="1" if seq > 1 else "")
    with _client(tmp_env) as client:
        mobile = client.get(f"/partials/board/{pid}?o=tb").text

    xs = {m for m in re.findall(r'data-x="([\d.]+)"', mobile)}
    assert len(xs) == 1, "모바일은 노드가 한 열로 쌓인다"
    width = int(re.search(r'data-w="(\d+)"', mobile).group(1))
    assert width <= 400, "캔버스 폭이 폰 화면 폭을 넘으면 가로 스크롤이 생긴다"
    # 포트는 손가락으로 집을 수 있어야 한다(데스크톱 r=6보다 크게).
    assert int(re.search(r'class="node-port port-out"[^>]*r="(\d+)"', mobile,
                         re.S).group(1)) >= 10
    # 모바일에서는 노드를 끌어 옮기지 못한다 — 끌기는 화면 이동으로 넘긴다.
    assert 'orientation() !== "tb"' in EDITOR_JS
