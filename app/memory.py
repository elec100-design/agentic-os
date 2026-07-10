import json
import re
import subprocess
from datetime import datetime
from pathlib import Path

from app import config, workspace


# --- 노트 앱 메타데이터 (고정/그룹/보관) --------------------------------
# 볼트 파일을 건드리지 않도록 사이드카 JSON에 저장한다. 키는 노트 절대경로.

def _load_state():
    path = Path(config.NOTE_STATE_PATH)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(state):
    path = Path(config.NOTE_STATE_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def _default_flags():
    return {"pinned": False, "archived": False, "group": None}


def is_managed(path):
    """MEMORY_DIR 안의 노트만 앱이 관리(이름변경/삭제/고정 등)한다."""
    try:
        p = Path(path).resolve()
        return p.is_relative_to(Path(config.MEMORY_DIR).resolve()) and p.suffix == ".md"
    except (OSError, ValueError):
        return False


def note_flags(path):
    flags = _default_flags()
    flags.update(_load_state().get(str(Path(path).resolve()), {}))
    return flags


def set_note_flags(path, **changes):
    if not is_managed(path):
        raise ValueError("managed 노트가 아닙니다")
    state = _load_state()
    key = str(Path(path).resolve())
    entry = {**_default_flags(), **state.get(key, {}), **changes}
    state[key] = entry
    _save_state(state)
    return entry


def rename_note(path, new_name):
    if not is_managed(path):
        raise ValueError("managed 노트가 아닙니다")
    src = Path(path).resolve()
    if not src.is_file():
        raise FileNotFoundError(path)
    safe = re.sub(r"[^\w가-힣 .\-]", "", new_name).strip() or "note"
    if not safe.endswith(".md"):
        safe += ".md"
    dst = src.with_name(safe)
    if dst.exists() and dst != src:
        raise FileExistsError(safe)
    src.rename(dst)
    # 사이드카 상태의 키도 이동
    state = _load_state()
    if str(src) in state:
        state[str(dst)] = state.pop(str(src))
        _save_state(state)
    return dst


def delete_note(path):
    if not is_managed(path):
        raise ValueError("managed 노트가 아닙니다")
    p = Path(path).resolve()
    if p.is_file():
        p.unlink()
    state = _load_state()
    if str(p) in state:
        del state[str(p)]
        _save_state(state)


def all_group_names():
    groups = {e.get("group") for e in _load_state().values() if e.get("group")}
    return sorted(groups)


def _slug(text, maxlen=40):
    s = re.sub(r"[^\w가-힣 -]", "", text).strip()
    s = re.sub(r"\s+", "-", s)
    return s[:maxlen] or "note"


def save_note(prompt, provider, output, when=None, session_id=None, workdir=None):
    memory_dir = Path(config.MEMORY_DIR)
    memory_dir.mkdir(parents=True, exist_ok=True)
    when = when or datetime.now()
    date = when.strftime("%Y-%m-%d")
    base = f"{date}-{_slug(prompt)}"
    path = memory_dir / f"{base}.md"
    n = 1
    while path.exists():
        n += 1
        path = memory_dir / f"{base}-{n}.md"
    summary = prompt.replace("\n", " ").replace('"', "'")[:80]
    session_line = f"session_id: {session_id}\n" if session_id else ""
    ws = workspace.name_for_path(workdir)
    workspace_line = f"workspace: {ws}\n" if ws else ""
    body = (
        f"---\n"
        f"date: {date}\n"
        f"provider: {provider}\n"
        f'prompt: "{summary}"\n'
        f"{session_line}"
        f"{workspace_line}"
        f"tags: [agentic-os]\n"
        f"---\n\n"
        f"## 프롬프트\n\n{prompt}\n\n"
        f"## 결과\n\n{output}\n"
    )
    path.write_text(body, encoding="utf-8")
    if ws:
        # 작업 위치 이름으로 자동 그룹핑 (새 파일이라 수동 그룹과 충돌 없음)
        set_note_flags(path, group=ws, auto_group=True)
    return path


def append_note(path, prompt, provider, output, session_id=None):
    """이어진 세션(resume)의 결과를 기존 노트에 N차 섹션으로 덧붙인다.
    frontmatter의 session_id는 새 값으로 교체(다음 resume에 필요)하고
    updated 날짜를 갱신한다. 고정/그룹/보관 플래그는 건드리지 않는다."""
    if not is_managed(path):
        raise ValueError("managed 노트가 아닙니다")
    p = Path(path).resolve()
    if not p.is_file():
        raise FileNotFoundError(path)
    text = p.read_text(encoding="utf-8", errors="replace")
    today = datetime.now().strftime("%Y-%m-%d")
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            lines = text[4:end].splitlines()
            lines = [l for l in lines
                     if l.partition(":")[0].strip() not in ("session_id", "updated")]
            if session_id:
                lines.append(f"session_id: {session_id}")
            lines.append(f"updated: {today}")
            text = "---\n" + "\n".join(lines) + text[end:]
    n = text.count("## 프롬프트") + 1
    text += f"\n## 프롬프트 ({n}차)\n\n{prompt}\n\n## 결과 ({n}차)\n\n{output}\n"
    p.write_text(text, encoding="utf-8")
    return p


def backfill_auto_groups(rows):
    """기존 잡의 (note_path, workdir)로 그룹 없는 노트를 소급 자동 그룹핑.
    수동 그룹이 있거나 사용자가 그룹을 해제한(auto_group=False) 노트는 스킵."""
    for note_path, workdir in rows:
        if not is_managed(note_path) or not Path(note_path).is_file():
            continue
        flags = note_flags(note_path)
        if flags["group"] is not None or flags.get("auto_group") is False:
            continue
        ws = workspace.name_for_path(workdir)
        if ws:
            set_note_flags(note_path, group=ws, auto_group=True)


def read_note(path):
    """볼트 안의 노트만 읽는다. frontmatter의 provider/session_id를 파싱해
    본문과 함께 돌려주고, 경로가 볼트 밖이거나 없으면 None."""
    p = Path(path).resolve()
    vault = Path(config.VAULT_PATH).resolve()
    if not p.is_relative_to(vault) or p.suffix != ".md" or not p.is_file():
        return None
    text = p.read_text(encoding="utf-8", errors="replace")
    meta = {"name": p.stem, "path": str(p), "provider": None,
            "session_id": None, "body": text}
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            for line in text[4:end].splitlines():
                key, _, value = line.partition(":")
                if key.strip() == "provider":
                    meta["provider"] = value.strip() or None
                elif key.strip() == "session_id":
                    meta["session_id"] = value.strip() or None
            meta["body"] = text[end + 5:].strip()
    return meta


def _note_entry(path):
    p = Path(path)
    entry = {"name": p.stem, "path": str(p), "managed": is_managed(p)}
    if entry["managed"]:
        entry.update(note_flags(p))
    else:
        entry.update(_default_flags())
    return entry


def recent_notes(limit=10):
    d = Path(config.MEMORY_DIR)
    if not d.exists():
        return []
    files = sorted(d.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    return [_note_entry(f) for f in files[:limit]]


def browse_notes(limit=60, include_archived=False):
    """MEMORY_DIR 노트를 상태와 함께 그룹핑해 반환.
    { pinned:[], groups:{name:[]}, ungrouped:[], group_names:[] }"""
    d = Path(config.MEMORY_DIR)
    result = {"pinned": [], "groups": {}, "ungrouped": [],
              "group_names": all_group_names()}
    if not d.exists():
        return result
    files = sorted(d.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    for f in files[:limit]:
        e = _note_entry(f)
        if e["archived"] and not include_archived:
            continue
        if e["pinned"]:
            result["pinned"].append(e)
        elif e["group"]:
            result["groups"].setdefault(e["group"], []).append(e)
        else:
            result["ungrouped"].append(e)
    return result


def search_notes(query, limit=5):
    vault = str(config.VAULT_PATH)
    try:
        out = subprocess.run(
            ["rg", "-il", "-F", "--sortr", "modified", "--glob", "*.md", query, vault],
            capture_output=True, text=True, timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    paths = out.stdout.splitlines()[:limit]
    return [_note_entry(p) for p in paths]


def build_context(query, limit=3):
    # dict.fromkeys dedupes while preserving order (set() iteration order for
    # strings is hash-randomized per process, which would make tie-breaking
    # among same-length tokens nondeterministic)
    tokens = sorted(dict.fromkeys(re.findall(r"[\w가-힣]{3,}", query)),
                     key=len, reverse=True)[:3]
    notes = []
    seen = set()
    for token in tokens:
        for note in search_notes(token, limit=limit):
            if note["path"] not in seen:
                seen.add(note["path"])
                notes.append(note)
    notes = notes[:limit]
    parts = []
    for note in notes:
        try:
            text = Path(note["path"]).read_text(encoding="utf-8")[:2000]
        except OSError:
            continue
        parts.append(f"### {note['name']}\n{text}")
    if not parts:
        return ""
    return "다음은 관련된 과거 메모리입니다:\n\n" + "\n\n".join(parts) + "\n\n---\n\n"
