"""app/gitcheckpoint.py — 잡 실행 전후 git 체크포인트.

지금까지 승인 없이 워크스페이스에 직접 쓰는 잡(codex는 샌드박스까지 끔)의
유일한 "diff"는 에이전트 출력 평문에서 +/- 로 시작하는 줄에 색만 입히는
것이었다 — 실제 git diff 가 아니었고, 되돌릴 방법도 없었다.

범위는 의도적으로 좁다: 워크스페이스가 실행 시작 시점에 깨끗했을 때만
체크포인트를 잡는다(capture 문서 참고). 여기서는 그 경계 자체를 검증한다 —
dirty-at-start, non-repo, no-workdir 이 전부 "없음"으로 정직하게 보고되는지.
"""
import subprocess

import pytest

from app import gitcheckpoint


def _git(args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True,
                   capture_output=True, text=True)


def _repo(tmp_path, name="repo"):
    d = tmp_path / name
    d.mkdir()
    _git(["init", "-q", "."], d)
    _git(["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q",
         "--allow-empty", "-m", "init"], d)
    return d


# --- capture: 언제 체크포인트를 잡을 수 있는가 ---------------------------

async def test_capture_none_when_no_workdir():
    r = await gitcheckpoint.capture(None)
    assert r == {"available": False, "reason": "no_workdir"}


async def test_capture_none_when_not_a_repo(tmp_path):
    d = tmp_path / "plain"
    d.mkdir()
    r = await gitcheckpoint.capture(str(d))
    assert r == {"available": False, "reason": "not_a_repo"}


async def test_capture_none_when_dirty_at_start(tmp_path):
    """이미 손으로 고치던 파일이 있으면 '이 실행이 바꾼 것'을 가려낼 수 없다."""
    d = _repo(tmp_path)
    (d / "already_editing.txt").write_text("사람이 만든 파일")
    r = await gitcheckpoint.capture(str(d))
    assert r == {"available": False, "reason": "dirty_at_start"}


async def test_capture_available_on_clean_repo(tmp_path):
    d = _repo(tmp_path)
    r = await gitcheckpoint.capture(str(d))
    assert r["available"] is True
    assert r["before_sha"]  # init 커밋의 sha


async def test_capture_handles_repo_with_no_commits(tmp_path):
    d = tmp_path / "fresh"
    d.mkdir()
    _git(["init", "-q", "."], d)
    r = await gitcheckpoint.capture(str(d))
    assert r["available"] is True
    assert r["before_sha"] is None  # HEAD 가 아직 없다


# --- diff_since: 실제로 바뀐 것만 잡는가 ----------------------------------

