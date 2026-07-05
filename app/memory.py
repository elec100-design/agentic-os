import re
import subprocess
from datetime import datetime
from pathlib import Path

from app import config


def _slug(text, maxlen=40):
    s = re.sub(r"[^\w가-힣 -]", "", text).strip()
    s = re.sub(r"\s+", "-", s)
    return s[:maxlen] or "note"


def save_note(prompt, provider, output, when=None):
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
    body = (
        f"---\n"
        f"date: {date}\n"
        f"provider: {provider}\n"
        f'prompt: "{summary}"\n'
        f"tags: [agentic-os]\n"
        f"---\n\n"
        f"## 프롬프트\n\n{prompt}\n\n"
        f"## 결과\n\n{output}\n"
    )
    path.write_text(body, encoding="utf-8")
    return path


def recent_notes(limit=10):
    d = Path(config.MEMORY_DIR)
    if not d.exists():
        return []
    files = sorted(d.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    return [{"name": f.stem, "path": str(f)} for f in files[:limit]]


def search_notes(query, limit=5):
    vault = str(config.VAULT_PATH)
    try:
        out = subprocess.run(
            ["rg", "-il", "--sortr", "modified", "--glob", "*.md", query, vault],
            capture_output=True, text=True, timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    paths = out.stdout.splitlines()[:limit]
    return [{"name": Path(p).stem, "path": p} for p in paths]


def build_context(query, limit=3):
    parts = []
    for note in search_notes(query, limit=limit):
        try:
            text = Path(note["path"]).read_text(encoding="utf-8")[:2000]
        except OSError:
            continue
        parts.append(f"### {note['name']}\n{text}")
    if not parts:
        return ""
    return "다음은 관련된 과거 메모리입니다:\n\n" + "\n\n".join(parts) + "\n\n---\n\n"
