"""claude stream-json → 실행 타임라인.

지금까지 실행 로그는 CLI 원문 텍스트뿐이라 어떤 도구를 썼는지, 무엇이 거부됐는지
보이지 않았다. execution_steps 테이블과 프론트 아이콘(💭🔧📋)은 이미 있었지만
채워 주는 코드가 없어 죽은 코드였다.

픽스처는 손으로 쓴 JSON 이 아니라 실제 `claude -p --output-format stream-json`
출력을 받아 서명·사용량만 덜어낸 것이다(tests/fixtures/claude_stream_json.jsonl).
그 실행은 파일 편집에는 성공하고 Bash 는 거부당한 상태라, 조용한 실패가
타임라인에 드러나는지까지 한 픽스처로 확인할 수 있다.
"""
from pathlib import Path

from app import config, db
from app.providers import ClaudeProvider

STREAM = Path(__file__).parent / "fixtures" / "claude_stream_json.jsonl"


def _events(raw=None):
    p = ClaudeProvider()
    out = []
    for line in (raw or STREAM.read_text()).splitlines():
        out.extend(p.iter_events(line))
    return out


# --- 명령 ---------------------------------------------------------------

def test_claude_streams_events_flag_is_declared():
    """worker 는 이 플래그로 JSONL 파싱 여부를 정한다."""
    assert ClaudeProvider.streams_events is True


# --- 이벤트 → 스텝 ------------------------------------------------------

def test_tool_calls_become_steps_with_readable_titles():
    calls = [e for e in _events() if e.kind == "tool_call"]
    titles = [e.title for e in calls]
    assert any(t.startswith("Read: ") and t.endswith("calc.py") for t in titles)
    assert any(t.startswith("Edit: ") for t in titles)
    # Bash 는 명령줄이 그대로 제목에 들어가야 무엇을 돌렸는지 알 수 있다
    assert any("python3 -c" in t for t in titles if t.startswith("Bash: "))


def test_tool_results_are_recorded_and_errors_flagged():
    results = [e for e in _events() if e.kind == "tool_result"]
    assert results, "도구 결과가 하나도 기록되지 않았다"
    # 이 픽스처의 Bash 는 승인 거부라 결과가 오류로 온다
    assert any("approval" in (e.detail or "") for e in results)


def test_permission_denial_becomes_an_error_step():
    """exit 0 + subtype success 로 묻히던 승인 거부를 타임라인에 남긴다."""
    errs = [e for e in _events() if e.kind == "error"]
    assert len(errs) == 1
    assert "권한 거부" in errs[0].title and "Bash" in errs[0].title
    assert errs[0].display.startswith("⚠")


def test_assistant_text_is_shown_but_thinking_is_not_echoed():
    """생각은 스텝으로 남기되 실행 로그 본문에는 흘리지 않는다."""
    for e in _events():
        if e.kind == "thought":
            assert e.display == ""
        if e.kind == "text":
            assert e.display, "최종 응답은 실행 로그에도 보여야 한다"


def test_non_json_and_malformed_lines_are_ignored():
    p = ClaudeProvider()
    for junk in ("", "   ", "not json", "{broken", "[1,2]", "null"):
        assert p.iter_events(junk) == []


def test_long_tool_detail_is_clipped():
    """도구 결과가 수 MB 여도 스텝 하나가 DB 를 채우지 않게 자른다."""
    big = "x" * 50000
    line = ('{"type":"user","message":{"role":"user","content":'
            '[{"type":"tool_result","tool_use_id":"t1","content":"%s"}]}}' % big)
    ev = ClaudeProvider().iter_events(line)
    assert len(ev) == 1
    assert len(ev[0].detail) < 3000 and ev[0].detail.endswith("(생략)")


# --- 최종 결과 파싱 (stream-json 으로 바꿔도 깨지면 안 된다) ----------------

def test_parse_output_extracts_final_answer_and_session_id():
    r = ClaudeProvider().parse_output(STREAM.read_text(), "", 0)
    assert r.session_id == "c19fbf7b-5c8b-464c-9a4b-7cf548fd8e47"
    assert "multiply" in r.text
    # 원문 JSONL 이 그대로 새어 나오면 안 된다
    assert '"type"' not in r.text


def test_parse_output_still_handles_single_json_object():
    """--output-format json 형식(기존 동작)도 계속 읽힌다."""
    r = ClaudeProvider().parse_output(
        '{"result": "답변입니다", "session_id": "sess-9"}', "", 0)
    assert r.text == "답변입니다"
    assert r.session_id == "sess-9"


def test_parse_output_falls_back_to_plain_text():
    r = ClaudeProvider().parse_output("plain text", "", 0)
    assert r.text == "plain text"
    assert r.session_id is None


# --- 스키마: 채널 메시지 없는 잡도 스텝을 가질 수 있어야 한다 ----------------

def test_steps_can_be_recorded_for_a_plain_job(tmp_path):
    """전에는 message_id NOT NULL 이라 홈에서 보낸 잡은 스텝을 못 남겼다."""
    conn = db.get_conn(tmp_path / "t.db")
    job_id = db.create_job(conn, prompt="p", provider="claude")
    db.create_execution_step(conn, None, kind="tool_call", title="Read: a.py",
                             job_id=job_id)
    db.create_execution_step(conn, None, kind="tool_result", title="결과",
                             job_id=job_id)
    steps = db.list_job_steps(conn, job_id)
    assert [s["kind"] for s in steps] == ["tool_call", "tool_result"]
    assert [s["seq"] for s in steps] == [1, 2]


