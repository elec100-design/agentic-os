"""좌측 사이드바(에이전트 DM·채널 목록), DM 채널, 채널 만들기(에이전트 소환)."""
from fastapi.testclient import TestClient

from app import agents, db


def _client(tmp_env):
    from app.main import app
    return TestClient(app)


def _three_agents(conn):
    return [db.create_agent(conn, slug, name, provider="claude") for slug, name in
            (("researcher", "Researcher"), ("builder", "Builder"),
             ("reviewer", "Reviewer"))]


# --- 사이드바 목록 -----------------------------------------------------------

def test_home_sidebar_lists_agents_and_channels(tmp_env, completed_setup):
    with _client(tmp_env) as client:
        conn = db.get_conn()
        _three_agents(conn)
        cid = db.create_channel(conn, "신제품 출시")

        home = client.get("/").text
        for slug in ("researcher", "builder", "reviewer"):
            assert f'href="/dm/{slug}"' in home, slug
        assert f'href="/channels/{cid}"' in home
        assert "신제품 출시" in home


def test_sidebar_is_empty_but_guiding_without_agents(tmp_env, completed_setup):
    """에이전트가 없을 때 빈 목록만 보이면 무엇을 해야 할지 알 수 없다."""
    with _client(tmp_env) as client:
        home = client.get("/").text
        assert "rail-empty" in home
        assert 'href="/settings/agents"' in home and 'href="/channels/new"' in home


def test_preview_falls_back_when_the_last_message_has_no_body(tmp_env, completed_setup):
    """실행 중이거나 실패한 메시지가 마지막이면 본문이 없다 — 그때 빈 줄을
    보여주면 대화가 없는 것처럼 보인다."""
    from app.main import _preview
    conn = db.get_conn()
    cid = db.create_channel(conn, "c")
    assert _preview(conn, cid) == ""

    root = db.create_message(conn, cid, "user", "오늘 할 일 정리해줘", author="user")
    running = db.create_message(conn, cid, "agent", "", author="a",
                                parent_id=root, status="queued")
    assert _preview(conn, cid) in ("실행 중…", "Working…")

    # 실패해서 본문이 영영 없는 경우 → 직전에 내용이 있던 발화로 물러선다
    db.update_message(conn, running, status="failed")
    preview = _preview(conn, cid)
    assert "오늘 할 일 정리해줘" in preview
    assert preview.startswith(("실패", "Failed"))

    # 정상 응답이 오면 그 본문을 보여준다
    db.update_message(conn, running, status="done", body="정리했습니다")
    assert _preview(conn, cid) == "정리했습니다"


def test_preview_clips_long_bodies(tmp_env):
    from app.main import _preview
    conn = db.get_conn()
    cid = db.create_channel(conn, "c")
    db.create_message(conn, cid, "user", "가" * 200, author="user")
    preview = _preview(conn, cid)
    assert len(preview) == 61 and preview.endswith("…")


# --- DM ---------------------------------------------------------------------

def test_dm_link_creates_the_channel_once(tmp_env, completed_setup):
    with _client(tmp_env) as client:
        conn = db.get_conn()
        _three_agents(conn)

        r = client.get("/dm/researcher", follow_redirects=False)
        assert r.status_code == 303
        first = r.headers["location"]

        # 다시 열어도 새 채널이 생기지 않는다
        r = client.get("/dm/researcher", follow_redirects=False)
        assert r.headers["location"] == first
        assert len(db.list_channels(conn, kind="dm")) == 1

        channel = db.list_channels(conn, kind="dm")[0]
        assert channel["kind"] == "dm"
        assert [m["slug"] for m in db.list_channel_members(conn, channel["id"])] \
            == ["researcher"]


