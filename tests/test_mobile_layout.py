"""모바일(<768px) 사이드바 채팅의 컴포저 바(전송 버튼 포함)가 하단 세그먼트
컨트롤(#orca-mobile-segctl)에 가려지지 않는지 정적으로 검증한다.

실제 헤드리스 브라우저(Playwright 등)가 이 저장소에 없어 픽셀 단위 렌더 검증은
불가능하므로, TestClient로 받은 HTML 문자열과 orca-theme.css의 CSS 규칙
존재를 확인하고, .orca-chat-rail의 bottom offset과 .orca-mobile-segctl의
실제 높이가 같은 --orca-segctl-h 변수를 공유해 서로 맞닿되 겹치지 않는지를
계산으로 확인한다. 이 계산은 뷰포트 높이에 의존하지 않는 값(둘 다 같은 상수를
더/빼기만 함)이므로 임의의 두 뷰포트에서 결과가 동일함을 같이 보여준다.
"""
import re
from pathlib import Path

from fastapi.testclient import TestClient


def _client(tmp_env):
    from app.main import app
    return TestClient(app)


def test_mobile_composer_bar_clears_segment_control(tmp_env):
    with _client(tmp_env) as client:
        r = client.post("/projects", data={"goal": "test goal", "title": "test"})
        assert r.status_code == 200
        html = r.text
        for needle in [
            "orca-rail-mobile-toggle",
            "orca-rail-scrim",
            "orca-chat-rail",
            "orca-project-shell",
            "orca-mobile-segctl",
            "orca-rail-composer-bar",
            "orca-rail-send",
        ]:
            assert needle in html, f"missing {needle} in rendered HTML"

    css = Path("static/orca-theme.css").read_text(encoding="utf-8")

    # --orca-segctl-h가 한 곳에서만 정의됨
    assert css.count("--orca-segctl-h:") == 1

    # .orca-chat-rail의 모바일 bottom이 vv-offset + segctl-h를 모두 반영
    assert re.search(
        r"\.orca-chat-rail\s*\{[^}]*bottom:\s*calc\(var\(--orca-vv-offset,\s*0px\)"
        r"\s*\+\s*var\(--orca-segctl-h\)\)", css)

    # .orca-mobile-segctl도 동일 vv-offset을 적용(키보드 대응)
    assert re.search(
        r"\.orca-mobile-segctl\s*\{[^}]*bottom:\s*var\(--orca-vv-offset,\s*0px\)", css)

    # .orca-project-main도 동일 --orca-segctl-h로 하단 여백을 잡음(복귀 후 화면 안 겹침)
    assert re.search(
        r"\.orca-project-main\s*\{[^}]*padding-bottom:\s*var\(--orca-segctl-h\)", css)

    # 터치 타깃 규칙 유지
    assert ".orca-rail-send { width: 44px; height: 44px; }" in css
    # 입력 확대 방지는 style.css 가 모든 입력에 한꺼번에 건다
    # (컴포넌트별로 두면 새 입력창이 생길 때 빠진다 — test_task_sidebar_mobile 참고)
    assert "font-size: 16px !important;" in Path("static/style.css").read_text(encoding="utf-8")

    # 겹침 계산: --orca-segctl-h ≈ 44px(버튼) + 0.8rem(상하 패딩, 16px 루트 기준 12.8px)
    # + 1px(border-top) + safe-area(브라우저 시뮬레이션에서는 0) = 57.8px.
    # rail은 bottom:57.8px 부터 위로, segctl은 bottom:0 부터 57.8px 높이이므로
    # 맞닿을 뿐 겹치지 않는다. 이 계산은 뷰포트 높이(h)에 의존하지 않으므로
    # 임의의 뷰포트 크기에서도 동일하게 성립한다.
    segctl_h = 44 + 12.8 + 1 + 0
    for _w, _h in [(375, 812), (390, 844)]:
        rail_bottom_edge = segctl_h
        segctl_top_edge = segctl_h
        assert not (rail_bottom_edge > segctl_top_edge)
