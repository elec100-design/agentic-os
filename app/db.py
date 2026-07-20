import sqlite3
from datetime import datetime, timezone

from app import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  prompt TEXT NOT NULL,
  provider TEXT NOT NULL,
  model TEXT,
  status TEXT NOT NULL DEFAULT 'queued',
  session_id TEXT,
  output TEXT NOT NULL DEFAULT '',
  error TEXT,
  resume_at TEXT,
  attempts INTEGER NOT NULL DEFAULT 0,
  timeout_sec INTEGER,
  note_path TEXT,
  workdir TEXT,
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
    _migrate(conn)
    return conn


def _migrate(conn):
    """기존 DB에 없는 컬럼을 더한다 (CREATE TABLE IF NOT EXISTS는 새 컬럼을 못 붙임)."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(jobs)")}
    for col in ("model", "note_path", "workdir", "route_reason"):
        if col not in cols:
            conn.execute(f"ALTER TABLE jobs ADD COLUMN {col} TEXT")
    conn.commit()


def create_job(conn, prompt, provider, timeout_sec=None, session_id=None,
               model=None, workdir=None, note_path=None, route_reason=None):
    cur = conn.execute(
        "INSERT INTO jobs (prompt, provider, model, timeout_sec, session_id, "
        "workdir, note_path, route_reason, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (prompt, provider, model, timeout_sec, session_id, workdir, note_path,
         route_reason, now_iso()),
    )
    conn.commit()
    return cur.lastrowid


def delete_job(conn, job_id):
    conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
    conn.commit()


def delete_jobs_by_note(conn, note_path):
    """노트에 연결된 작업 행을 삭제 (메모리↔작업큐 연동)."""
    cur = conn.execute("DELETE FROM jobs WHERE note_path = ?", (note_path,))
    conn.commit()
    return cur.rowcount


def jobs_sharing_note(conn, note_path, exclude_id):
    """같은 노트에 연결된 다른 작업 수 (스레드 노트 보존 판단용)."""
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM jobs WHERE note_path = ? AND id != ?",
        (note_path, exclude_id),
    ).fetchone()
    return row["c"]


def list_note_workdirs(conn):
    """노트가 연결된 잡의 (note_path, workdir) 목록 (소급 그룹핑용)."""
    return conn.execute(
        "SELECT note_path, workdir FROM jobs "
        "WHERE note_path IS NOT NULL AND workdir IS NOT NULL"
    ).fetchall()


def relink_note_path(conn, old_path, new_path):
    conn.execute("UPDATE jobs SET note_path = ? WHERE note_path = ?",
                 (new_path, old_path))
    conn.commit()


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


def claim_next_job(conn, exclude_providers=None):
    """실행 가능한 가장 오래된 잡을 원자적으로 'running'으로 표시하고 반환한다.

    exclude_providers를 주면 그 provider의 잡은 건너뛴다 — 워커가 이미 실행
    중인 provider(같은 CLI 직렬화)나 배타 실행 중인 협의 잡을 제외하는 데 쓴다.
    SELECT~UPDATE 사이에 await가 없어 단일 이벤트 루프에서 원자적이다.
    """
    now = now_iso()
    params = [now]
    exclude_sql = ""
    excl = list(exclude_providers or ())
    if excl:
        placeholders = ", ".join("?" for _ in excl)
        exclude_sql = f" AND provider NOT IN ({placeholders})"
        params.extend(excl)
    row = conn.execute(
        "SELECT * FROM jobs WHERE (status = 'queued' "
        "OR (status = 'rate_limited' AND resume_at <= ?))" + exclude_sql +
        " ORDER BY id LIMIT 1",
        params,
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
    """서버 재시작 시 'running' 상태 잡을 복구한다.
    협의(council) 잡은 재시도가 매우 비싸고(여러 CLI 재실행), 재개 시
    누적 출력이 중복 저장되므로 처음부터 다시 돌리지 않고 실패 처리한다."""
    conn.execute(
        "UPDATE jobs SET status = 'failed', error = 'interrupted: server restarted', "
        "finished_at = ? WHERE status = 'running' AND provider = 'council'",
        (now_iso(),),
    )
    conn.execute(
        "UPDATE jobs SET status = 'queued' WHERE status = 'running'")
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
