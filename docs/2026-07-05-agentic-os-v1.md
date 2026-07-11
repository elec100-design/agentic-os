# Agentic OS V1 Implementation Plan

> **Note:** V1 당시 문서. 프로덕션은 V2에서 Gemini 대신 **Antigravity CLI(`agy`)** 를 사용합니다. 경로: `docs/2026-07-05-agentic-os-v1-design.md` (구 `docs/superpowers/`).

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Claude/Gemini/SuperGrok/Hermes 유료 구독 CLI를 하나의 로컬 웹 대시보드에서 호출하고, 사용 제한 시 자동 대기·재개하며, 결과를 Obsidian에 저장하는 단일 FastAPI 앱.

**Architecture:** 단일 Python 프로세스. FastAPI가 대시보드(Jinja2+HTMX)를 서빙하고, 같은 프로세스의 asyncio 태스크가 SQLite 작업 큐를 순차 처리한다. 프로바이더별 어댑터가 CLI 명령 조립·출력 파싱·제한 감지를 담당한다.

**Tech Stack:** Python 3.11+, FastAPI, uvicorn, Jinja2, HTMX(vendored), SQLite(WAL), pytest + pytest-asyncio, launchd.

**Spec:** `docs/2026-07-05-agentic-os-v1-design.md`

## Global Constraints

- 각 서비스는 유료 구독 CLI 헤드리스 모드로만 호출: `claude -p`, `gemini -p`, `grok -p`, `hermes -z`. API 키 사용 금지.
- 동시 실행 작업 수는 1개 (의도적 제한).
- 웹 서버는 `127.0.0.1:8899`에만 바인딩.
- Obsidian 저장 경로: `<obsidian-vault>/Agentic OS/`
- 기본 타임아웃 30분, 제한 시 기본 재개 지연 60분, 최대 시도 10회.
- 상태 값은 정확히: `queued | running | rate_limited | done | failed`.
- 외부 CDN 의존 금지 — htmx는 `static/htmx.min.js`로 vendored.
- 프로젝트 루트: `~/agentic-os` (경로에 공백 있음 — 셸 명령에서 반드시 인용).

---

### Task 1: 프로젝트 스캐폴드 + config + DB 계층

**Files:**
- Create: `requirements.txt`, `.gitignore`, `pyproject.toml`
- Create: `app/__init__.py`, `app/config.py`, `app/db.py`
- Create: `tests/__init__.py`, `tests/conftest.py`
- Test: `tests/test_db.py`

**Interfaces:**
- Produces: `config.DB_PATH, VAULT_PATH, MEMORY_DIR, PORT, JOB_TIMEOUT_SEC, DEFAULT_RESUME_DELAY_MIN, MAX_ATTEMPTS, WORKER_POLL_SEC`
- Produces: `db.get_conn(db_path=None)`, `db.now_iso()`, `db.create_job(conn, prompt, provider, timeout_sec=None) -> int`, `db.get_job(conn, job_id)`, `db.list_jobs(conn, limit=50)`, `db.update_job(conn, job_id, **fields)`, `db.append_output(conn, job_id, text)`, `db.claim_next_job(conn)`, `db.recover_running(conn)`, `db.log_usage(conn, provider, duration_sec, outcome, job_id=None)`, `db.usage_counts(conn, since_iso)`, `db.limit_status(conn)`

- [ ] **Step 1: 스캐폴드 파일 생성**

`requirements.txt`:
```
fastapi
uvicorn[standard]
jinja2
python-multipart
pytest
pytest-asyncio
httpx
```

`.gitignore`:
```
.venv/
__pycache__/
data/
*.pyc
```

`pyproject.toml`:
```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

`app/__init__.py`, `tests/__init__.py`: 빈 파일.

`app/config.py`:
```python
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "aos.db"

VAULT_PATH = Path(
    "<obsidian-vault>"
)
MEMORY_DIR = VAULT_PATH / "Agentic OS"

PORT = 8899
JOB_TIMEOUT_SEC = 30 * 60
DEFAULT_RESUME_DELAY_MIN = 60
MAX_ATTEMPTS = 10
WORKER_POLL_SEC = 5
```

`tests/conftest.py`:
```python
import pytest

from app import config


@pytest.fixture
def tmp_env(tmp_path, monkeypatch):
    """테스트마다 DB/볼트를 임시 디렉토리로 격리한다."""
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "VAULT_PATH", tmp_path / "vault")
    monkeypatch.setattr(config, "MEMORY_DIR", tmp_path / "vault" / "Agentic OS")
    return tmp_path
```

- [ ] **Step 2: venv 생성 + 의존성 설치**

```bash
cd "~/agentic-os"
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```
Expected: 에러 없이 설치 완료. 이후 모든 `pytest`/`uvicorn`은 `.venv/bin/` 경로로 실행.

- [ ] **Step 3: 실패하는 테스트 작성** — `tests/test_db.py`

```python
from datetime import datetime, timedelta, timezone

from app import db


def _conn(tmp_env):
    from app import config
    return db.get_conn(config.DB_PATH)


def test_create_and_get_job(tmp_env):
    conn = _conn(tmp_env)
    job_id = db.create_job(conn, "테스트 프롬프트", "claude")
    job = db.get_job(conn, job_id)
    assert job["prompt"] == "테스트 프롬프트"
    assert job["provider"] == "claude"
    assert job["status"] == "queued"
    assert job["attempts"] == 0
    assert job["output"] == ""


def test_create_job_with_timeout(tmp_env):
    conn = _conn(tmp_env)
    job_id = db.create_job(conn, "p", "claude", timeout_sec=120)
    assert db.get_job(conn, job_id)["timeout_sec"] == 120


def test_update_and_append(tmp_env):
    conn = _conn(tmp_env)
    job_id = db.create_job(conn, "p", "gemini")
    db.update_job(conn, job_id, status="running", session_id="s1")
    db.append_output(conn, job_id, "hello ")
    db.append_output(conn, job_id, "world")
    job = db.get_job(conn, job_id)
    assert job["status"] == "running"
    assert job["session_id"] == "s1"
    assert job["output"] == "hello world"


def test_claim_next_job_picks_queued_and_marks_running(tmp_env):
    conn = _conn(tmp_env)
    a = db.create_job(conn, "first", "claude")
    db.create_job(conn, "second", "claude")
    job = db.claim_next_job(conn)
    assert job["id"] == a
    assert job["status"] == "running"
    assert job["attempts"] == 1


