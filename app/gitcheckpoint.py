"""잡 실행 전후 git 체크포인트 — 실제 diff 와 되돌리기.

agentic-os 는 codex 를 --dangerously-bypass-approvals-and-sandbox 로, claude 를
acceptEdits 로 돌려 승인 없이 워크스페이스에 직접 쓴다. 그런데 무엇이 바뀌었는지
볼 수도, 되돌릴 수도 없었다 — 유일한 "diff"(board-workspace.js)는 에이전트가
출력한 평문에서 +/- 로 시작하는 줄에 색만 입히는 것이지 git diff 가 아니었다.

범위를 의도적으로 좁힌다: 워크스페이스가 실행 시작 시점에 **깨끗했을 때만**
체크포인트를 잡는다. 이미 손으로 고치던 파일이 있는 상태(dirty)에서 시작하면
"이 실행이 바꾼 것"과 "원래 사용자가 고치던 것"을 구분할 방법이 없다.
stash-and-pop 으로 억지로 분리하는 방법도 있지만, 그러면 실행 중 워킹트리를
두 번 건드리게 되어 동시에 같은 워크스페이스에서 다른 잡이 돌 때 경쟁이
생긴다. 깨끗한 시작만 지원하는 편이 정직하고 안전하다 — 나머지는
"체크포인트 없음"으로 표시한다.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

GIT_TIMEOUT_SEC = 20
# 화면·DB 에 남기는 diff 본문 상한 — 대용량 바이너리·자동생성 파일이 껴도
# 잡 하나가 DB 를 무제한으로 채우지 않게 한다.
DIFF_PATCH_CLIP = 20000


async def _git(args, cwd):
    """git 명령 하나를 돌리고 (returncode, stdout, stderr) 를 돌려준다."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", *args, cwd=cwd,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        out, err = await asyncio.wait_for(proc.communicate(), timeout=GIT_TIMEOUT_SEC)
        return proc.returncode, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")
    except (FileNotFoundError, OSError, asyncio.TimeoutError):
        return 1, "", "git unavailable or timed out"


async def capture(workdir):
    """잡 실행 직전에 부른다.

    돌려주는 dict 는 그대로 jobs.git_run 에 JSON 으로 저장된다. available 이
    false 면 diff_since/revert 를 부르지 않는다 — 이유(reason)를 화면에 그대로
    보여 줄 수 있게 남겨 둔다.
    """
    if not workdir:
        return {"available": False, "reason": "no_workdir"}

    rc, _, _ = await _git(["rev-parse", "--is-inside-work-tree"], workdir)
    if rc != 0:
        return {"available": False, "reason": "not_a_repo"}

    rc, status, _ = await _git(["status", "--porcelain"], workdir)
    if rc != 0:
        return {"available": False, "reason": "git_error"}
    if status.strip():
        return {"available": False, "reason": "dirty_at_start"}

    rc, sha, _ = await _git(["rev-parse", "HEAD"], workdir)
    before_sha = sha.strip() if rc == 0 else None  # 커밋이 아예 없는 새 repo 는 None
    return {"available": True, "before_sha": before_sha}


