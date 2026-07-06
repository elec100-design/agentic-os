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


def test_build_context_extracts_keywords_from_multi_word_query(tmp_env):
    memory.save_note("코끼리 연구", "claude", "코끼리는 크다", when=datetime(2026, 7, 5))
    ctx = memory.build_context("코끼리 연구에 대해 알려줘? (참고자료+요약)")
    assert "코끼리는 크다" in ctx


# --- 노트 상태 (고정/그룹/보관/이름변경/삭제) ---

def test_save_note_records_session_id(tmp_env):
    path = memory.save_note("q", "claude", "a", when=datetime(2026, 7, 5),
                            session_id="sess-9")
    assert "session_id: sess-9" in path.read_text(encoding="utf-8")


def test_is_managed_only_inside_memory_dir(tmp_env):
    path = memory.save_note("노트", "claude", "x", when=datetime(2026, 7, 5))
    assert memory.is_managed(path)
    assert not memory.is_managed("/etc/hosts")
    assert not memory.is_managed(config.VAULT_PATH / "다른곳.md")


def test_pin_and_flags_persist(tmp_env):
    path = memory.save_note("노트", "claude", "x", when=datetime(2026, 7, 5))
    memory.set_note_flags(path, pinned=True)
    assert memory.note_flags(path)["pinned"] is True
    # recent_notes 에 상태가 실림
    entry = [n for n in memory.recent_notes() if n["path"] == str(path)][0]
    assert entry["pinned"] is True


def test_group_and_browse_grouping(tmp_env):
    a = memory.save_note("가", "claude", "x", when=datetime(2026, 7, 5))
    b = memory.save_note("나", "claude", "y", when=datetime(2026, 7, 5))
    memory.set_note_flags(a, group="연구")
    memory.set_note_flags(b, pinned=True)
    browse = memory.browse_notes()
    assert str(b) in [n["path"] for n in browse["pinned"]]
    assert "연구" in browse["groups"]
    assert "연구" in browse["group_names"]


def test_archive_hidden_from_browse(tmp_env):
    a = memory.save_note("가", "claude", "x", when=datetime(2026, 7, 5))
    memory.set_note_flags(a, archived=True)
    browse = memory.browse_notes()
    all_paths = ([n["path"] for n in browse["ungrouped"]]
                 + [n["path"] for n in browse["pinned"]])
    assert str(a) not in all_paths
    assert str(a) in [n["path"] for n in memory.browse_notes(include_archived=True)["ungrouped"]]


def test_rename_moves_file_and_state(tmp_env):
    a = memory.save_note("옛이름", "claude", "x", when=datetime(2026, 7, 5))
    memory.set_note_flags(a, pinned=True)
    new = memory.rename_note(a, "새이름")
    assert new.exists()
    assert not a.exists()
    assert new.name == "새이름.md"
    assert memory.note_flags(new)["pinned"] is True


def test_delete_removes_file(tmp_env):
    a = memory.save_note("삭제대상", "claude", "x", when=datetime(2026, 7, 5))
    memory.delete_note(a)
    assert not a.exists()


def test_mutations_reject_unmanaged(tmp_env):
    import pytest
    outside = config.VAULT_PATH / "밖.md"
    config.VAULT_PATH.mkdir(parents=True, exist_ok=True)
    outside.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError):
        memory.delete_note(outside)
    with pytest.raises(ValueError):
        memory.set_note_flags(outside, pinned=True)