def test_dm_page_hides_group_only_controls(tmp_env, completed_setup):
    with _client(tmp_env) as client:
        conn = db.get_conn()
        _three_agents(conn)
        db.update_agent(conn, db.get_agent_by_slug(conn, "researcher")["id"],
                        persona="조사를 담당합니다")
        page = client.get("/dm/researcher", follow_redirects=True).text
        # 상대가 한 명뿐이라 멤버 추가·에이전트 간 대화·에이전트 선택이 없다
        assert "member-add-select" not in page
        assert "agent-chat-toggle" not in page
        assert "new-thread-provider" not in page
        assert "조사를 담당합니다" in page


def test_dm_message_goes_to_that_agent_without_a_mention(tmp_env, completed_setup):
    """상대가 한 명뿐인 방에서 이름을 부르게 하는 건 번거롭기만 하다."""
    with _client(tmp_env) as client:
        conn = db.get_conn()
        ids = _three_agents(conn)
        client.get("/dm/researcher", follow_redirects=False)
        channel = db.list_channels(conn, kind="dm")[0]

        r = client.post(f"/api/channels/{channel['id']}/messages",
                        json={"body": "오늘 할 일 정리해줘"})
        assert r.status_code == 202
        assert r.json()["agent_id"] == ids[0]
        assert db.get_job(conn, r.json()["job_id"])["agent_id"] == ids[0]


def test_dm_still_honors_an_explicit_mention_of_a_member(tmp_env, completed_setup):
    """DM 이라도 그 방의 멤버를 명시적으로 부르면 그대로 간다."""
    with _client(tmp_env) as client:
        conn = db.get_conn()
        ids = _three_agents(conn)
        client.get("/dm/researcher", follow_redirects=False)
        channel = db.list_channels(conn, kind="dm")[0]
        r = client.post(f"/api/channels/{channel['id']}/messages",
                        json={"body": "@researcher 정리해줘"})
        assert r.json()["agent_id"] == ids[0]


def test_dm_for_unknown_agent_is_404(tmp_env, completed_setup):
    with _client(tmp_env) as client:
        assert client.get("/dm/nobody", follow_redirects=False).status_code == 404


def test_archiving_an_agent_takes_its_dm_off_the_list(tmp_env, completed_setup):
    """상대가 없는 DM 은 열어도 말을 걸 수 없다 — 목록에서 내린다(기록은 남는다)."""
    with _client(tmp_env) as client:
        conn = db.get_conn()
        _three_agents(conn)
        client.get("/dm/researcher", follow_redirects=False)
        channel = db.list_channels(conn, kind="dm")[0]

        db.archive_agent(conn, db.get_agent_by_slug(conn, "researcher")["id"])
        assert db.list_channels(conn, kind="dm") == []
        assert db.get_channel(conn, channel["id"]) is not None   # 기록은 보존
        assert 'href="/dm/researcher"' not in client.get("/").text


# --- 채널 만들기(에이전트 소환) -----------------------------------------------

def test_create_channel_summons_the_picked_agents(tmp_env, completed_setup):
    with _client(tmp_env) as client:
        conn = db.get_conn()
        ids = _three_agents(conn)

        page = client.get("/channels/new").text
        assert 'name="agent_ids"' in page and "@researcher" in page

        r = client.post("/channels/new", follow_redirects=False, data={
            "title": "신제품 출시", "topic": "8월 런칭", "workdir": "",
            "agent_ids": [str(ids[0]), str(ids[2])], "agent_chat": "1"})
        assert r.status_code == 303
        channel_id = int(r.headers["location"].rsplit("/", 1)[-1])

        channel = db.get_channel(conn, channel_id)
        assert channel["title"] == "신제품 출시" and channel["kind"] == "channel"
        assert channel["agent_chat"] == 1
        assert {m["slug"] for m in db.list_channel_members(conn, channel_id)} == {
            "researcher", "reviewer"}


def test_create_channel_without_agents_is_allowed(tmp_env, completed_setup):
    """멤버는 채널 화면에서 나중에 더할 수 있다 — 만들기부터 막지 않는다."""
    with _client(tmp_env) as client:
        r = client.post("/channels/new", follow_redirects=False,
                        data={"title": "빈 채널"})
        assert r.status_code == 303
        conn = db.get_conn()
        channel_id = int(r.headers["location"].rsplit("/", 1)[-1])
        assert db.list_channel_members(conn, channel_id) == []


