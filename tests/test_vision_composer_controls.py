"""좌측 사이드바 비전보드 채팅의 컴포저 컨트롤 검증.

우측 작업 채팅과 같은 컨트롤(파일 첨부·에이전트·모델·작업 위치·폴더/리포 탐색)을
좌측 비전보드 채팅에도 넣으면서, 한 페이지에 컴포저가 둘이 된다. 그래서
1) 컨트롤 마크업이 실제로 렌더되는지,
2) id가 중복되지 않는지,
3) app.js가 전역 id 대신 폼 단위(class)로 컨트롤을 찾는지
를 확인한다. 브라우저가 없으므로 렌더된 HTML과 JS/CSS 원문으로 검증한다.
"""
import re
from collections import Counter
from pathlib import Path

from fastapi.testclient import TestClient

APP_JS = Path("static/app.js").read_text()
STYLE_CSS = Path("static/style.css").read_text()
WORKSPACES_HTML = Path("templates/partials/workspaces.html").read_text()
CONTROLS_HTML = Path("templates/partials/composer_controls.html").read_text()

# 비전보드 컴포저 안에 있어야 하는 컨트롤 (class로만 — id는 우측 채팅이 갖는다)
VISION_CONTROLS = [
    "composer-provider",   # 에이전트 hidden input
    "composer-model",      # 모델 hidden input
    "composer-files",      # 파일 첨부 input
    "file-chips",          # 첨부 칩
    "tools-btn",           # 첨부·도구 (+)
    "agent-btn",           # 에이전트 선택 칩
    "model-btn",           # 모델 선택 칩
    "workspace-picker",    # 폴더/리포 선택
]


def _client():
    from app.main import app
    return TestClient(app)


def _vision_form(html):
    """좌측 비전보드 컴포저(form#vision-composer)의 HTML만 잘라낸다."""
    start = html.index('id="vision-composer"')
    return html[start:html.index("</form>", start)]


def test_vision_composer_has_the_same_controls_as_the_job_chat(completed_setup):
    with _client() as client:
        form = _vision_form(client.get("/").text)
    for cls in VISION_CONTROLS:
        assert cls in form, f"비전보드 컴포저에 {cls} 컨트롤이 없다"


def test_vision_composer_can_upload_files(completed_setup):
    """파일 첨부가 실제로 전송되려면 multipart 인코딩이어야 한다."""
    with _client() as client:
        html = client.get("/").text
    tag = html[html.index('<form class="composer vision-composer"'):]
    tag = tag[:tag.index(">") + 1]
    assert 'enctype="multipart/form-data"' in tag


def test_home_has_no_duplicate_element_ids(completed_setup):
    """컴포저가 둘이라 id를 그대로 두면 충돌한다 — 좌측은 class만 쓴다."""
    with _client() as client:
        html = client.get("/").text
    dupes = [i for i, n in Counter(re.findall(r'\sid="([^"]+)"', html)).items() if n > 1]
    assert not dupes, f"홈에 중복된 id가 있다: {dupes}"


def test_workspace_picker_partial_is_id_free():
    """작업 위치 조각은 컴포저마다 한 번씩, 즉 페이지에 두 번 실린다."""
    assert not re.search(r'\sid="', WORKSPACES_HTML), "workspaces.html에 id가 남아 있다"
    for cls in ("ws-workdir", "ws-chip", "ws-label", "ws-popup", "ws-add"):
        assert cls in WORKSPACES_HTML


def test_composer_controls_partial_is_shared_by_every_composer():
    """마크업이 복사되지 않고 한 조각에서 나온다.

    홈은 컴포저가 둘(우측 작업 채팅 + 좌측 비전보드 채팅)이라 두 번 include
    되고, id는 우측만 내보낸다.
    """
    include = "partials/composer_controls.html"
    home = Path("templates/index.html").read_text()
    assert home.count(include) == 2
    assert "ids = true" in home and "ids = false" in home


def test_app_js_mounts_composers_per_form_not_by_global_id():
    """전역 getElementById로 잡으면 둘째 컴포저가 죽는다."""
    assert "function mountComposer(form)" in APP_JS
    assert 'document.querySelectorAll("form.composer").forEach(mountComposer)' in APP_JS
    # 컨트롤은 폼 안에서 찾아야 한다
    for cls in ("composer-provider", "composer-files", "agent-btn", "model-btn",
                "tools-btn", "workspace-picker"):
        assert f'form.querySelector(".{cls}")' in APP_JS, f"{cls}를 폼 단위로 찾지 않는다"
    # 예전 전역 조회가 남아 있으면 안 된다
    for stale in ("getElementById(\"provider-input\")", "getElementById(\"file-input\")",
                  "getElementById(\"agent-btn\")", "getElementById(\"ws-workdir\")",
                  "getElementById(\"ws-popup\")", "getElementById(\"workspace-picker\")"):
        assert stale not in APP_JS, f"전역 조회가 남아 있다: {stale}"


def test_each_composer_keeps_its_own_agent_and_workspace_choice():
    """선택 상태(chosenModel·작업 위치)가 mountComposer 안에 있어야 독립적이다."""
    body = APP_JS[APP_JS.index("function mountComposer(form)"):
                  APP_JS.index("function closeAllComposerPopups()")]
    assert "const chosenModel = {}" in body
    assert "let wsSelectedPath" in body


def test_adding_a_workspace_updates_every_picker_but_selects_only_the_opener():
    """목록은 전역이라 모두 갱신하되, 새 위치 자동 선택은 ＋를 누른 컴포저만."""
    assert "for (const c of COMPOSERS)" in APP_JS
    assert "c === wsModalOwner" in APP_JS


def test_hidden_model_chip_is_actually_hidden():
    """.cbar-chip의 display:inline-flex가 [hidden]을 덮어쓰던 버그 방지."""
    assert ".cbar-chip[hidden] { display: none; }" in STYLE_CSS


def test_agent_and_model_popups_are_distinguishable():
    """둘 다 .agent-popup이라 model-popup 클래스로 구분해야 한다."""
    assert 'class="cbar-popup agent-popup model-popup"' in CONTROLS_HTML
    assert '.agent-popup:not(.model-popup)' in APP_JS