async def test_diff_since_reports_new_and_modified_files(tmp_path):
    d = _repo(tmp_path)
    (d / "existing.txt").write_text("v1\n")
    _git(["add", "-A"], d)
    _git(["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "add existing"], d)

    checkpoint = await gitcheckpoint.capture(str(d))
    (d / "existing.txt").write_text("v2\n")   # 수정
    (d / "new.txt").write_text("새 파일\n")     # 신규(미추적)

    diff = await gitcheckpoint.diff_since(str(d), checkpoint)
    assert set(diff["files"]) == {"existing.txt", "new.txt"}
    assert "existing.txt" in diff["stat"]
    assert "new.txt" in diff["patch"]
    assert "새 파일" in diff["patch"]


async def test_diff_since_empty_when_nothing_changed(tmp_path):
    d = _repo(tmp_path)
    checkpoint = await gitcheckpoint.capture(str(d))
    diff = await gitcheckpoint.diff_since(str(d), checkpoint)
    assert diff["files"] == []
    assert diff["stat"] == ""


async def test_diff_since_from_first_commit_ever(tmp_path):
    """커밋이 하나도 없던 저장소도 diff 를 낼 수 있어야 한다(빈 트리 기준)."""
    d = tmp_path / "fresh"
    d.mkdir()
    _git(["init", "-q", "."], d)
    checkpoint = await gitcheckpoint.capture(str(d))
    (d / "first.txt").write_text("첫 파일")
    diff = await gitcheckpoint.diff_since(str(d), checkpoint)
    assert diff["files"] == ["first.txt"]


async def test_diff_patch_is_clipped(tmp_path, monkeypatch):
    d = _repo(tmp_path)
    checkpoint = await gitcheckpoint.capture(str(d))
    (d / "big.txt").write_text("x" * 5000 + "\n")
    monkeypatch.setattr(gitcheckpoint, "DIFF_PATCH_CLIP", 200)
    diff = await gitcheckpoint.diff_since(str(d), checkpoint)
    assert len(diff["patch"]) < 300
    assert diff["patch"].endswith("…(생략)")


# --- revert: 실제로 되돌리는가, 그리고 안전한가 ---------------------------

async def test_revert_restores_modified_file_and_removes_new_file(tmp_path):
    d = _repo(tmp_path)
    (d / "existing.txt").write_text("v1\n")
    _git(["add", "-A"], d)
    _git(["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "add"], d)

    checkpoint = await gitcheckpoint.capture(str(d))
    (d / "existing.txt").write_text("에이전트가 고침\n")
    (d / "new_by_agent.txt").write_text("에이전트가 만듦\n")
    # 실제 흐름과 동일하게: worker 가 diff_since 결과를 checkpoint 에 합친 뒤
    # revert 에 넘긴다 — revert 는 그 files 목록만 정확히 건드린다.
    checkpoint = {**checkpoint, **await gitcheckpoint.diff_since(str(d), checkpoint)}

    ok, msg = await gitcheckpoint.revert(str(d), checkpoint)
    assert ok, msg
    assert (d / "existing.txt").read_text() == "v1\n"
    assert not (d / "new_by_agent.txt").exists()


async def test_revert_only_touches_recorded_files_not_unrelated_untracked(tmp_path):
    """워크트리 전체를 겨누지 않는다 — 기록된 파일만 정확히 건드린다."""
    d = _repo(tmp_path)
    checkpoint = await gitcheckpoint.capture(str(d))
    (d / "by_agent.txt").write_text("에이전트가 만듦\n")
    checkpoint = {**checkpoint, **await gitcheckpoint.diff_since(str(d), checkpoint)}
    assert checkpoint["files"] == ["by_agent.txt"]

    # revert 계산 이후, 이 잡과 무관한 미추적 파일이 하나 더 생겼다고 하자
    # (사용자가 직접 만들었거나 다른 프로세스가 만든 것) — 지워지면 안 된다.
    (d / "unrelated.txt").write_text("이 잡과 무관함\n")

    ok, msg = await gitcheckpoint.revert(str(d), checkpoint)
    assert ok, msg
    assert not (d / "by_agent.txt").exists()
    assert (d / "unrelated.txt").exists(), "이 잡이 기록하지 않은 파일까지 지워졌다"


async def test_revert_leaves_repo_alone_when_nothing_to_revert(tmp_path):
    d = _repo(tmp_path)
    checkpoint = await gitcheckpoint.capture(str(d))
    ok, msg = await gitcheckpoint.revert(str(d), checkpoint)
    assert ok is False
    assert "없습니다" in msg


async def test_revert_refuses_when_checkpoint_unavailable(tmp_path):
    d = tmp_path / "plain"
    d.mkdir()
    checkpoint = {"available": False, "reason": "not_a_repo"}
    ok, msg = await gitcheckpoint.revert(str(d), checkpoint)
    assert ok is False


async def test_revert_on_repo_with_no_prior_commits(tmp_path):
    """capture 시점에 커밋이 아예 없던 저장소(빈 초기 커밋 포함) — 파일별
    삭제로 처리해야 한다. `git checkout <sha> -- .` 처럼 워크트리 전체를
    겨누면 대상이 하나도 없을 때 pathspec 오류로 죽는다(2026-08-01 실측)."""
    d = tmp_path / "fresh"
    d.mkdir()
    _git(["init", "-q", "."], d)
    checkpoint = await gitcheckpoint.capture(str(d))
    (d / "oops.txt").write_text("실수로 만듦")
    checkpoint = {**checkpoint, **await gitcheckpoint.diff_since(str(d), checkpoint)}
    ok, msg = await gitcheckpoint.revert(str(d), checkpoint)
    assert ok, msg
    assert not (d / "oops.txt").exists()


async def test_revert_on_repo_with_empty_initial_commit(tmp_path):
    """_repo() 헬퍼가 만드는 --allow-empty 초기 커밋 — before_sha 는 있지만
    그 트리에 파일이 0개다. to_restore 가 비어도 to_delete 삭제는 돼야 한다."""
    d = _repo(tmp_path)   # --allow-empty 커밋 하나만 있는 저장소
    checkpoint = await gitcheckpoint.capture(str(d))
    (d / "created.txt").write_text("새로 만듦")
    checkpoint = {**checkpoint, **await gitcheckpoint.diff_since(str(d), checkpoint)}
    ok, msg = await gitcheckpoint.revert(str(d), checkpoint)
    assert ok, msg
    assert not (d / "created.txt").exists()


# --- summarize: 저장된 JSON 을 읽는 쪽 -------------------------------------

def test_summarize_parses_json():
    assert gitcheckpoint.summarize('{"available": true}') == {"available": True}


def test_summarize_handles_missing_or_broken_json():
    assert gitcheckpoint.summarize(None) is None
    assert gitcheckpoint.summarize("") is None
    assert gitcheckpoint.summarize("{broken") is None