async def diff_since(workdir, checkpoint):
    """잡이 끝난 뒤 부른다. checkpoint 는 capture() 가 돌려준 dict.

    checkpoint["available"] 가 false 면 호출하지 않는다(worker 가 걸러 준다).
    """
    before_sha = checkpoint.get("before_sha")
    # before_sha 가 None 이면(첫 커밋 이전) 워킹트리 전체를 빈 트리와 비교한다.
    base = before_sha or "4b825dc642cb6eb9a060e54bf8d69288fbee4904"  # git 의 빈 트리 상수

    # git diff 는 미추적(신규) 파일을 보여주지 않는다 — intent-to-add 로 경로만
    # 잠깐 인덱스에 올려(내용은 스테이지하지 않음) 새 파일도 diff 에 잡히게
    # 한다. 되돌리기는 git clean 으로 새 파일을 지우므로, 계산이 끝나면 다시
    # unstage 해 원래의 '미추적' 상태로 되돌린다.
    _, status, _ = await _git(["status", "--porcelain"], workdir)
    untracked = [line[3:] for line in status.splitlines() if line.startswith("?? ")]
    if untracked:
        await _git(["add", "-N", "--", *untracked], workdir)
    try:
        rc, stat, _ = await _git(["diff", "--stat", base], workdir)
        if rc != 0:
            return {"error": "diff_failed"}
        rc, names, _ = await _git(["diff", "--name-only", base], workdir)
        files = [f for f in names.splitlines() if f.strip()]

        patch = ""
        if files:
            rc, patch, _ = await _git(["diff", base], workdir)
            if len(patch) > DIFF_PATCH_CLIP:
                patch = patch[:DIFF_PATCH_CLIP] + "\n…(생략)"
    finally:
        if untracked:
            await _git(["reset", "--", *untracked], workdir)

    return {"stat": stat.strip(), "files": files, "patch": patch}


async def revert(workdir, checkpoint):
    """이 잡이 만든, 아직 커밋되지 않은 변경을 checkpoint 시점으로 되돌린다.

    checkpoint["files"](diff_since 가 남긴 목록) 에 있는 파일만 정확히 건드린다
    — `git checkout … -- .` 나 `git clean -fd` 처럼 워크트리 전체를 겨누는
    명령은 쓰지 않는다. 그런 명령은 (1) before_sha 시점에 파일이 하나도
    없었을 때 pathspec 이 아예 안 맞아 실패하고, (2) 이 잡과 무관한 다른
    미추적 파일까지 지울 위험이 있다(2026-08-01 실측: 빈 초기 커밋을 가진
    저장소에서 `-- .` 이 "pathspec '.' did not match any file(s)" 로 죽었다).

    파일별로 before_sha 시점에 존재했으면 그 내용으로 복원(checkout)하고,
    그때 없었으면(이번 실행이 새로 만든 것) 삭제한다. 그 사이 다른 작업이
    같은 파일을 더 건드렸다면 그것까지 함께 되돌아간다 — "이 잡을 되돌린다"가
    아니라 "이 잡이 건드린 파일들을 그 시점으로 되돌린다"에 가까우므로,
    호출부(UI)가 사용자에게 명시적으로 확인받은 뒤에만 불러야 한다.
    """
    if not checkpoint.get("available"):
        return False, "체크포인트가 없어 되돌릴 수 없습니다"
    before_sha = checkpoint.get("before_sha")
    files = checkpoint.get("files") or []

    rc, cur_status, _ = await _git(["status", "--porcelain"], workdir)
    if rc != 0:
        return False, "저장소 상태를 확인하지 못했습니다"
    if not cur_status.strip():
        return False, "되돌릴 변경이 없습니다"
    if not files:
        # status 는 지저분한데 diff_since 가 남긴 목록이 없다 — 무엇을
        # 되돌려야 할지 알 수 없으니 손대지 않는다(전체 워크트리를 겨누지 않음).
        return False, "되돌릴 대상을 알 수 없습니다"

    existed = set()
    if before_sha:
        rc, tree, _ = await _git(["ls-tree", "-r", "--name-only", before_sha], workdir)
        if rc != 0:
            return False, "이전 상태를 확인하지 못했습니다"
        existed = set(tree.splitlines())

    to_restore = [f for f in files if f in existed]
    to_delete = [f for f in files if f not in existed]

    if to_restore:
        rc, _, err = await _git(["checkout", before_sha, "--", *to_restore], workdir)
        if rc != 0:
            return False, f"되돌리기 실패: {err.strip()[:200]}"
    for f in to_delete:
        (Path(workdir) / f).unlink(missing_ok=True)

    return True, "되돌렸습니다"


def summarize(git_run_json):
    """jobs.git_run(JSON 문자열) 을 파싱한다. 없거나 깨졌으면 None."""
    if not git_run_json:
        return None
    try:
        return json.loads(git_run_json)
    except (json.JSONDecodeError, TypeError):
        return None
