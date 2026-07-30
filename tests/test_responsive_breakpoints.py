"""비전 보드 프로젝트 화면(static/orca-theme.css)의 3단계 브레이크포인트
(>=1024 데스크톱 / 768~1023 태블릿 / <768 모바일)가 6개 대표 뷰포트에서
사이드바·탭바·컴포저·세그먼트 컨트롤을 겹침/잘림/클릭 불가 없이 보여주는지
정적으로 검증한다.

이 저장소에는 헤드리스 브라우저(Playwright 등)가 없어 실제 픽셀 렌더링은
확인할 수 없다. 대신 (1) 렌더된 HTML에 필요한 요소가 모두 존재하는지,
(2) orca-theme.css의 미디어쿼리를 폭 구간별로 파싱해 각 구간에서 어떤
규칙이 "실제로 적용되는지"를 계산해, 스펙(docs/vision-board-ade-spec.md §3)이
요구하는 표시 상태(사이드바 노출 방식/탭바 모양/컴포저 위치/세그먼트 컨트롤
노출 여부)와 일치하는지를 규칙 텍스트 매칭으로 검증한다.
"""
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REQUIRED_ELEMENTS = [
    "orca-project-shell",
    "orca-chat-rail",
    "orca-rail-resize-handle",
    "orca-rail-mobile-toggle",
    "orca-rail-scrim",
    "orca-project-main",
    "orca-workspace",
    "orca-ws-header",
    "orca-ws-back",
    "orca-tabbar",
    "orca-tabbar-scroll",
    "orca-rail-composer-bar",
    "orca-rail-send",
    "orca-mobile-segctl",
]

VIEWPORTS = [
    (1440, 900, "desktop"),
    (1024, 768, "desktop"),
    (820, 1180, "tablet"),
    (768, 1024, "tablet"),
    (390, 844, "mobile"),
    (360, 740, "mobile"),
]


def _client(tmp_env):
    from app.main import app
    return TestClient(app)


def _media_blocks(css_text):
    """@media 규칙별로 (조건 문자열, 본문) 목록을 얕은 중괄호 매칭으로 추출."""
    blocks = []
    for m in re.finditer(r"@media\s*([^{]+)\{", css_text):
        cond = m.group(1).strip()
        start = m.end()
        depth = 1
        i = start
        while depth and i < len(css_text):
            if css_text[i] == "{":
                depth += 1
            elif css_text[i] == "}":
                depth -= 1
            i += 1
        blocks.append((cond, css_text[start:i - 1]))
    return blocks


def _blocks_matching(blocks, width):
    """given a viewport width, return concatenated body of every @media block
    whose min/max-width condition matches (only min-width/max-width conditions
    are evaluated; prefers-reduced-motion 등 다른 조건은 항상 매칭 대상에서 제외)."""
    matched = []
    for cond, body in blocks:
        if "prefers-reduced-motion" in cond:
            continue
        mins = re.findall(r"min-width:\s*(\d+)px", cond)
        maxs = re.findall(r"max-width:\s*(\d+)px", cond)
        if any(width < int(v) for v in mins):
            continue
        if any(width > int(v) for v in maxs):
            continue
        matched.append(body)
    return "\n".join(matched)


@pytest.fixture(scope="module")
def css_blocks():
    css = Path("static/orca-theme.css").read_text(encoding="utf-8")
    return _media_blocks(css)


def test_project_page_has_required_layout_elements(tmp_env):
    with _client(tmp_env) as client:
        r = client.post("/projects", data={"goal": "test goal", "title": "test"})
        assert r.status_code == 200
        for needle in REQUIRED_ELEMENTS:
            assert needle in r.text, f"missing {needle} in rendered HTML"


