"""app/instructions.py — 워크스페이스 지침(CLAUDE.md/AGENTS.md/.agentic-os.md) 주입.

지금까지 어느 지침 파일이 프롬프트에 붙는지는 provider 가 스스로 무엇을
읽느냐에 달려 있었고, 라우팅은 자동이라 사용자는 그 사실조차 몰랐다. claude
가 헤드리스에서도 CLAUDE.md 를 스스로 읽는다는 것은 실측으로 확인했다
(app/instructions.py 상단 주석 — 워터마크 테스트). codex 의 AGENTS.md 자가
읽기는 사용량 한도로 실측하지 못해, self_reads 에 넣지 않고 항상 주입한다.
"""
from app import instructions
from app.providers import ClaudeProvider, CodexProvider


class _NoSelfRead:
    """self_reads 속성이 아예 없는 provider(대부분)를 흉내낸다."""


def _write(tmp_path, name, text):
    (tmp_path / name).write_text(text, encoding="utf-8")


# --- 언제 아무것도 안 붙이는가 --------------------------------------------

def test_no_workdir_means_no_injection():
    prefix, applied = instructions.build_context(None, _NoSelfRead())
    assert prefix == "" and applied == []


def test_no_candidate_files_means_no_injection(tmp_path):
    prefix, applied = instructions.build_context(str(tmp_path), _NoSelfRead())
    assert prefix == "" and applied == []


def test_empty_file_is_skipped(tmp_path):
    _write(tmp_path, "CLAUDE.md", "   \n\n  ")
    prefix, applied = instructions.build_context(str(tmp_path), _NoSelfRead())
    assert prefix == "" and applied == []


# --- 실제로 붙는가 --------------------------------------------------------

def test_injects_all_candidates_for_a_provider_with_no_self_reads(tmp_path):
    _write(tmp_path, "CLAUDE.md", "claude 전용 지침")
    _write(tmp_path, "AGENTS.md", "agents 전용 지침")
    _write(tmp_path, ".agentic-os.md", "agentic-os 공용 지침")
    prefix, applied = instructions.build_context(str(tmp_path), _NoSelfRead())
    assert applied == ["CLAUDE.md", "AGENTS.md", ".agentic-os.md"]
    assert "claude 전용 지침" in prefix
    assert "agents 전용 지침" in prefix
    assert "agentic-os 공용 지침" in prefix


def test_agentic_os_md_is_always_injected_regardless_of_provider(tmp_path):
    """provider-무관 공용 지침 — 어떤 CLI 도 자기 것으로 읽지 않는다."""
    _write(tmp_path, ".agentic-os.md", "항상 전달돼야 함")
    for provider in (ClaudeProvider(), CodexProvider(), _NoSelfRead()):
        prefix, applied = instructions.build_context(str(tmp_path), provider)
        assert applied == [".agentic-os.md"], provider
        assert "항상 전달돼야 함" in prefix


# --- self_reads: claude 만 CLAUDE.md 를 뺀다(실측 확인됨) -------------------

def test_claude_skips_claude_md_but_keeps_others(tmp_path):
    _write(tmp_path, "CLAUDE.md", "claude 가 스스로 읽는다")
    _write(tmp_path, "AGENTS.md", "claude 는 이건 안 읽는다")
    prefix, applied = instructions.build_context(str(tmp_path), ClaudeProvider())
    assert applied == ["AGENTS.md"]
    assert "claude 가 스스로 읽는다" not in prefix
    assert "claude 는 이건 안 읽는다" in prefix


def test_non_claude_provider_still_gets_claude_md(tmp_path):
    """claude 전용 워크스페이스 지침도 다른 에이전트가 걸리면 알아야 한다."""
    _write(tmp_path, "CLAUDE.md", "claude 만을 위한 것처럼 보이지만")
    prefix, applied = instructions.build_context(str(tmp_path), CodexProvider())
    assert applied == ["CLAUDE.md"]
    assert "claude 만을 위한 것처럼 보이지만" in prefix


def test_codex_gets_agents_md_too_pending_verification(tmp_path):
    """codex 의 AGENTS.md 자가 읽기는 미실측 — 빠지는 것보다 중복이 안전하므로
    self_reads 에 넣지 않았다. 이 테스트는 그 설계 선택 자체를 못박는다."""
    assert "AGENTS.md" not in getattr(CodexProvider(), "self_reads", frozenset())
    _write(tmp_path, "AGENTS.md", "codex 용")
    prefix, applied = instructions.build_context(str(tmp_path), CodexProvider())
    assert applied == ["AGENTS.md"]


# --- 안전장치 --------------------------------------------------------------

def test_long_file_is_clipped(tmp_path, monkeypatch):
    monkeypatch.setattr(instructions, "MAX_CHARS_PER_FILE", 50)
    _write(tmp_path, ".agentic-os.md", "x" * 500)
    prefix, applied = instructions.build_context(str(tmp_path), _NoSelfRead())
    assert applied == [".agentic-os.md"]
    assert "…(생략)" in prefix
    assert len(prefix) < 200


def test_prefix_ends_before_the_actual_prompt_with_a_separator(tmp_path):
    """send_prompt = prefix + 원래 프롬프트 로 이어붙이므로, 프롬프트와
    섞이지 않게 구분선으로 끝나야 한다."""
    _write(tmp_path, ".agentic-os.md", "지침")
    prefix, _ = instructions.build_context(str(tmp_path), _NoSelfRead())
    assert prefix.endswith("---\n\n")