def test_claim_next_job_skips_future_rate_limited(tmp_env):
    conn = _conn(tmp_env)
    job_id = db.create_job(conn, "p", "claude")
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(timespec="seconds")
    db.update_job(conn, job_id, status="rate_limited", resume_at=future)
    assert db.claim_next_job(conn) is None


def test_claim_next_job_resumes_past_rate_limited(tmp_env):
    conn = _conn(tmp_env)
    job_id = db.create_job(conn, "p", "claude")
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(timespec="seconds")
    db.update_job(conn, job_id, status="rate_limited", resume_at=past)
    job = db.claim_next_job(conn)
    assert job["id"] == job_id
    assert job["status"] == "running"


def test_recover_running(tmp_env):
    conn = _conn(tmp_env)
    job_id = db.create_job(conn, "p", "claude")
    db.update_job(conn, job_id, status="running")
    db.recover_running(conn)
    assert db.get_job(conn, job_id)["status"] == "queued"


def test_usage_counts_and_limit_status(tmp_env):
    conn = _conn(tmp_env)
    db.log_usage(conn, "claude", 1.5, "ok")
    db.log_usage(conn, "claude", 2.0, "rate_limited")
    db.log_usage(conn, "gemini", 0.5, "ok")
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat(timespec="seconds")
    counts = db.usage_counts(conn, since)
    assert counts["claude"]["ok"] == 1
    assert counts["claude"]["rate_limited"] == 1
    assert counts["gemini"]["ok"] == 1

    job_id = db.create_job(conn, "p", "claude")
    future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(timespec="seconds")
    db.update_job(conn, job_id, status="rate_limited", resume_at=future)
    status = db.limit_status(conn)
    assert status["claude"] == future
    assert "gemini" not in status
```

- [ ] **Step 4: 테스트 실패 확인**

Run: `cd "~/agentic-os" && .venv/bin/pytest tests/test_db.py -v`
Expected: FAIL — `AttributeError: module 'app.db' has no attribute ...` 또는 import 오류.

- [ ] **Step 5: `app/db.py` 구현**

```python
import sqlite3
from datetime import datetime, timezone