@pytest.mark.parametrize("w,h,tier", VIEWPORTS)
def test_breakpoint_tier_matches_spec(css_blocks, w, h, tier):
    body = _blocks_matching(css_blocks, w)

    if tier == "desktop":
        # 사이드바+메인 동시 표시(grid 셸), 리사이즈 핸들 활성, 탭바 가로 배치
        assert "grid-template-columns: max-content 1fr" in body, \
            "desktop shell must be a two-column grid"
        assert ".orca-rail-resize-handle { display: none; }" not in body, \
            "resize handle must stay active on desktop"
        assert "--orca-segctl-h" not in body, \
            "desktop must not pick up the mobile segment-control block"
        assert not re.search(r"\.orca-ws-header\s*\{[^}]*display:\s*flex", body), \
            "desktop must not show the mobile back-header"

    elif tier == "tablet":
        # 사이드바는 오프캔버스(오버레이) + 스크림, 리사이즈 핸들 비활성, 메인 전체 폭
        assert "transform: translateX(-100%)" in body, \
            "tablet sidebar must be off-canvas by default"
        assert ".orca-rail-resize-handle { display: none; }" in body, \
            "resize handle must be disabled on tablet"
        assert "display: flex" in re.search(
            r"\.orca-rail-mobile-toggle\s*\{[^}]*\}", body).group(), \
            "tablet must show the overlay toggle entry point"
        assert re.search(r"\.orca-rail-scrim\s*\{[^}]*position:\s*fixed", body), \
            "tablet must enable the scrim overlay"
        assert ".orca-project-shell { display: block; }" in body, \
            "tablet main must take full width (stacked shell)"
        assert "--orca-segctl-h" not in body, \
            "tablet must not pick up the mobile segment-control block"

    else:  # mobile
        assert "--orca-segctl-h" in body, \
            "mobile must define the segment-control height variable"
        assert re.search(r"\.orca-chat-rail\s*\{[^}]*position:\s*fixed", body), \
            "mobile chat rail must be a fullscreen layer"
        assert "transform: translateX(-100%)" not in body, \
            "mobile must not reuse the tablet off-canvas transform"
        assert (".orca-mobile-segctl {\n    display: flex" in body
                or re.search(r"\.orca-mobile-segctl\s*\{\s*display:\s*flex", body)), \
            "mobile must show the bottom segmented control"
        assert re.search(r"\.orca-ws-header\s*\{[^}]*display:\s*flex", body), \
            "mobile must show the workspace back-header"
        assert (".orca-rail-mobile-toggle,\n" not in body
                and "display: none" in re.search(
                    r"\.orca-rail-collapse-btn\s*\{[^}]*\}", body).group()), \
            "mobile must hide the desktop collapse button without leftover " \
            "!important hacks on the tablet toggle/scrim"
        assert "!important" not in body, \
            "mobile block must not rely on !important overrides " \
            "(breakpoints should not overlap)"
        assert ".orca-rail-send { width: 44px; height: 44px; }" in body, \
            "mobile composer send button must meet the 44px touch target"


def test_breakpoint_boundaries_do_not_overlap(css_blocks):
    # 3단계 구간이 서로 겹치지 않는지 — 768/1023 경계에서 태블릿·모바일 규칙이
    # 동시에 활성화되지 않아야 한다(겹치면 두 규칙이 뒤섞여 예측 불가능한 렌더가 됨).
    at_768 = _blocks_matching(css_blocks, 768)
    assert "--orca-segctl-h" not in at_768, "768px must resolve to tablet only, not mobile"
    at_767 = _blocks_matching(css_blocks, 767)
    assert "transform: translateX(-100%)" not in at_767, \
        "767px must resolve to mobile only, not tablet"
    at_1023 = _blocks_matching(css_blocks, 1023)
    assert "grid-template-columns: max-content 1fr" not in at_1023, \
        "1023px must resolve to tablet only, not desktop"
    at_1024 = _blocks_matching(css_blocks, 1024)
    assert "transform: translateX(-100%)" not in at_1024, \
        "1024px must resolve to desktop only, not tablet"
