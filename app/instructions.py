"""워크스페이스 프로젝트 지침을 프롬프트 앞에 붙인다.

agentic-os 는 여러 CLI 를 같은 워크스페이스에서 자동 라우팅으로 돌린다. 그런데
CLAUDE.md 는 claude 만, AGENTS.md 는 (아마) codex 만 스스로 읽는다 — 그래서
워크스페이스에 CLAUDE.md 만 있으면 그날 codex 가 걸렸을 때는 지침이 통째로
사라진다. 라우팅이 자동이라 사용자는 그 사실조차 모른다.

CANDIDATE_FILES 를 찾아 provider 가 스스로 읽지 않는 파일만 프롬프트에
직접 붙인다. `.agentic-os.md` 는 어떤 CLI 도 자기 것으로 읽지 않는, 이
앱 전용의 provider-무관 지침 파일이다 — 라우팅에 상관없이 항상 전달된다.

self_reads 검증 상태(2026-08-01):
  - claude: 헤드리스(-p)에서도 CLAUDE.md 를 스스로 읽는 것을 실측 확인했다
    (워터마크 테스트 — CLAUDE.md 에 이례적인 지시를 심고 claude -p 로 무관한
    질문을 던지자, 그 지시를 인지하고 있다고 답했다). 그래서 claude 에게는
    CLAUDE.md 를 다시 붙이지 않는다.
  - codex: AGENTS.md 를 스스로 읽는지 같은 방식으로 검증하려 했으나 codex 가
    사용량 한도(2026-08-25 해제)에 걸려 실측하지 못했다. 확인 전까지는
    codex 도 AGENTS.md 를 injected 대상에 포함한다(중복될 수 있지만 빠지는
    것보다 안전하다) — self_reads 에 넣지 않는다.
"""
from __future__ import annotations

from pathlib import Path

CANDIDATE_FILES = ("CLAUDE.md", "AGENTS.md", ".agentic-os.md")
MAX_CHARS_PER_FILE = 4000


def build_context(workdir, provider):
    """워크스페이스 지침을 읽어 (프롬프트 앞에 붙일 텍스트, 적용된 파일명 목록)을 돌려준다.

    적용된 게 없으면 ("", [])."""
    if not workdir:
        return "", []
    self_reads = getattr(provider, "self_reads", frozenset())

    blocks, applied = [], []
    for name in CANDIDATE_FILES:
        if name in self_reads:
            continue
        path = Path(workdir) / name
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            continue
        if not text:
            continue
        if len(text) > MAX_CHARS_PER_FILE:
            text = text[:MAX_CHARS_PER_FILE] + "\n…(생략)"
        blocks.append(f"### {name}\n{text}")
        applied.append(name)

    if not blocks:
        return "", []
    prefix = ("다음은 이 작업 위치(workspace)의 프로젝트 지침입니다. 반드시 따르세요:\n\n"
              + "\n\n".join(blocks) + "\n\n---\n\n")
    return prefix, applied