from app import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  prompt TEXT NOT NULL,
  provider TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued',
  session_id TEXT,
  output TEXT NOT NULL DEFAULT '',
  error TEXT,
  resume_at TEXT,
  attempts INTEGER NOT NULL DEFAULT 0,
  timeout_sec INTEGER,
  created_at TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT
);
CREATE TABLE IF NOT EXISTS usage_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  provider TEXT NOT NULL,
  ts TEXT NOT NULL,
  duration_sec REAL,
  outcome TEXT NOT NULL,
  job_id INTEGER
);
"""


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_conn(db_path=None):
    path = db_path or config.DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn


def create_job(conn, prompt, provider, timeout_sec=None):
    cur = conn.execute(
        "INSERT INTO jobs (prompt, provider, timeout_sec, created_at) VALUES (?, ?, ?, ?)",
        (prompt, provider, timeout_sec, now_iso()),
    )
    conn.commit()
    return cur.lastrowid


def get_job(conn, job_id):
    return conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()


def list_jobs(conn, limit=50):
    return conn.execute(
        "SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()


def update_job(conn, job_id, **fields):
    cols = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE jobs SET {cols} WHERE id = ?", (*fields.values(), job_id))
    conn.commit()


def append_output(conn, job_id, text):
    conn.execute("UPDATE jobs SET output = output || ? WHERE id = ?", (text, job_id))
    conn.commit()


def claim_next_job(conn):
    now = now_iso()
    row = conn.execute(
        "SELECT * FROM jobs WHERE status = 'queued' "
        "OR (status = 'rate_limited' AND resume_at <= ?) ORDER BY id LIMIT 1",
        (now,),
    ).fetchone()
    if row is None:
        return None
    conn.execute(
        "UPDATE jobs SET status = 'running', started_at = ?, attempts = attempts + 1 "
        "WHERE id = ?",
        (now, row["id"]),
    )
    conn.commit()
    return get_job(conn, row["id"])


def recover_running(conn):
    conn.execute("UPDATE jobs SET status = 'queued' WHERE status = 'running'")
    conn.commit()


def log_usage(conn, provider, duration_sec, outcome, job_id=None):
    conn.execute(
        "INSERT INTO usage_log (provider, ts, duration_sec, outcome, job_id) "
        "VALUES (?, ?, ?, ?, ?)",
        (provider, now_iso(), duration_sec, outcome, job_id),
    )
    conn.commit()


def usage_counts(conn, since_iso):
    rows = conn.execute(
        "SELECT provider, outcome, COUNT(*) AS c FROM usage_log "
        "WHERE ts >= ? GROUP BY provider, outcome",
        (since_iso,),
    ).fetchall()
    result = {}
    for r in rows:
        result.setdefault(r["provider"], {})[r["outcome"]] = r["c"]
    return result


def limit_status(conn):
    rows = conn.execute(
        "SELECT provider, MIN(resume_at) AS resume_at FROM jobs "
        "WHERE status = 'rate_limited' GROUP BY provider"
    ).fetchall()
    return {r["provider"]: r["resume_at"] for r in rows}
```

- [ ] **Step 6: 테스트 통과 확인**

Run: `.venv/bin/pytest tests/test_db.py -v`
Expected: 8 passed.

- [ ] **Step 7: 커밋**

```bash
git add -A && git commit -m "feat: project scaffold, config, SQLite job queue layer"
```

---

### Task 2: 프로바이더 어댑터 + 자동 라우팅

**Files:**
- Create: `app/providers.py`
- Test: `tests/test_providers.py`

**Interfaces:**
- Consumes: `config.DEFAULT_RESUME_DELAY_MIN`
- Produces: `ParseResult(text: str, session_id: str | None)` dataclass
- Produces: 각 프로바이더 클래스의 공통 메서드 —
  `build_command(prompt, session_id=None) -> list[str]`,
  `parse_output(stdout, stderr, exit_code) -> ParseResult`,
  `detect_rate_limit(output, exit_code, now=None) -> datetime | None` (UTC aware; None이면 제한 아님)
- Produces: `PROVIDERS: dict[str, Provider]` (키: `claude|gemini|grok|hermes`), `route_auto(prompt) -> str`, `CONTINUE_PROMPT: str`

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_providers.py`

```python
from datetime import datetime, timedelta, timezone

from app.providers import (
    CONTINUE_PROMPT,
    PROVIDERS,
    ClaudeProvider,
    GeminiProvider,
    GrokProvider,
    HermesProvider,
    route_auto,
)

NOW = datetime(2026, 7, 5, 12, 0, tzinfo=timezone.utc)


# --- Claude ---

def test_claude_build_new():
    cmd = ClaudeProvider().build_command("안녕")
    assert cmd == ["claude", "-p", "--output-format", "json", "안녕"]


def test_claude_build_resume():
    cmd = ClaudeProvider().build_command("안녕", session_id="abc-123")
    assert "--resume" in cmd and "abc-123" in cmd
    assert cmd[-1] == CONTINUE_PROMPT


def test_claude_parse_json_output():
    stdout = '{"result": "답변입니다", "session_id": "sess-9"}'
    r = ClaudeProvider().parse_output(stdout, "", 0)
    assert r.text == "답변입니다"
    assert r.session_id == "sess-9"


def test_claude_parse_non_json_falls_back():
    r = ClaudeProvider().parse_output("plain text", "", 0)
    assert r.text == "plain text"
    assert r.session_id is None


def test_claude_rate_limit_with_epoch():
    out = "Claude AI usage limit reached|1751719800"
    ra = ClaudeProvider().detect_rate_limit(out, 1, now=NOW)
    assert ra == datetime.fromtimestamp(1751719800, tz=timezone.utc)


def test_claude_rate_limit_without_epoch_uses_default():
    ra = ClaudeProvider().detect_rate_limit("usage limit reached", 1, now=NOW)
    assert ra == NOW + timedelta(minutes=60)


def test_claude_no_limit_on_success_exit():
    assert ClaudeProvider().detect_rate_limit("usage limit reached", 0, now=NOW) is None


def test_claude_no_limit_on_normal_error():
    assert ClaudeProvider().detect_rate_limit("some other error", 1, now=NOW) is None


# --- Gemini ---

def test_gemini_build_and_resume():
    p = GeminiProvider()
    assert p.build_command("hi") == ["gemini", "-p", "hi"]
    resumed = p.build_command("hi", session_id="latest")
    assert "--resume" in resumed and "latest" in resumed


def test_gemini_rate_limit():
    p = GeminiProvider()
    assert p.detect_rate_limit("Error 429: RESOURCE_EXHAUSTED", 1, now=NOW) == NOW + timedelta(minutes=60)
    assert p.detect_rate_limit("fine", 1, now=NOW) is None


def test_gemini_parse_records_session():
    assert GeminiProvider().parse_output("out", "", 0).session_id == "latest"


# --- Grok ---

def test_grok_build_and_resume():
    p = GrokProvider()
    assert p.build_command("hi") == ["grok", "-p", "hi"]
    resumed = p.build_command("hi", session_id="latest")
    assert resumed[:2] == ["grok", "-c"]
    assert CONTINUE_PROMPT in resumed


def test_grok_rate_limit():
    p = GrokProvider()
    assert p.detect_rate_limit("Too Many Requests", 1, now=NOW) is not None


# --- Hermes ---

def test_hermes_build_and_never_limits():
    p = HermesProvider()
    assert p.build_command("hi") == ["hermes", "-z", "hi"]
    assert p.detect_rate_limit("rate limit", 1, now=NOW) is None


# --- Registry & routing ---

def test_registry_has_all_four():
    assert set(PROVIDERS) == {"claude", "gemini", "grok", "hermes"}


def test_route_auto():
    assert route_auto("최신 뉴스 검색해줘") == "grok"
    assert route_auto("이 PDF 문서 요약해줘") == "gemini"
    assert route_auto("로컬 파일 정리해줘") == "hermes"
    assert route_auto("버그 수정해줘") == "claude"
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `.venv/bin/pytest tests/test_providers.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.providers'`.

- [ ] **Step 3: `app/providers.py` 구현**

```python
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app import config

CONTINUE_PROMPT = (
    "이전 작업이 사용 제한으로 중단되었습니다. "
    "중단 지점부터 이어서 작업을 끝까지 완료해주세요."
)


@dataclass
class ParseResult:
    text: str
    session_id: str | None = None


def _default_resume_at(now=None):
    now = now or datetime.now(timezone.utc)
    return now + timedelta(minutes=config.DEFAULT_RESUME_DELAY_MIN)


class ClaudeProvider:
    name = "claude"
    _limit_re = re.compile(r"usage limit reached|rate.?limit", re.I)
    _epoch_re = re.compile(r"limit reached\|(\d{9,})")

    def build_command(self, prompt, session_id=None):
        if session_id:
            return [
                "claude", "-p", "--output-format", "json",
                "--resume", session_id, CONTINUE_PROMPT,
            ]
        return ["claude", "-p", "--output-format", "json", prompt]

    def parse_output(self, stdout, stderr, exit_code):
        try:
            data = json.loads(stdout)
            return ParseResult(
                text=data.get("result", stdout), session_id=data.get("session_id")
            )
        except (json.JSONDecodeError, TypeError):
            return ParseResult(text=stdout or stderr)

    def detect_rate_limit(self, output, exit_code, now=None):
        if exit_code == 0 or not self._limit_re.search(output):
            return None
        m = self._epoch_re.search(output)
        if m:
            return datetime.fromtimestamp(int(m.group(1)), tz=timezone.utc)
        return _default_resume_at(now)


class GeminiProvider:
    name = "gemini"
    _limit_re = re.compile(r"\b429\b|RESOURCE_EXHAUSTED|quota", re.I)

    def build_command(self, prompt, session_id=None):
        if session_id:
            return ["gemini", "-p", CONTINUE_PROMPT, "--resume", session_id]
        return ["gemini", "-p", prompt]

    def parse_output(self, stdout, stderr, exit_code):
        return ParseResult(text=stdout or stderr, session_id="latest")

    def detect_rate_limit(self, output, exit_code, now=None):
        if exit_code == 0 or not self._limit_re.search(output):
            return None
        return _default_resume_at(now)


class GrokProvider:
    name = "grok"
    _limit_re = re.compile(r"rate.?limit|\b429\b|too many requests", re.I)

    def build_command(self, prompt, session_id=None):
        if session_id:
            return ["grok", "-c", "-p", CONTINUE_PROMPT]
        return ["grok", "-p", prompt]

    def parse_output(self, stdout, stderr, exit_code):
        return ParseResult(text=stdout or stderr, session_id="latest")

    def detect_rate_limit(self, output, exit_code, now=None):
        if exit_code == 0 or not self._limit_re.search(output):
            return None
        return _default_resume_at(now)


class HermesProvider:
    name = "hermes"

    def build_command(self, prompt, session_id=None):
        return ["hermes", "-z", prompt]

    def parse_output(self, stdout, stderr, exit_code):
        return ParseResult(text=stdout or stderr)

    def detect_rate_limit(self, output, exit_code, now=None):
        return None  # 로컬 실행 — 사용 제한 없음


PROVIDERS = {
    p.name: p
    for p in [ClaudeProvider(), GeminiProvider(), GrokProvider(), HermesProvider()]
}

_GROK_KW = ["검색", "최신", "뉴스", "트렌드", "search", "news", "latest", "trend"]
_GEMINI_KW = ["문서", "요약", "pdf", "번역", "summar", "document", "translate"]
_HERMES_KW = ["로컬", "파일 정리", "개인", "local", "private"]


def route_auto(prompt):
    low = prompt.lower()
    for kw in _GROK_KW:
        if kw in low:
            return "grok"
    for kw in _GEMINI_KW:
        if kw in low:
            return "gemini"
    for kw in _HERMES_KW:
        if kw in low:
            return "hermes"
    return "claude"
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `.venv/bin/pytest tests/test_providers.py -v`
Expected: 16 passed.

- [ ] **Step 5: 실제 CLI 출력으로 제한 패턴 보정 (검증 단계)**

각 CLI를 1회 실행해 정상 출력 형태를 확인하고, 파서가 실제와 맞는지 점검:

```bash
claude -p --output-format json "1+1은?" | head -c 500
gemini -p "1+1은?" | head -c 300
grok -p "1+1은?" | head -c 300
hermes -z "1+1은?" 2>&1 | head -c 300
```
Expected: 각각 응답 텍스트 출력. claude JSON에 `result`, `session_id` 키가 있는지 확인 — 키 이름이 다르면 `parse_output`과 테스트 픽스처를 실제 값으로 수정하고 재실행.

- [ ] **Step 6: 커밋**

```bash
git add -A && git commit -m "feat: provider adapters with rate-limit detection and auto-routing"
```

---

### Task 3: Obsidian 메모리 연동

**Files:**
- Create: `app/memory.py`
- Test: `tests/test_memory.py`

**Interfaces:**
- Consumes: `config.MEMORY_DIR`, `config.VAULT_PATH`
- Produces: `memory.save_note(prompt, provider, output, when=None) -> Path`,
  `memory.recent_notes(limit=10) -> list[dict]` (각 dict: `{"name", "path"}`),
  `memory.search_notes(query, limit=5) -> list[dict]`,
  `memory.build_context(query, limit=3) -> str` (관련 노트 없으면 빈 문자열)

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_memory.py`

```python
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
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `.venv/bin/pytest tests/test_memory.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.memory'`.

- [ ] **Step 3: `app/memory.py` 구현**

```python
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
            ["rg", "-il", "--sort", "modified", "--glob", "*.md", query, vault],
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
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `.venv/bin/pytest tests/test_memory.py -v`
Expected: 7 passed. (`rg`는 시스템에 이미 설치되어 있음 — 없으면 `brew install ripgrep`.)

- [ ] **Step 5: 커밋**

```bash
git add -A && git commit -m "feat: Obsidian memory notes with search and context building"
```

---

### Task 4: 큐 워커 + 자동 재개

**Files:**
- Create: `app/worker.py`
- Test: `tests/test_worker.py`

**Interfaces:**
- Consumes: `db.*` (Task 1), `PROVIDERS`/`ParseResult` (Task 2), `memory.save_note` (Task 3)
- Produces: `worker.run_job(conn, job, providers=None, save=True)` (async),
  `worker.worker_loop(stop_event=None, providers=None, save=True)` (async),
  `worker.current: dict` — `{"job_id": int | None, "proc": Process | None}` (실행 중 작업 취소용)

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_worker.py`

```python
import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app import config, db, worker
from app.providers import ParseResult


class FakeProvider:
    """실제 CLI 대신 셸 명령을 실행하는 테스트용 어댑터."""
    name = "fake"

    def __init__(self, cmd, resume_at=None, session_id="s1"):
        self.cmd = cmd
        self.resume_at = resume_at
        self._session_id = session_id

    def build_command(self, prompt, session_id=None):
        return self.cmd

    def parse_output(self, stdout, stderr, exit_code):
        return ParseResult(text=stdout.strip(), session_id=self._session_id)

    def detect_rate_limit(self, output, exit_code, now=None):
        return self.resume_at


def _setup(tmp_env, provider):
    conn = db.get_conn(config.DB_PATH)
    job_id = db.create_job(conn, "테스트", "fake")
    job = db.claim_next_job(conn)
    return conn, job, {"fake": provider}


async def test_run_job_success(tmp_env):
    p = FakeProvider(["sh", "-c", "echo hello"])
    conn, job, providers = _setup(tmp_env, p)
    await worker.run_job(conn, job, providers=providers, save=False)
    fresh = db.get_job(conn, job["id"])
    assert fresh["status"] == "done"
    assert fresh["output"] == "hello"
    assert fresh["session_id"] == "s1"
    counts = db.usage_counts(conn, "2000-01-01")
    assert counts["fake"]["ok"] == 1


async def test_run_job_failure_preserves_stderr(tmp_env):
    p = FakeProvider(["sh", "-c", "echo boom >&2; exit 3"])
    conn, job, providers = _setup(tmp_env, p)
    await worker.run_job(conn, job, providers=providers, save=False)
    fresh = db.get_job(conn, job["id"])
    assert fresh["status"] == "failed"
    assert "boom" in fresh["error"]
    assert db.usage_counts(conn, "2000-01-01")["fake"]["failed"] == 1


async def test_run_job_rate_limited_stores_resume(tmp_env):
    resume = datetime.now(timezone.utc) + timedelta(hours=1)
    p = FakeProvider(["sh", "-c", "echo limited; exit 1"], resume_at=resume)
    conn, job, providers = _setup(tmp_env, p)
    await worker.run_job(conn, job, providers=providers, save=False)
    fresh = db.get_job(conn, job["id"])
    assert fresh["status"] == "rate_limited"
    assert fresh["resume_at"] == resume.isoformat(timespec="seconds")
    assert fresh["session_id"] == "s1"
    assert db.usage_counts(conn, "2000-01-01")["fake"]["rate_limited"] == 1


async def test_run_job_timeout(tmp_env):
    p = FakeProvider(["sh", "-c", "sleep 30"])
    conn, job, providers = _setup(tmp_env, p)
    db.update_job(conn, job["id"], timeout_sec=1)
    job = db.get_job(conn, job["id"])
    await worker.run_job(conn, job, providers=providers, save=False)
    fresh = db.get_job(conn, job["id"])
    assert fresh["status"] == "failed"
    assert "timeout" in fresh["error"]


async def test_run_job_missing_cli(tmp_env):
    p = FakeProvider(["definitely-not-a-real-command-xyz"])
    conn, job, providers = _setup(tmp_env, p)
    await worker.run_job(conn, job, providers=providers, save=False)
    assert db.get_job(conn, job["id"])["status"] == "failed"


async def test_run_job_cancelled_not_overwritten(tmp_env):
    p = FakeProvider(["sh", "-c", "echo hi"])
    conn, job, providers = _setup(tmp_env, p)
    db.update_job(conn, job["id"], status="failed", error="cancelled")
    await worker.run_job(conn, job, providers=providers, save=False)
    fresh = db.get_job(conn, job["id"])
    assert fresh["status"] == "failed"
    assert fresh["error"] == "cancelled"


async def test_run_job_saves_memory_note(tmp_env):
    p = FakeProvider(["sh", "-c", "echo 결과물"])
    conn, job, providers = _setup(tmp_env, p)
    await worker.run_job(conn, job, providers=providers, save=True)
    notes = list(config.MEMORY_DIR.glob("*.md"))
    assert len(notes) == 1
    assert "결과물" in notes[0].read_text(encoding="utf-8")


async def test_worker_loop_fails_job_over_max_attempts(tmp_env):
    conn = db.get_conn(config.DB_PATH)
    job_id = db.create_job(conn, "p", "fake")
    db.update_job(conn, job_id, attempts=config.MAX_ATTEMPTS)
    stop = asyncio.Event()
    providers = {"fake": FakeProvider(["sh", "-c", "echo hi"])}
    task = asyncio.create_task(worker.worker_loop(stop, providers=providers, save=False))
    for _ in range(100):
        await asyncio.sleep(0.05)
        if db.get_job(conn, job_id)["status"] == "failed":
            break
    stop.set()
    await task
    fresh = db.get_job(conn, job_id)
    assert fresh["status"] == "failed"
    assert "max attempts" in fresh["error"]
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `.venv/bin/pytest tests/test_worker.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.worker'`.

- [ ] **Step 3: `app/worker.py` 구현**

```python
import asyncio
from datetime import datetime, timezone

from app import config, db, memory
from app.providers import PROVIDERS

# 실행 중인 작업 취소를 위해 main.py가 참조하는 공유 상태
current = {"job_id": None, "proc": None}


async def _pump(stream, sink):
    while True:
        line = await stream.readline()
        if not line:
            return
        sink(line.decode("utf-8", errors="replace"))


async def run_job(conn, job, providers=None, save=True):
    providers = providers or PROVIDERS
    provider = providers[job["provider"]]
    timeout = job["timeout_sec"] or config.JOB_TIMEOUT_SEC
    cmd = provider.build_command(job["prompt"], session_id=job["session_id"])
    start = datetime.now(timezone.utc)

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except (FileNotFoundError, OSError) as e:
        db.update_job(conn, job["id"], status="failed", error=str(e),
                      finished_at=db.now_iso())
        db.log_usage(conn, provider.name, 0, "failed", job["id"])
        return

    current["job_id"], current["proc"] = job["id"], proc
    stdout_parts, stderr_parts = [], []

    def on_stdout(text):
        stdout_parts.append(text)
        db.append_output(conn, job["id"], text)

    try:
        await asyncio.wait_for(
            asyncio.gather(
                _pump(proc.stdout, on_stdout),
                _pump(proc.stderr, stderr_parts.append),
                proc.wait(),
            ),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        db.update_job(conn, job["id"], status="failed", error="timeout",
                      finished_at=db.now_iso())
        db.log_usage(conn, provider.name, timeout, "failed", job["id"])
        return
    finally:
        current["job_id"], current["proc"] = None, None

    stdout = "".join(stdout_parts)
    stderr = "".join(stderr_parts)
    duration = (datetime.now(timezone.utc) - start).total_seconds()

    # 사용자가 취소한 작업은 결과를 덮어쓰지 않는다
    fresh = db.get_job(conn, job["id"])
    if fresh["status"] == "failed" and fresh["error"] == "cancelled":
        return

    result = provider.parse_output(stdout, stderr, proc.returncode)
    resume_at = provider.detect_rate_limit(stdout + "\n" + stderr, proc.returncode)

    if resume_at is not None:
        db.update_job(
            conn, job["id"], status="rate_limited",
            resume_at=resume_at.isoformat(timespec="seconds"),
            session_id=result.session_id or job["session_id"],
        )
        db.log_usage(conn, provider.name, duration, "rate_limited", job["id"])
        return

    if proc.returncode != 0:
        db.update_job(conn, job["id"], status="failed",
                      error=(stderr or f"exit {proc.returncode}")[-2000:],
                      finished_at=db.now_iso())
        db.log_usage(conn, provider.name, duration, "failed", job["id"])
        return

    db.update_job(
        conn, job["id"], status="done", output=result.text,
        session_id=result.session_id or job["session_id"],
        finished_at=db.now_iso(),
    )
    db.log_usage(conn, provider.name, duration, "ok", job["id"])

    if save:
        try:
            memory.save_note(job["prompt"], provider.name, result.text)
        except OSError as e:
            db.update_job(conn, job["id"], error=f"memory_save_failed: {e}")


async def worker_loop(stop_event=None, providers=None, save=True):
    conn = db.get_conn()
    db.recover_running(conn)
    while not (stop_event and stop_event.is_set()):
        job = db.claim_next_job(conn)
        if job is None:
            await asyncio.sleep(0.1 if stop_event else config.WORKER_POLL_SEC)
            continue
        if job["attempts"] > config.MAX_ATTEMPTS:
            db.update_job(conn, job["id"], status="failed",
                          error="max attempts exceeded", finished_at=db.now_iso())
            continue
        await run_job(conn, job, providers=providers, save=save)
```

주의: `worker_loop`의 sleep은 `stop_event`가 주어지면(테스트) 0.1초, 아니면 `WORKER_POLL_SEC`. 테스트가 빨리 끝나게 하기 위한 조치다.

- [ ] **Step 4: 테스트 통과 확인**

Run: `.venv/bin/pytest tests/test_worker.py -v`
Expected: 8 passed.

- [ ] **Step 5: 전체 테스트 회귀 확인**

Run: `.venv/bin/pytest -v`
Expected: 전부 passed (누적 31개).

- [ ] **Step 6: 커밋**

```bash
git add -A && git commit -m "feat: queue worker with rate-limit auto-resume and memory save"
```

---

### Task 5: FastAPI 대시보드 + 템플릿

**Files:**
- Create: `app/main.py`
- Create: `templates/index.html`, `templates/job.html`, `templates/partials/jobs.html`, `templates/partials/usage.html`, `templates/partials/memory.html`
- Create: `static/style.css`, `static/htmx.min.js` (다운로드)
- Test: `tests/test_main.py`

**Interfaces:**
- Consumes: Task 1–4의 모든 공개 함수 (`db.*`, `PROVIDERS`, `route_auto`, `memory.*`, `worker.worker_loop`, `worker.current`)
- Produces: `app.main:app` (uvicorn 진입점). 라우트: `GET /`, `POST /jobs`, `GET /jobs/{id}`, `POST /jobs/{id}/cancel`, `GET /jobs/{id}/stream` (SSE), `GET /partials/jobs`, `GET /partials/usage`, `GET /partials/memory?q=`

- [ ] **Step 1: htmx vendoring**

```bash
cd "~/agentic-os"
mkdir -p static templates/partials
curl -fsSL -o static/htmx.min.js https://unpkg.com/htmx.org@1.9.12/dist/htmx.min.js
```
Expected: `static/htmx.min.js` 존재 (~48KB).

- [ ] **Step 2: 실패하는 테스트 작성** — `tests/test_main.py`

```python
from fastapi.testclient import TestClient


def _client(tmp_env):
    from app.main import app
    return TestClient(app)


def test_index_renders(tmp_env):
    with _client(tmp_env) as client:
        r = client.get("/")
        assert r.status_code == 200
        assert "Agentic OS" in r.text


def test_create_job_queues_it(tmp_env):
    from app import config, db
    with _client(tmp_env) as client:
        r = client.post("/jobs", data={"prompt": "버그 수정해줘", "provider": "claude"},
                        follow_redirects=False)
        assert r.status_code == 303
    conn = db.get_conn(config.DB_PATH)
    jobs = db.list_jobs(conn)
    assert jobs[0]["prompt"] == "버그 수정해줘"
    assert jobs[0]["provider"] == "claude"


def test_create_job_auto_routes(tmp_env):
    from app import config, db
    with _client(tmp_env) as client:
        client.post("/jobs", data={"prompt": "최신 뉴스 검색", "provider": "auto"},
                    follow_redirects=False)
    conn = db.get_conn(config.DB_PATH)
    assert db.list_jobs(conn)[0]["provider"] == "grok"


def test_cancel_job(tmp_env):
    from app import config, db
    with _client(tmp_env) as client:
        client.post("/jobs", data={"prompt": "p", "provider": "claude"},
                    follow_redirects=False)
        conn = db.get_conn(config.DB_PATH)
        job_id = db.list_jobs(conn)[0]["id"]
        client.post(f"/jobs/{job_id}/cancel", follow_redirects=False)
        job = db.get_job(conn, job_id)
        assert job["status"] == "failed"
        assert job["error"] == "cancelled"


def test_partials_render(tmp_env):
    with _client(tmp_env) as client:
        assert client.get("/partials/jobs").status_code == 200
        assert client.get("/partials/usage").status_code == 200
        assert client.get("/partials/memory").status_code == 200


def test_job_detail_page(tmp_env):
    from app import config, db
    with _client(tmp_env) as client:
        conn = db.get_conn(config.DB_PATH)
        job_id = db.create_job(conn, "상세 페이지 테스트", "claude")
        r = client.get(f"/jobs/{job_id}")
        assert r.status_code == 200
        assert "상세 페이지 테스트" in r.text
```

주의: TestClient의 lifespan이 워커 루프를 시작하지만 `tmp_env` 픽스처가 DB를 임시 경로로 돌려놓았고 큐가 비어 있으므로 실제 CLI가 실행될 일은 없다. 단, `test_create_job_*`는 워커가 job을 집어 실제 `claude`/`grok`을 실행할 수 있다 — 이를 막기 위해 main.py의 lifespan은 `AOS_DISABLE_WORKER=1` 환경변수를 존중해야 하며, conftest.py에 다음을 추가한다:

`tests/conftest.py`에 추가:
```python
import os
os.environ["AOS_DISABLE_WORKER"] = "1"
```
(파일 상단, import 직후에 한 줄로.)

- [ ] **Step 3: 테스트 실패 확인**

Run: `.venv/bin/pytest tests/test_main.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.main'`.

- [ ] **Step 4: `app/main.py` 구현**

```python
import asyncio
import json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import config, db, memory, worker
from app.providers import route_auto

BASE = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE / "templates"))


@asynccontextmanager
async def lifespan(app):
    stop = asyncio.Event()
    task = None
    if not os.environ.get("AOS_DISABLE_WORKER"):
        task = asyncio.create_task(worker.worker_loop(stop))
    yield
    stop.set()
    if task:
        task.cancel()


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")


def _usage_context(conn):
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat(
        timespec="seconds"
    )
    return {
        "counts": db.usage_counts(conn, since),
        "limits": db.limit_status(conn),
        "providers": ["claude", "gemini", "grok", "hermes"],
    }


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {})


@app.post("/jobs")
def create_job(
    prompt: str = Form(...),
    provider: str = Form("auto"),
    attach_memory: bool = Form(False),
    timeout_min: int | None = Form(None),
):
    if provider == "auto":
        provider = route_auto(prompt)
    if attach_memory:
        prompt = memory.build_context(prompt) + prompt
    conn = db.get_conn()
    db.create_job(conn, prompt, provider,
                  timeout_sec=timeout_min * 60 if timeout_min else None)
    return RedirectResponse("/", status_code=303)


@app.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_detail(request: Request, job_id: int):
    conn = db.get_conn()
    job = db.get_job(conn, job_id)
    return templates.TemplateResponse(request, "job.html", {"job": job})


@app.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: int):
    conn = db.get_conn()
    db.update_job(conn, job_id, status="failed", error="cancelled",
                  finished_at=db.now_iso())
    if worker.current["job_id"] == job_id and worker.current["proc"]:
        worker.current["proc"].terminate()
    return RedirectResponse("/", status_code=303)


@app.get("/jobs/{job_id}/stream")
async def stream_job(job_id: int):
    async def gen():
        sent = 0
        while True:
            conn = db.get_conn()
            job = db.get_job(conn, job_id)
            if job is None:
                break
            out = job["output"]
            if len(out) > sent:
                yield f"data: {json.dumps(out[sent:])}\n\n"
                sent = len(out)
            if job["status"] in ("done", "failed", "rate_limited"):
                yield f"event: status\ndata: {job['status']}\n\n"
                break
            await asyncio.sleep(1)

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/partials/jobs", response_class=HTMLResponse)
def partial_jobs(request: Request):
    conn = db.get_conn()
    return templates.TemplateResponse(
        request, "partials/jobs.html", {"jobs": db.list_jobs(conn)}
    )


@app.get("/partials/usage", response_class=HTMLResponse)
def partial_usage(request: Request):
    conn = db.get_conn()
    return templates.TemplateResponse(
        request, "partials/usage.html", _usage_context(conn)
    )


@app.get("/partials/memory", response_class=HTMLResponse)
def partial_memory(request: Request, q: str = ""):
    notes = memory.search_notes(q) if q else memory.recent_notes()
    return templates.TemplateResponse(
        request, "partials/memory.html", {"notes": notes, "q": q}
    )
```

- [ ] **Step 5: 템플릿 작성**

`templates/index.html`:
```html
<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Agentic OS</title>
<link rel="stylesheet" href="/static/style.css">
<script src="/static/htmx.min.js"></script>
</head>
<body>
<h1>Agentic OS</h1>

<section class="panel dispatch">
  <form method="post" action="/jobs">
    <textarea name="prompt" rows="3" required placeholder="무엇을 할까요?"></textarea>
    <div class="controls">
      <label><input type="radio" name="provider" value="auto" checked> 자동</label>
      <label><input type="radio" name="provider" value="claude"> Claude</label>
      <label><input type="radio" name="provider" value="gemini"> Gemini</label>
      <label><input type="radio" name="provider" value="grok"> Grok</label>
      <label><input type="radio" name="provider" value="hermes"> Hermes</label>
      <label><input type="checkbox" name="attach_memory" value="true"> 관련 메모리 첨부</label>
      <label>타임아웃(분): <input type="number" name="timeout_min" min="1" style="width:4em"></label>
      <button type="submit">전송</button>
    </div>
  </form>
</section>

<section class="panel" id="jobs" hx-get="/partials/jobs" hx-trigger="load, every 3s"></section>

<div class="row">
  <section class="panel" id="usage" hx-get="/partials/usage" hx-trigger="load, every 10s"></section>
  <section class="panel" id="memory" hx-get="/partials/memory" hx-trigger="load"></section>
</div>
</body>
</html>
```

`templates/partials/jobs.html`:
```html
<h2>작업 큐</h2>
<table>
  <tr><th>#</th><th>프롬프트</th><th>모델</th><th>상태</th><th></th></tr>
  {% for j in jobs %}
  <tr class="status-{{ j['status'] }}">
    <td>{{ j['id'] }}</td>
    <td><a href="/jobs/{{ j['id'] }}">{{ j['prompt'][:50] }}</a></td>
    <td>{{ j['provider'] }}</td>
    <td>
      {{ j['status'] }}
      {% if j['status'] == 'rate_limited' %}<br><small>재개: {{ j['resume_at'] }}</small>{% endif %}
    </td>
    <td>
      {% if j['status'] in ('queued', 'running', 'rate_limited') %}
      <form method="post" action="/jobs/{{ j['id'] }}/cancel"><button>취소</button></form>
      {% endif %}
    </td>
  </tr>
  {% endfor %}
</table>
```

`templates/partials/usage.html`:
```html
<h2>사용량 (24시간)</h2>
<table>
  <tr><th>서비스</th><th>성공</th><th>실패</th><th>제한</th><th>상태</th></tr>
  {% for p in providers %}
  <tr>
    <td>{{ p }}</td>
    <td>{{ counts.get(p, {}).get('ok', 0) }}</td>
    <td>{{ counts.get(p, {}).get('failed', 0) }}</td>
    <td>{{ counts.get(p, {}).get('rate_limited', 0) }}</td>
    <td>
      {% if p in limits %}🔴 제한 중 — {{ limits[p] }} 재개
      {% else %}🟢 사용 가능{% endif %}
    </td>
  </tr>
  {% endfor %}
</table>
```

`templates/partials/memory.html`:
```html
<h2>메모리</h2>
<form hx-get="/partials/memory" hx-target="#memory">
  <input type="search" name="q" value="{{ q }}" placeholder="노트 검색…">
</form>
<ul>
  {% for n in notes %}
  <li title="{{ n['path'] }}">{{ n['name'] }}</li>
  {% endfor %}
</ul>
```

`templates/job.html`:
```html
<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<title>작업 #{{ job['id'] }} — Agentic OS</title>
<link rel="stylesheet" href="/static/style.css">
</head>
<body>
<p><a href="/">← 대시보드</a></p>
<h1>작업 #{{ job['id'] }} <small>({{ job['provider'] }} / {{ job['status'] }})</small></h1>
<h2>프롬프트</h2>
<pre>{{ job['prompt'] }}</pre>
{% if job['error'] %}<p class="error">⚠️ {{ job['error'] }}</p>{% endif %}
<h2>출력</h2>
<pre id="out">{% if job['status'] in ('done', 'failed') %}{{ job['output'] }}{% endif %}</pre>
<script>
const status = "{{ job['status'] }}";
if (["queued", "running", "rate_limited"].includes(status)) {
  const es = new EventSource("/jobs/{{ job['id'] }}/stream");
  es.onmessage = (e) => {
    document.getElementById("out").textContent += JSON.parse(e.data);
  };
  es.addEventListener("status", () => { es.close(); location.reload(); });
}
</script>
</body>
</html>
```

`static/style.css`:
```css
body { font-family: -apple-system, sans-serif; max-width: 960px; margin: 2rem auto; padding: 0 1rem; }
.panel { border: 1px solid #ddd; border-radius: 8px; padding: 1rem; margin-bottom: 1rem; }
.row { display: flex; gap: 1rem; }
.row .panel { flex: 1; }
textarea { width: 100%; box-sizing: border-box; }
.controls { display: flex; flex-wrap: wrap; gap: 0.75rem; align-items: center; margin-top: 0.5rem; }
table { width: 100%; border-collapse: collapse; }
td, th { text-align: left; padding: 0.3rem 0.5rem; border-bottom: 1px solid #eee; }
pre { white-space: pre-wrap; background: #f6f6f6; padding: 1rem; border-radius: 6px; }
.status-running { background: #fffbe6; }
.status-rate_limited { background: #fff0f0; }
.error { color: #b00; }
@media (prefers-color-scheme: dark) {
  body { background: #1a1a1a; color: #ddd; }
  .panel { border-color: #333; }
  pre { background: #252525; }
  td, th { border-color: #333; }
  .status-running { background: #332f1a; }
  .status-rate_limited { background: #331f1f; }
}
```

- [ ] **Step 6: 테스트 통과 확인**

Run: `.venv/bin/pytest tests/test_main.py -v`
Expected: 6 passed.

- [ ] **Step 7: 전체 테스트 회귀 확인**

Run: `.venv/bin/pytest -v`
Expected: 전부 passed (누적 37개).

- [ ] **Step 8: 수동 스모크 테스트**

```bash
cd "~/agentic-os"
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8899
```
브라우저에서 `http://localhost:8899` 접속:
- 4개 패널(디스패치/작업 큐/사용량/메모리)이 렌더링되는지
- Hermes(로컬)로 짧은 프롬프트 1건 전송 → 큐에 나타나고 → 완료 후 상태 `done`, 상세 페이지에서 출력 확인
확인 후 Ctrl-C로 종료.

- [ ] **Step 9: 커밋**

```bash
git add -A && git commit -m "feat: FastAPI dashboard with dispatch, queue, usage, memory panels"
```

---

### Task 6: launchd 자동 시작 + E2E 검증

**Files:**
- Create: `launchd/com.agentic-os.dashboard.plist`
- Create: `install.sh`

**Interfaces:**
- Consumes: `app.main:app`, `.venv/bin/uvicorn`
- Produces: 로그인 시 자동 시작되는 launchd 서비스 `com.agentic-os.dashboard`

- [ ] **Step 1: plist 작성** — `launchd/com.agentic-os.dashboard.plist`

주의: `EnvironmentVariables`의 PATH에 4개 CLI 경로가 모두 포함되어야 launchd 환경에서 서브프로세스 실행이 가능하다 (`claude`/`hermes`: `~/.local/bin`, `grok`: `~/.grok/bin`, `gemini`/`rg`: `/opt/homebrew/bin`).

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.agentic-os.dashboard</string>
  <key>ProgramArguments</key>
  <array>
    <string>~/agentic-os/.venv/bin/uvicorn</string>
    <string>app.main:app</string>
    <string>--host</string>
    <string>127.0.0.1</string>
    <string>--port</string>
    <string>8899</string>
  </array>
  <key>WorkingDirectory</key>
  <string>~/agentic-os</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>~/agentic-os/data/aos.log</string>
  <key>StandardErrorPath</key>
  <string>~/agentic-os/data/aos.err.log</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>~/.local/bin:~/.grok/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    <key>HOME</key>
    <string>~</string>
  </dict>
</dict>
</plist>
```

- [ ] **Step 2: `install.sh` 작성**

```bash
#!/bin/sh
# Agentic OS launchd 서비스 설치/재설치
set -e
PLIST_SRC="$(cd "$(dirname "$0")" && pwd)/launchd/com.agentic-os.dashboard.plist"
PLIST_DST="$HOME/Library/LaunchAgents/com.agentic-os.dashboard.plist"
mkdir -p "$HOME/Library/LaunchAgents" "$(dirname "$PLIST_SRC")/../data"
launchctl unload "$PLIST_DST" 2>/dev/null || true
cp "$PLIST_SRC" "$PLIST_DST"
launchctl load "$PLIST_DST"
echo "설치 완료 — http://localhost:8899"
```

```bash
chmod +x install.sh
```

- [ ] **Step 3: 서비스 설치 및 기동 확인**

```bash
cd "~/agentic-os" && ./install.sh
sleep 3
curl -s -o /dev/null -w "%{http_code}" http://localhost:8899/
```
Expected: `200`.

- [ ] **Step 4: E2E 검증 — 4개 CLI 실제 호출**

브라우저(`http://localhost:8899`)에서 아래 4건을 순서대로 전송하고 각각 `done` 상태와 출력, Obsidian 노트 생성을 확인:

1. 모델 **Claude** 선택: "1+1은? 한 단어로만 답해."
2. 모델 **Gemini** 선택: "2+2는? 한 단어로만 답해."
3. 모델 **Grok** 선택: "3+3은? 한 단어로만 답해."
4. 모델 **Hermes** 선택: "4+4는? 한 단어로만 답해."

확인:
```bash
ls "<obsidian-vault>/Agentic OS/"
```
Expected: 노트 4개 생성. 사용량 패널에 서비스별 성공 1씩 표시.

- [ ] **Step 5: E2E 검증 — 제한 재개 시뮬레이션**

가짜 rate_limited 작업을 주입해 자동 재개를 검증:

```bash
cd "~/agentic-os"
.venv/bin/python - <<'EOF'
from datetime import datetime, timedelta, timezone
from app import db
conn = db.get_conn()
job_id = db.create_job(conn, "5+5는? 한 단어로만 답해.", "hermes")
past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(timespec="seconds")
db.update_job(conn, job_id, status="rate_limited", resume_at=past)
print("injected job", job_id)
EOF
sleep 15
```
대시보드에서 해당 작업이 자동으로 `running` → `done`으로 진행되는지 확인.
Expected: 워커가 `resume_at`이 지난 작업을 집어 자동 실행 완료.

- [ ] **Step 6: 재시작 복구 검증**

```bash
launchctl kickstart -k gui/$(id -u)/com.agentic-os.dashboard
sleep 3
curl -s -o /dev/null -w "%{http_code}" http://localhost:8899/
```
Expected: `200` — 강제 재시작 후에도 대시보드 및 큐 데이터(SQLite) 유지.

- [ ] **Step 7: 커밋**

```bash
git add -A && git commit -m "feat: launchd service with install script"
```

---

## Self-Review 결과 (계획 작성 시 수행)

- **Spec coverage:** 어댑터 4종+제한 감지(Task 2), 큐+자동 재개+복구(Task 4, 1), 대시보드 4패널+SSE+취소(Task 5), Obsidian 저장/검색/컨텍스트 첨부(Task 3, 5), launchd(Task 6), WAL(Task 1), 타임아웃 작업별 조정(Task 1, 4, 5) — 모두 매핑됨.
- **Placeholder scan:** 없음. 모든 코드 블록 완전 기재.
- **Type consistency:** `detect_rate_limit(output, exit_code, now=None)` 시그니처가 providers/worker/테스트에서 일치. `ParseResult`, `claim_next_job`, `current` dict 사용처 일치 확인.