def test_plain_job_steps_do_not_mix_between_jobs(tmp_path):
    conn = db.get_conn(tmp_path / "t.db")
    a = db.create_job(conn, prompt="a", provider="claude")
    b = db.create_job(conn, prompt="b", provider="claude")
    db.create_execution_step(conn, None, kind="tool_call", title="A", job_id=a)
    db.create_execution_step(conn, None, kind="tool_call", title="B", job_id=b)
    assert [s["title"] for s in db.list_job_steps(conn, a)] == ["A"]
    # seq 는 잡마다 1부터 — 다른 잡의 스텝 수에 영향받지 않는다
    assert db.list_job_steps(conn, b)[0]["seq"] == 1


def test_migration_relaxes_message_id_not_null(tmp_path):
    """구버전 DB(execution_steps.message_id NOT NULL)도 열리면 완화된다."""
    import sqlite3
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    # 나머지 테이블은 get_conn 의 SCHEMA 가 만들게 두고, 낡은 정의의
    # execution_steps 만 미리 심는다(FK 대상 테이블은 아직 없어도 된다 —
    # SQLite 는 기본적으로 FK 를 강제하지 않는다).
    old.executescript("""
        CREATE TABLE execution_steps (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          message_id INTEGER NOT NULL REFERENCES messages(id),
          job_id INTEGER, seq INTEGER NOT NULL, kind TEXT NOT NULL,
          title TEXT NOT NULL DEFAULT '', detail TEXT NOT NULL DEFAULT '',
          status TEXT NOT NULL DEFAULT 'done', created_at TEXT NOT NULL,
          finished_at TEXT);
        INSERT INTO execution_steps (message_id, seq, kind, title, created_at)
          VALUES (1, 1, 'output_chunk', '실행 로그', '2026-01-01T00:00:00Z');
    """)
    old.commit()
    old.close()

    conn = db.get_conn(path)
    meta = {r["name"]: r for r in conn.execute("PRAGMA table_info(execution_steps)")}
    assert not meta["message_id"]["notnull"], "NOT NULL 이 풀리지 않았다"
    # 기존 행은 보존된다
    assert db.list_execution_steps(conn, 1)[0]["title"] == "실행 로그"
    # 그리고 이제 message_id 없이 넣을 수 있다
    db.create_execution_step(conn, None, kind="tool_call", title="새 스텝", job_id=7)
    assert db.list_job_steps(conn, 7)[0]["title"] == "새 스텝"


def test_run_timeline_renders_in_job_view(tmp_env):
    """작업 뷰에 타임라인이 실제로 나온다 — 권한 거부는 눈에 띄게."""
    from fastapi.testclient import TestClient
    from app.main import app
    conn = db.get_conn(config.DB_PATH)
    job_id = db.create_job(conn, prompt="테스트", provider="claude")
    db.update_job(conn, job_id, status="done", output="끝")
    db.create_execution_step(conn, None, kind="tool_call",
                             title="Bash: pytest -q", job_id=job_id)
    db.create_execution_step(conn, None, kind="error",
                             title="권한 거부: Bash", job_id=job_id)
    with TestClient(app) as client:
        html = client.get(f"/partials/job/{job_id}").text
    assert "run-timeline" in html
    assert "Bash: pytest -q" in html
    assert "권한 거부: Bash" in html
    # 거부가 있으면 접지 않고 펼쳐서 보여 준다
    assert "<details class=\"run-timeline\" open>" in html


def test_steps_migration_is_safe_under_concurrent_connections(tmp_path):
    """get_conn 은 요청마다 불리고 WAL 이라 커넥션이 여러 개다. 테이블을 다시
    만드는 동안 다른 커넥션이 들어와도 "no such table: execution_steps" 로 죽으면
    안 된다 — 브라우저에서 실제로 이 오류를 맞고 트랜잭션으로 감쌌다.

    다른 컬럼 추가 마이그레이션은 미리 끝내 두고, 이번에 손댄 부분만 겨눈다.
    """
    import sqlite3
    import threading

    path = tmp_path / "race.db"
    db.get_conn(path).close()          # 나머지 마이그레이션을 먼저 끝낸다
    # execution_steps 만 구버전(NOT NULL) 정의로 되돌린다
    raw = sqlite3.connect(path)
    raw.executescript("""
        DROP TABLE execution_steps;
        CREATE TABLE execution_steps (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          message_id INTEGER NOT NULL,
          job_id INTEGER, seq INTEGER NOT NULL, kind TEXT NOT NULL,
          title TEXT NOT NULL DEFAULT '', detail TEXT NOT NULL DEFAULT '',
          status TEXT NOT NULL DEFAULT 'done', created_at TEXT NOT NULL,
          finished_at TEXT);
        INSERT INTO execution_steps (message_id, seq, kind, title, created_at)
          VALUES (1, 1, 'output_chunk', '보존될 행', '2026-01-01T00:00:00Z');
    """)
    raw.commit()
    raw.close()

    errors = []
    barrier = threading.Barrier(8)

    def open_conn():
        barrier.wait()                 # 최대한 동시에 들이받게 한다
        try:
            db.get_conn(path).close()
        except Exception as e:         # noqa: BLE001 — 무엇이 터지든 실패로 본다
            errors.append(e)

    threads = [threading.Thread(target=open_conn) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"동시 접속 중 마이그레이션이 깨졌다: {errors[:2]}"
    conn = db.get_conn(path)
    meta = {r["name"]: r for r in conn.execute("PRAGMA table_info(execution_steps)")}
    assert not meta["message_id"]["notnull"]
    assert db.list_execution_steps(conn, 1)[0]["title"] == "보존될 행"
    names = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "execution_steps_new" not in names, "중간 테이블이 남았다"
