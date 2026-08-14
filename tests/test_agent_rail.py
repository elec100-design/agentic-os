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
        assert 'id="member-add-select"' not in page
        assert 'id="agent-chat-toggle"' not in page
        assert 'id="new-thread-provider"' not in page
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