def test_create_channel_rejects_an_empty_title(tmp_env, completed_setup):
    with _client(tmp_env) as client:
        r = client.post("/channels/new", data={"title": "   "})
        assert r.status_code == 400
        assert db.list_channels(db.get_conn(), kind="channel") == []


def test_create_channel_ignores_unknown_agents_and_bad_workdir(tmp_env, completed_setup):
    with _client(tmp_env) as client:
        conn = db.get_conn()
        ids = _three_agents(conn)
        r = client.post("/channels/new", follow_redirects=False, data={
            "title": "c", "workdir": "/etc/definitely-not-registered",
            "agent_ids": [str(ids[0]), "9999"]})
        channel_id = int(r.headers["location"].rsplit("/", 1)[-1])
        assert db.get_channel(conn, channel_id)["workdir"] is None
        assert len(db.list_channel_members(conn, channel_id)) == 1


def test_channel_page_marks_the_open_conversation(tmp_env, completed_setup):
    with _client(tmp_env) as client:
        conn = db.get_conn()
        _three_agents(conn)
        cid = db.create_channel(conn, "팀")
        page = client.get(f"/channels/{cid}").text
        assert "rail-item active" in page
        # 대화 중에도 다른 상대로 옮겨갈 수 있어야 한다
        assert 'href="/dm/builder"' in page


def test_avatar_color_is_stable_per_slug(tmp_env):
    """목록에서 사람을 색으로 알아보므로 같은 슬러그는 늘 같은 색이어야 한다."""
    from app.main import avatar_color
    assert avatar_color("researcher") == avatar_color("researcher")
    assert avatar_color("") and avatar_color(None)


# --- 목록 접기/펼치기 ---------------------------------------------------------

def test_rail_groups_have_a_collapse_toggle(tmp_env, completed_setup):
    """에이전트가 늘어나면 채널이 화면 밖으로 밀린다 — 묶음별로 접을 수 있어야 한다."""
    with _client(tmp_env) as client:
        conn = db.get_conn()
        _three_agents(conn)
        for path in ("/", "/channels/new"):
            page = client.get(path).text
            assert 'data-rail-group="agents"' in page, path
            assert 'data-rail-group="channels"' in page, path
            assert page.count('class="rail-collapse"') == 2, path
            assert page.count('class="rail-group-body"') == 2, path
            assert "agent-rail.js" in page, path


def test_collapsed_body_is_hidden_by_the_browser_not_by_css(tmp_env):
    """`hidden` 으로 접는 요소에 CSS 가 display 를 주면 브라우저 기본
    `[hidden] { display: none }` 을 명시도로 이겨 버려 접히지 않는다
    (승인 배지에서 실제로 겪은 버그다). rail-group-body 는 display 를 받지 않는다."""
    from pathlib import Path
    css = Path("static/style.css").read_text(encoding="utf-8")
    for block in css.split(".rail-group-body")[1:]:
        rule = block.split("{")[1].split("}")[0] if "{" in block else ""
        assert "display" not in rule, f"rail-group-body 에 display 를 주면 안 된다: {rule}"

    js = Path("static/agent-rail.js").read_text(encoding="utf-8")
    assert "body.hidden = collapsed" in js
    assert "localStorage" in js   # 페이지를 옮겨도 접힌 상태가 유지돼야 한다


# --- 중앙 탭으로 열기 ---------------------------------------------------------

