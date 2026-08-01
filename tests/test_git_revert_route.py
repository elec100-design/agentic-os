"""POST /jobs/{id}/git-revert 와 작업 뷰의 diff·되돌리기 UI.

git 체크포인트 계산 자체는 tests/test_gitcheckpoint.py, worker 연동은
tests/test_worker.py 가 검증한다. 여기서는 API 계약과 템플릿 렌더만 본다.
"""
import json
import subprocess

from fastapi.testclient import TestClient

from app import config, db


def _client():
    from app.main import app
    return TestClient(app)


def _git(args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True,
                   capture_output=True, text=True)


def _repo_with_change(tmp_path):
    """capture 시점 커밋 + 그 뒤 신규 파일 하나 — diff_since 가 잡아낼 상태."""
    d = tmp_path / "repo"
    d.mkdir()
    _git(["init", "-q", "."], d)
    _git(["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q",
         "--allow-empty", "-m", "init"], d)
    before_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=d, capture_output=True, text=True
    ).stdout.strip()
    (d / "created.txt").write_text("에이전트가 만듦\n")
    return d, before_sha


def _make_job(conn, workdir, git_run):
    job_id = db.create_job(conn, prompt="테스트", provider="claude", workdir=str(workdir))
    db.update_job(conn, job_id, status="done", output="끝", git_run=json.dumps(git_run))
    return job_id


def test_job_view_shows_diff_section_with_file_count(tmp_env, tmp_path):
    d, before_sha = _repo_with_change(tmp_path)
    conn = db.get_conn(config.DB_PATH)
    job_id = _make_job(conn, d, {
        "available": True, "before_sha": before_sha,
        "stat": " created.txt | 1 +", "files": ["created.txt"], "patch": "+에이전트가 만듦",
    })
    with _client() as client:
        html = client.get(f"/partials/job/{job_id}").text
    assert "git-diff" in html
    assert "Changed files" in html  # 기본 언어는 영어
    assert "created.txt" in html
    assert '<button type="button" class="btn-ghost git-revert-btn"' in html


def test_job_view_hides_diff_section_when_no_files_changed(tmp_env, tmp_path):
    d, before_sha = _repo_with_change(tmp_path)
    conn = db.get_conn(config.DB_PATH)
    job_id = _make_job(conn, d, {"available": True, "before_sha": before_sha,
                                 "stat": "", "files": [], "patch": ""})
    with _client() as client:
        html = client.get(f"/partials/job/{job_id}").text
    assert 'class="git-diff"' not in html


def test_job_view_hides_diff_section_when_checkpoint_unavailable(tmp_env, tmp_path):
    conn = db.get_conn(config.DB_PATH)
    job_id = _make_job(conn, tmp_path, {"available": False, "reason": "dirty_at_start"})
    with _client() as client:
        html = client.get(f"/partials/job/{job_id}").text
    assert 'class="git-diff"' not in html


def test_job_view_shows_reverted_badge_instead_of_button(tmp_env, tmp_path):
    d, before_sha = _repo_with_change(tmp_path)
    conn = db.get_conn(config.DB_PATH)
    job_id = _make_job(conn, d, {
        "available": True, "before_sha": before_sha, "stat": "x", "files": ["created.txt"],
        "patch": "", "reverted_at": "2026-08-01T00:00:00Z",
    })
    with _client() as client:
        html = client.get(f"/partials/job/{job_id}").text
    assert "Reverted" in html  # 기본 언어는 영어
    assert "git-revert-btn" not in html


def test_revert_route_actually_reverts_and_marks_job(tmp_env, tmp_path):
    d, before_sha = _repo_with_change(tmp_path)
    conn = db.get_conn(config.DB_PATH)
    job_id = _make_job(conn, d, {"available": True, "before_sha": before_sha,
                                 "stat": "x", "files": ["created.txt"], "patch": ""})
    assert (d / "created.txt").exists()

    with _client() as client:
        r = client.post(f"/jobs/{job_id}/git-revert")
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert not (d / "created.txt").exists()

    git_run = json.loads(db.get_job(conn, job_id)["git_run"])
    assert git_run["reverted_at"]


def test_revert_route_404_for_missing_job(tmp_env):
    with _client() as client:
        assert client.post("/jobs/999999/git-revert").status_code == 404


def test_revert_route_400_when_checkpoint_unavailable(tmp_env, tmp_path):
    conn = db.get_conn(config.DB_PATH)
    job_id = _make_job(conn, tmp_path, {"available": False, "reason": "not_a_repo"})
    with _client() as client:
        r = client.post(f"/jobs/{job_id}/git-revert")
    assert r.status_code == 400


def test_revert_route_400_when_already_reverted(tmp_env, tmp_path):
    d, before_sha = _repo_with_change(tmp_path)
    conn = db.get_conn(config.DB_PATH)
    job_id = _make_job(conn, d, {
        "available": True, "before_sha": before_sha, "files": ["created.txt"],
        "stat": "x", "patch": "", "reverted_at": "2026-08-01T00:00:00Z",
    })
    with _client() as client:
        r = client.post(f"/jobs/{job_id}/git-revert")
    assert r.status_code == 400
    assert "이미" in r.json()["detail"]


def test_job_view_js_wires_revert_button():
    js = open("static/job-view.js", encoding="utf-8").read()
    assert "wireGitRevert" in js
    assert "/git-revert" in js
    assert "confirm(" in js  # 되돌리기는 확인 없이 자동 실행되면 안 된다


# --- 적용된 지침 표시 (app/instructions.py 연동) --------------------------

def test_job_view_shows_applied_instructions_badge(tmp_env, tmp_path):
    conn = db.get_conn(config.DB_PATH)
    job_id = db.create_job(conn, prompt="테스트", provider="claude", workdir=str(tmp_path))
    db.update_job(conn, job_id, status="done", output="끝",
                  instructions_applied=json.dumps(["AGENTS.md", ".agentic-os.md"]))
    with _client() as client:
        html = client.get(f"/partials/job/{job_id}").text
    assert "Instructions applied" in html  # 기본 언어는 영어
    assert "<code>AGENTS.md</code>" in html
    assert "<code>.agentic-os.md</code>" in html


def test_job_view_hides_instructions_badge_when_nothing_applied(tmp_env, tmp_path):
    conn = db.get_conn(config.DB_PATH)
    job_id = db.create_job(conn, prompt="테스트", provider="claude")
    db.update_job(conn, job_id, status="done", output="끝")
    with _client() as client:
        html = client.get(f"/partials/job/{job_id}").text
    assert "instructions-applied" not in html
