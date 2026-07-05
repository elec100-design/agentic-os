from datetime import datetime

from app import config, memory


def test_save_note_creates_file_with_frontmatter(tmp_env):
    path = memory.save_note("경쟁사 분석해줘", "claude", "분석 결과입니다.",
                            when=datetime(2026, 7, 5))
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---")
    assert "provider: claude" in text
    assert "date: 2026-07-05" in text
    assert "분석 결과입니다." in text
    assert path.name.startswith("2026-07-05-")


def test_save_note_dedupes_filename(tmp_env):
    a = memory.save_note("같은 제목", "claude", "1", when=datetime(2026, 7, 5))
    b = memory.save_note("같은 제목", "claude", "2", when=datetime(2026, 7, 5))
    assert a != b
    assert b.exists()


def test_save_note_escapes_quotes_in_summary(tmp_env):
    path = memory.save_note('그는 "안녕"이라 했다', "grok", "out",
                            when=datetime(2026, 7, 5))
    text = path.read_text(encoding="utf-8")
    assert 'prompt: "' in text
    # frontmatter 줄 안에 이중따옴표 중첩이 없어야 함
    prompt_line = [l for l in text.splitlines() if l.startswith("prompt:")][0]
    assert prompt_line.count('"') == 2


def test_recent_notes_returns_newest_first(tmp_env):
    import os, time
    a = memory.save_note("첫번째", "claude", "1", when=datetime(2026, 7, 4))
    time.sleep(0.05)
    b = memory.save_note("두번째", "claude", "2", when=datetime(2026, 7, 5))
    notes = memory.recent_notes(limit=5)
    assert notes[0]["path"] == str(b)
    assert notes[1]["path"] == str(a)


def test_recent_notes_empty_when_no_dir(tmp_env):
    assert memory.recent_notes() == []


def test_build_context_returns_empty_without_matches(tmp_env):
    config.MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    assert memory.build_context("존재하지않는키워드xyz") == ""


def test_build_context_includes_matching_note(tmp_env):
    memory.save_note("코끼리 연구", "claude", "코끼리는 크다", when=datetime(2026, 7, 5))
    ctx = memory.build_context("코끼리")
    assert "코끼리는 크다" in ctx
    assert ctx.endswith("---\n\n")