def test_channel_view_partial_renders_without_page_chrome(tmp_env, completed_setup):
    """중앙 탭에 얹을 조각 — 사이드바·<html> 껍데기 없이 대화만 나와야 한다."""
    with _client(tmp_env) as client:
        conn = db.get_conn()
        _three_agents(conn)
        cid = db.create_channel(conn, "팀")
        db.add_channel_member(conn, cid, db.get_agent_by_slug(conn, "researcher")["id"])

        r = client.get(f"/partials/channel/{cid}")
        assert r.status_code == 200
        body = r.text
        assert "<html" not in body and 'class="sidebar"' not in body
        assert 'class="channel-view"' in body
        assert f'data-channel-id="{cid}"' in body and 'data-channel-title="팀"' in body
        # 마운트에 필요한 데이터가 조각 안에 실려 있다(전역 상수를 쓰지 않는다)
        assert 'class="channel-data"' in body
        assert "@researcher" in body

        assert client.get("/partials/channel/9999").status_code == 404
        # 탭 복원 시 살아 있는지 확인하는 probe
        assert client.head(f"/partials/channel/{cid}").status_code == 200


def test_rail_items_carry_what_the_tab_opener_needs(tmp_env, completed_setup):
    """홈에서는 링크를 따라가지 않고 중앙 탭을 연다 — 그러려면 채널 id 가 필요하다.
    아직 대화한 적 없는 에이전트는 채널이 없으므로 슬러그로 만들어야 한다."""
    with _client(tmp_env) as client:
        conn = db.get_conn()
        _three_agents(conn)
        cid = db.create_channel(conn, "팀")

        home = client.get("/").text
        assert 'data-dm-slug="researcher"' in home     # DM 은 슬러그로 연다
        assert f'data-channel-id="{cid}"' in home      # 채널은 id 를 바로 들고 있다
        assert 'data-rail-title="팀"' in home
        # 링크(href)도 남아 있어야 한다 — JS 가 없거나 다른 화면이면 그대로 이동한다
        assert 'href="/dm/researcher"' in home


def test_api_dm_returns_channel_id_and_is_idempotent(tmp_env, completed_setup):
    with _client(tmp_env) as client:
        conn = db.get_conn()
        _three_agents(conn)

        r = client.get("/api/dm/researcher")
        assert r.status_code == 200
        first = r.json()["channel_id"]
        assert r.json()["kind"] == "dm"

        assert client.get("/api/dm/researcher").json()["channel_id"] == first
        assert len(db.list_channels(conn, kind="dm")) == 1
        assert client.get("/api/dm/nobody").status_code == 404


def test_home_can_mount_a_channel_tab(tmp_env, completed_setup):
    """홈은 mountChannelView 를 쓸 수 있어야 하고, 로더가 조각을 받아 온다."""
    with _client(tmp_env) as client:
        home = client.get("/").text
        assert "channels.js" in home        # 마운트 함수 제공
        assert "agent-rail.js" in home      # 목록 클릭 → 탭 열기

    from pathlib import Path
    js = Path("static/home.js").read_text(encoding="utf-8")
    assert "/partials/channel/" in js
    assert "mountChannelView" in js
    ch = Path("static/channels.js").read_text(encoding="utf-8")
    # 탭을 여러 개 열어도 섞이지 않게 root 안쪽만 본다
    assert "window.mountChannelView = function" in ch
    assert 'getElementById("thread-list")' not in ch
    # 탭을 닫으면 스트림을 끊어야 한다 — 안 그러면 닫은 대화가 계속 흘러든다
    assert "for (const es of streams) es.close()" in ch


def test_channel_view_handles_the_thread_api_shape(tmp_env, completed_setup):
    """/api/messages/{id}/thread 는 {root_id, messages} 를 준다.

    예전 channels.js 는 응답을 배열로 순회해서 쓰레드 답장 패널이 열리지 않았다
    (이 리팩터링 전부터의 버그). 두 형태를 모두 받는지 확인한다.
    """
    from pathlib import Path
    js = Path("static/channels.js").read_text(encoding="utf-8")
    assert "function threadMessages(payload)" in js
    assert "payload.messages" in js

    with _client(tmp_env) as client:
        conn = db.get_conn()
        cid = db.create_channel(conn, "c")
        root = db.create_message(conn, cid, "user", "질문", author="user")
        payload = client.get(f"/api/messages/{root}/thread").json()
        assert isinstance(payload, dict) and "messages" in payload
